"""Tests for the LLM adapters, call records and replay (DESIGN.md §9.6, §11.9)."""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from oris_matcher.llm.anthropic_llm import AnthropicLLM
from oris_matcher.llm.base import (
    REPLAY_MISS_ERROR_CLASS,
    AdapterOptions,
    LLMRequest,
    LLMResult,
    LLMStatus,
    ModelNotAllowedError,
    SystemBlock,
    Usage,
    classify_content,
    parse_retry_after,
    parse_should_retry,
)
from oris_matcher.llm.fake_llm import (
    FakeBehaviour,
    FakeLLM,
    Fault,
    FaultKind,
    default_answer,
)
from oris_matcher.llm.openai_llm import OpenAIChatLLM
from oris_matcher.llm.recording import (
    CALL_RECORD_KEYS,
    CallRecord,
    JsonlCallSink,
    MemoryCallSink,
    ReasonForCall,
    attribute_costs,
    read_calls_jsonl,
    system_blocks_sha256,
)
from oris_matcher.llm.replay_llm import RecordedRun, ReplayLLM, ReplayMissError, ResponseCache
from oris_matcher.prompts.v1.schema import BatchAnswer, output_json_schema
from oris_matcher.settings import DEFAULT_PER_CALL_TIMEOUT_S, load_models_config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "llm"
ALLOWLIST = load_models_config(ROOT / "config" / "models.toml").allowlist.patterns
HAIKU = "claude-haiku-4-5-20251001"
GPT = "gpt-4o-mini-2024-07-18"


def make_request(
    line_ids: tuple[str, ...] = ("L1", "L2"), model: str = HAIKU, provider: str = "anthropic"
) -> LLMRequest:
    """Build a request for the given line ids."""
    payload = json.dumps({"lines": [{"id": i} for i in line_ids]}, sort_keys=True)
    return LLMRequest(
        provider=provider,
        model=model,
        system_blocks=(SystemBlock("vocabulary", cache=False), SystemBlock("library", cache=True)),
        schema_json=json.dumps(output_json_schema()),
        user_payload=f"<boq_lines>{payload}</boq_lines>",
        max_tokens=800,
        temperature=0.0,
        line_ids=line_ids,
    )


def fixture_json(name: str) -> dict[str, Any]:
    """Load a JSON fixture from tests/fixtures/llm."""
    with open(FIXTURES / name, encoding="utf-8") as handle:
        loaded: dict[str, Any] = json.load(handle)
    return loaded


class Recorder:
    """A mock-transport handler that records requests and serves responses."""

    def __init__(self, responses: list[httpx2.Response | Exception]) -> None:
        """Keep the responses to serve, in order."""
        self.responses = responses
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Record the request and serve the next response, repeating the last."""
        self.requests.append(request)
        response = self.responses[min(len(self.requests), len(self.responses)) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, index: int = 0) -> dict[str, Any]:
        """Return the JSON body of a recorded request."""
        loaded: dict[str, Any] = json.loads(self.requests[index].content)
        return loaded


def options(recorder: Recorder, **overrides: Any) -> AdapterOptions:
    """Build adapter options over a mock transport that records requests."""
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
    return AdapterOptions(http_client=client, **overrides)


def anthropic_llm(recorder: Recorder, **overrides: Any) -> AnthropicLLM:
    """Build the Anthropic adapter over a recording mock transport."""
    return AnthropicLLM(HAIKU, ALLOWLIST, "test-key", options(recorder, **overrides))


def openai_llm(recorder: Recorder, **overrides: Any) -> OpenAIChatLLM:
    """Build the OpenAI adapter over a recording mock transport."""
    return OpenAIChatLLM(GPT, ALLOWLIST, "test-key", options(recorder, **overrides))


def error_body(kind: str) -> dict[str, Any]:
    """Build a provider error body of the given type."""
    return {"type": "error", "error": {"type": kind, "message": "nope"}}


def anthropic_message(stop_reason: str, text: str) -> dict[str, Any]:
    """Build an Anthropic message body with the given stop reason and text."""
    body = fixture_json("anthropic_message_ok.json")
    body["stop_reason"] = stop_reason
    body["content"] = [{"type": "text", "text": text}]
    return body


# ---------------------------------------------------------------- shared helpers in base


def test_parse_retry_after_reads_seconds_and_milliseconds() -> None:
    """Parse retry after reads seconds and milliseconds."""
    assert parse_retry_after({"retry-after": "20"}) == 20.0
    assert parse_retry_after({"Retry-After-Ms": "1500"}) == 1.5
    assert parse_retry_after({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}) is None
    assert parse_retry_after({}) is None


def test_parse_should_retry_reads_the_hint() -> None:
    """Parse should retry reads the hint."""
    assert parse_should_retry({"x-should-retry": "true"}) is True
    assert parse_should_retry({"X-Should-Retry": "false"}) is False
    assert parse_should_retry({"x-should-retry": "maybe"}) is None
    assert parse_should_retry({}) is None


@pytest.mark.parametrize(
    ("reasons", "text", "status"),
    [
        (("end_turn",), '{"lines": []}', LLMStatus.OK),
        (("end_turn",), '{"lines": [', LLMStatus.MALFORMED),
        (("end_turn",), "[1, 2]", LLMStatus.MALFORMED),
        (("max_tokens",), '{"lines": [', LLMStatus.TRUNCATED),
        (("length",), '{"lines": [', LLMStatus.TRUNCATED),
        (("refusal",), "I cannot", LLMStatus.REFUSAL),
        (("stop", "refusal"), "I cannot", LLMStatus.REFUSAL),
        (("content_filter",), "", LLMStatus.REFUSAL),
    ],
)
def test_classify_content(reasons: tuple[str, ...], text: str, status: LLMStatus) -> None:
    """Classify content."""
    got_status, parsed = classify_content(reasons, text)
    assert got_status == status
    assert (parsed is not None) == (status == LLMStatus.OK)


# ---------------------------------------------------------------- allowlist


@pytest.mark.parametrize("model", ["claude-opus-4-1", "gpt-4o", "local:llama3.1:70b", ""])
def test_every_adapter_refuses_a_model_outside_the_allowlist(model: str) -> None:
    """Every adapter refuses a model outside the allowlist."""
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(Recorder([])))
    options = AdapterOptions(http_client=client)
    with pytest.raises(ModelNotAllowedError):
        AnthropicLLM(model, ALLOWLIST, "k", options)
    with pytest.raises(ModelNotAllowedError):
        OpenAIChatLLM(model, ALLOWLIST, "k", options)
    with pytest.raises(ModelNotAllowedError):
        FakeLLM(model, ALLOWLIST)
    with pytest.raises(ModelNotAllowedError):
        ReplayLLM(RecordedRun(), model, ALLOWLIST)


async def test_adapter_refuses_a_request_for_a_model_outside_the_allowlist() -> None:
    """Adapter refuses a request for a model outside the allowlist."""
    fake = FakeLLM(HAIKU, ALLOWLIST)
    with pytest.raises(ModelNotAllowedError):
        await fake.complete(make_request(model="claude-opus-4-1"))


# ---------------------------------------------------------------- FakeLLM


async def test_fake_default_answer_is_a_valid_batch_answer() -> None:
    """Fake default answer is a valid batch answer."""
    result = await FakeLLM(HAIKU, ALLOWLIST).complete(make_request(("L1", "L2", "L3")))
    assert result.status == LLMStatus.OK
    batch = BatchAnswer.model_validate_json(result.raw_text)
    assert [line.id for line in batch.lines] == ["L1", "L2", "L3"]
    assert result.parsed is not None
    assert result.http_status == 200
    assert result.finish_reasons == ("end_turn",)
    assert result.served_model == HAIKU


async def test_fake_uses_canned_answers() -> None:
    """Fake uses canned answers."""
    answers = {"L1": {**default_answer("L1"), "evidence": "concrete C30/37"}}
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=answers))
    batch = BatchAnswer.model_validate_json((await fake.complete(make_request())).raw_text)
    assert batch.lines[0].evidence == "concrete C30/37"
    assert batch.lines[1] == BatchAnswer.model_validate({"lines": [default_answer("L2")]}).lines[0]


async def test_fake_is_deterministic_and_records_calls() -> None:
    """Fake is deterministic and records calls."""
    script = (Fault(FaultKind.RATE_LIMITED, retry_after_s=3.0), None, Fault(FaultKind.TRUNCATED))

    async def run() -> list[Any]:
        """Run the scripted fake four times."""
        fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(script=script))
        results = [await fake.complete(make_request()) for _ in range(4)]
        assert len(fake.calls) == 4
        return results

    first, second = await run(), await run()
    assert first == second
    assert [r.status for r in first] == [
        LLMStatus.RATE_LIMITED,
        LLMStatus.OK,
        LLMStatus.TRUNCATED,
        LLMStatus.OK,
    ]


async def test_fake_rule_applies_after_the_script() -> None:
    """Fake rule applies after the script."""

    def rule(call_no: int, req: LLMRequest) -> Fault | None:
        """Choose the fault for one call."""
        return Fault(FaultKind.TIMEOUT) if len(req.line_ids) > 1 else None

    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(script=(None,), rule=rule))
    assert (await fake.complete(make_request())).status == LLMStatus.OK
    assert (await fake.complete(make_request())).status == LLMStatus.TIMEOUT
    assert (await fake.complete(make_request(("L1",)))).status == LLMStatus.OK


@pytest.mark.parametrize(
    ("fault", "status", "http_status"),
    [
        (Fault(FaultKind.TIMEOUT), LLMStatus.TIMEOUT, None),
        (Fault(FaultKind.CONNECTION_ERROR), LLMStatus.API_ERROR, None),
        (Fault(FaultKind.RATE_LIMITED, retry_after_s=20.0), LLMStatus.RATE_LIMITED, 429),
        (Fault(FaultKind.OVERLOADED), LLMStatus.OVERLOADED, 529),
        (Fault(FaultKind.SERVER_ERROR, http_status=503), LLMStatus.API_ERROR, 503),
        (Fault(FaultKind.SERVER_ERROR), LLMStatus.API_ERROR, 500),
        (Fault(FaultKind.MALFORMED), LLMStatus.MALFORMED, 200),
        (Fault(FaultKind.INVALID_SCHEMA), LLMStatus.OK, 200),
        (Fault(FaultKind.TRUNCATED), LLMStatus.TRUNCATED, 200),
        (Fault(FaultKind.REFUSAL), LLMStatus.REFUSAL, 200),
    ],
)
async def test_fake_faults_map_to_statuses(
    fault: Fault, status: LLMStatus, http_status: int | None
) -> None:
    """Fake faults map to statuses."""
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(script=(fault,)))
    result = await fake.complete(make_request())
    assert result.status == status
    assert result.http_status == http_status
    assert isinstance(result.raw_text, str)
    if fault.kind == FaultKind.RATE_LIMITED:
        assert result.retry_after_s == 20.0


async def test_fake_id_faults_shape_the_lines() -> None:
    """Fake id faults shape the lines."""
    ids = ("L1", "L2", "L3")
    script = (
        Fault(FaultKind.DROP_IDS, ids=("L2",)),
        Fault(FaultKind.DUPLICATE_IDS, ids=("L1",)),
        Fault(FaultKind.CONFLICTING_DUPLICATES, ids=("L1",)),
        Fault(FaultKind.UNKNOWN_IDS, ids=("L9",)),
        Fault(FaultKind.SWAP_IDS),
    )
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(script=script))
    batches = [json.loads((await fake.complete(make_request(ids))).raw_text) for _ in script]
    line_ids = [[line["id"] for line in batch["lines"]] for batch in batches]
    assert line_ids[0] == ["L1", "L3"]
    assert line_ids[1] == ["L1", "L1", "L2", "L3"]
    assert batches[1]["lines"][0] == batches[1]["lines"][1]
    assert line_ids[2] == ["L1", "L1", "L2", "L3"]
    assert batches[2]["lines"][0] != batches[2]["lines"][1]
    assert line_ids[3] == ["L1", "L2", "L3", "L9"]
    assert line_ids[4] == ["L2", "L3", "L1"]


# ---------------------------------------------------------------- AnthropicLLM


async def test_anthropic_sends_cached_system_blocks_and_native_structured_output() -> None:
    """Anthropic sends cached system blocks and native structured output."""
    recorder = Recorder([httpx2.Response(200, json=fixture_json("anthropic_message_ok.json"))])
    req = make_request()
    await anthropic_llm(recorder).complete(req)
    body = recorder.body()
    assert recorder.requests[0].url.path == "/v1/messages"
    assert body["model"] == HAIKU
    assert body["max_tokens"] == 800
    assert body["temperature"] == 0.0
    assert body["system"] == [
        {"type": "text", "text": "vocabulary"},
        {"type": "text", "text": "library", "cache_control": {"type": "ephemeral"}},
    ]
    assert body["messages"] == [{"role": "user", "content": req.user_payload}]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": req.schema()}}
    assert recorder.requests[0].headers["x-stainless-retry-count"] == "0"


async def test_anthropic_ok_captures_provider_metadata() -> None:
    """Anthropic ok captures provider metadata."""
    headers = {
        "request-id": "req_011",
        "anthropic-ratelimit-requests-remaining": "49",
        "anthropic-ratelimit-tokens-limit": "50000",
        "x-unrelated": "1",
    }
    body = fixture_json("anthropic_message_ok.json")
    recorder = Recorder([httpx2.Response(200, json=body, headers=headers)])
    result = await anthropic_llm(recorder).complete(make_request())
    assert result.status == LLMStatus.OK
    assert result.raw_text == body["content"][0]["text"]
    assert result.parsed is not None
    assert result.served_model == HAIKU
    assert result.response_id == "msg_01ABC"
    assert result.provider_request_id == "req_011"
    assert result.finish_reasons == ("end_turn",)
    assert result.http_status == 200
    assert result.usage == Usage(input_tokens=120, output_tokens=80, cache_read=3000, cache_write=0)
    assert result.rate_limit_headers == (
        ("anthropic-ratelimit-requests-remaining", "49"),
        ("anthropic-ratelimit-tokens-limit", "50000"),
    )
    assert result.latency_ms >= 0


async def test_anthropic_429_is_reported_once_with_retry_after() -> None:
    """Anthropic 429 is reported once with retry after."""
    headers = {"retry-after": "7", "x-should-retry": "true", "request-id": "req_429"}
    recorder = Recorder(
        [httpx2.Response(429, json=error_body("rate_limit_error"), headers=headers)]
    )
    result = await anthropic_llm(recorder).complete(make_request())
    assert len(recorder.requests) == 1
    assert result.status == LLMStatus.RATE_LIMITED
    assert result.retry_after_s == 7.0
    assert result.should_retry is True
    assert result.http_status == 429
    assert result.provider_request_id == "req_429"
    assert result.error_class == "RateLimitError"
    assert "rate_limit_error" in result.raw_text


@pytest.mark.parametrize(
    ("http_status", "status"),
    [(529, LLMStatus.OVERLOADED), (500, LLMStatus.API_ERROR), (400, LLMStatus.API_ERROR)],
)
async def test_anthropic_error_statuses(http_status: int, status: LLMStatus) -> None:
    """Anthropic error statuses."""
    recorder = Recorder([httpx2.Response(http_status, json=error_body("overloaded_error"))])
    result = await anthropic_llm(recorder).complete(make_request())
    assert len(recorder.requests) == 1
    assert result.status == status
    assert result.http_status == http_status
    assert result.retry_after_s is None


async def test_anthropic_timeout_and_connection_errors_never_raise() -> None:
    """Anthropic timeout and connection errors never raise."""
    timeout = Recorder([httpx2.ReadTimeout("slow")])
    result = await anthropic_llm(timeout).complete(make_request())
    assert result.status == LLMStatus.TIMEOUT
    assert result.http_status is None
    assert result.error_class == "APITimeoutError"
    refused = Recorder([httpx2.ConnectError("refused")])
    result = await anthropic_llm(refused).complete(make_request())
    assert result.status == LLMStatus.API_ERROR
    assert result.error_class == "APIConnectionError"


@pytest.mark.parametrize(
    ("stop_reason", "text", "status"),
    [
        ("max_tokens", '{"lines": [{"id": "L1"', LLMStatus.TRUNCATED),
        ("refusal", "", LLMStatus.REFUSAL),
        ("end_turn", '{"lines": [', LLMStatus.MALFORMED),
    ],
)
async def test_anthropic_stop_reasons_keep_raw_text(
    stop_reason: str, text: str, status: LLMStatus
) -> None:
    """Anthropic stop reasons keep raw text."""
    body = anthropic_message(stop_reason, text)
    result = await anthropic_llm(Recorder([httpx2.Response(200, json=body)])).complete(
        make_request()
    )
    assert result.status == status
    assert result.raw_text == text
    assert result.finish_reasons == (stop_reason,)
    assert result.parsed is None


async def test_anthropic_unparsable_200_body_is_malformed_not_raised() -> None:
    """Anthropic unparsable 200 body is malformed not raised."""
    recorder = Recorder([httpx2.Response(200, text="<html>gateway</html>")])
    result = await anthropic_llm(recorder).complete(make_request())
    assert result.status == LLMStatus.MALFORMED
    assert result.raw_text == "<html>gateway</html>"


# ---------------------------------------------------------------- OpenAIChatLLM


async def test_openai_sends_strict_json_schema() -> None:
    """Openai sends strict json schema."""
    recorder = Recorder([httpx2.Response(200, json=fixture_json("openai_completion_ok.json"))])
    req = make_request(model=GPT, provider="openai")
    await openai_llm(recorder).complete(req)
    body = recorder.body()
    assert recorder.requests[0].url.path == "/v1/chat/completions"
    assert body["model"] == GPT
    assert body["max_completion_tokens"] == 800
    assert body["temperature"] == 0.0
    assert body["messages"] == [
        {"role": "system", "content": "vocabulary"},
        {"role": "system", "content": "library"},
        {"role": "user", "content": req.user_payload},
    ]
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == req.schema()
    assert recorder.requests[0].headers["x-stainless-retry-count"] == "0"


async def test_openai_ok_maps_usage_and_metadata() -> None:
    """Openai ok maps usage and metadata."""
    headers = {"x-request-id": "oa_req_1", "x-ratelimit-remaining-requests": "99"}
    body = fixture_json("openai_completion_ok.json")
    recorder = Recorder([httpx2.Response(200, json=body, headers=headers)])
    result = await openai_llm(recorder).complete(make_request(model=GPT, provider="openai"))
    assert result.status == LLMStatus.OK
    assert result.raw_text == body["choices"][0]["message"]["content"]
    assert result.usage == Usage(input_tokens=120, output_tokens=80, cache_read=3000, cache_write=0)
    assert result.provider_request_id == "oa_req_1"
    assert result.response_id == "chatcmpl-01ABC"
    assert result.served_model == GPT
    assert result.finish_reasons == ("stop",)
    assert result.rate_limit_headers == (("x-ratelimit-remaining-requests", "99"),)


async def test_openai_429_is_reported_once_with_retry_after() -> None:
    """Openai 429 is reported once with retry after."""
    headers = {"retry-after": "4", "x-request-id": "oa_429"}
    recorder = Recorder([httpx2.Response(429, json=error_body("rate_limit"), headers=headers)])
    result = await openai_llm(recorder).complete(make_request(model=GPT, provider="openai"))
    assert len(recorder.requests) == 1
    assert result.status == LLMStatus.RATE_LIMITED
    assert result.retry_after_s == 4.0
    assert result.provider_request_id == "oa_429"


@pytest.mark.parametrize(
    ("finish_reason", "refusal", "status"),
    [
        ("length", None, LLMStatus.TRUNCATED),
        ("content_filter", None, LLMStatus.REFUSAL),
        ("stop", "I can't help with that.", LLMStatus.REFUSAL),
    ],
)
async def test_openai_finish_reasons(
    finish_reason: str, refusal: str | None, status: LLMStatus
) -> None:
    """Openai finish reasons."""
    body = fixture_json("openai_completion_ok.json")
    body["choices"][0]["finish_reason"] = finish_reason
    body["choices"][0]["message"]["refusal"] = refusal
    if refusal is not None:
        body["choices"][0]["message"]["content"] = None
    recorder = Recorder([httpx2.Response(200, json=body)])
    result = await openai_llm(recorder).complete(make_request(model=GPT, provider="openai"))
    assert result.status == status
    if refusal is not None:
        assert result.raw_text == refusal
        assert "refusal" in result.finish_reasons


async def test_openai_timeout_never_raises() -> None:
    """Openai timeout never raises."""
    recorder = Recorder([httpx2.ReadTimeout("slow")])
    result = await openai_llm(recorder).complete(make_request(model=GPT, provider="openai"))
    assert result.status == LLMStatus.TIMEOUT


# ---------------------------------------------------------------- timeout and latency

OK_BODIES = {
    "anthropic": "anthropic_message_ok.json",
    "openai": "openai_completion_ok.json",
}
TIMEOUT_PARTS = ("connect", "read", "write", "pool")


async def complete_with(adapter: str, recorder: Recorder, **overrides: Any) -> LLMResult:
    """Run one call through the named adapter over the recorder."""
    if adapter == "anthropic":
        return await anthropic_llm(recorder, **overrides).complete(make_request())
    return await openai_llm(recorder, **overrides).complete(
        make_request(model=GPT, provider="openai")
    )


def ok_recorder(adapter: str) -> Recorder:
    """A recorder that answers every request with the adapter's OK fixture."""
    return Recorder([httpx2.Response(200, json=fixture_json(OK_BODIES[adapter]))])


def test_the_default_per_call_timeout_is_thirty_seconds() -> None:
    """§11.3 fixes the per-call timeout at 30 s."""
    assert DEFAULT_PER_CALL_TIMEOUT_S == 30
    assert AdapterOptions().timeout_s == 30


@pytest.mark.parametrize("adapter", list(OK_BODIES))
@pytest.mark.parametrize("timeout_s", [None, 5.0])
async def test_adapters_send_the_per_call_timeout(adapter: str, timeout_s: float | None) -> None:
    """Every outgoing request carries the configured timeout, not the SDK's 600 s default."""
    recorder = ok_recorder(adapter)
    overrides = {} if timeout_s is None else {"timeout_s": timeout_s}
    await complete_with(adapter, recorder, **overrides)
    expected = DEFAULT_PER_CALL_TIMEOUT_S if timeout_s is None else timeout_s
    assert recorder.requests[0].extensions["timeout"] == dict.fromkeys(TIMEOUT_PARTS, expected)


@pytest.mark.parametrize("adapter", list(OK_BODIES))
@pytest.mark.parametrize("outcome", ["ok", "timeout", "rate_limited"])
async def test_adapter_latency_comes_from_the_injected_clock(adapter: str, outcome: str) -> None:
    """Latency is the clock difference in milliseconds, for delivered and failed calls."""
    responses: dict[str, httpx2.Response | Exception] = {
        "ok": httpx2.Response(200, json=fixture_json(OK_BODIES[adapter])),
        "timeout": httpx2.ReadTimeout("slow"),
        "rate_limited": httpx2.Response(429, json=error_body("rate_limit_error")),
    }
    clock = iter([10.0, 12.5]).__next__
    result = await complete_with(adapter, Recorder([responses[outcome]]), clock=clock)
    assert result.latency_ms == 2500


# ---------------------------------------------------------------- recording


SAMPLE_RECORD_FIELDS: dict[str, Any] = {
    "call_id": "c1",
    "parent_call_id": None,
    "attempt_no": 1,
    "reason_for_call": ReasonForCall.FIRST,
    "started_at": "2026-10-05T10:00:00.000Z",
    "ended_at": "2026-10-05T10:00:01.000Z",
    "provider_name": "anthropic",
    "request_model": HAIKU,
    "response_model": HAIKU,
    "response_id": "msg_1",
    "provider_request_id": "req_1",
    "finish_reasons": ("end_turn",),
    "input_tokens": 10,
    "output_tokens": 5,
    "cache_read": 0,
    "cache_creation": 100,
    "request_sha256": "a" * 64,
    "user_message": "<boq_lines>béton</boq_lines>",
    "system_blocks_sha256": "b" * 64,
    "http_status": 200,
    "error_class": None,
    "raw_response": '{"lines": []}',
    "cost_usd": 0.0003,
    "latency_ms": 1000,
    "line_ids": ("L1", "L2"),
    "cache_hit": False,
    "source_call_id": None,
}
EXPECTED_CALL_RECORD_KEYS = frozenset(
    {
        "call_id",
        "parent_call_id",
        "attempt_no",
        "reason_for_call",
        "started_at",
        "ended_at",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.response.id",
        "provider_request_id",
        "gen_ai.response.finish_reasons",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.cache_read",
        "gen_ai.usage.cache_creation",
        "request_sha256",
        "user_message",
        "system_blocks_sha256",
        "http_status",
        "error_class",
        "raw_response",
        "cost_usd",
        "latency_ms",
        "line_ids",
        "cache_hit",
        "source_call_id",
    }
)


def sample_record(**overrides: Any) -> CallRecord:
    """Build a call record from the sample fields, with overrides."""
    return CallRecord(**{**SAMPLE_RECORD_FIELDS, **overrides})


def test_call_record_serialises_exactly_the_calls_jsonl_fields() -> None:
    """A record serialises to exactly the §9.6 keys and round-trips."""
    serialised = sample_record().to_json_dict()
    assert set(serialised) == set(CALL_RECORD_KEYS)
    assert set(CALL_RECORD_KEYS) == EXPECTED_CALL_RECORD_KEYS
    assert serialised["gen_ai.response.finish_reasons"] == ["end_turn"]
    assert serialised["reason_for_call"] == "first"
    assert CallRecord.from_json_dict(serialised) == sample_record()


def test_jsonl_sink_writes_one_sorted_utf8_object_per_line(tmp_path: Path) -> None:
    """Jsonl sink writes one sorted utf8 object per line."""
    path = tmp_path / "calls.jsonl"
    sink = JsonlCallSink(path)
    sink.write(sample_record())
    sink.write(sample_record(call_id="c2", reason_for_call=ReasonForCall.RETRY))
    raw = path.read_bytes()
    lines = raw.decode("utf-8").split("\n")
    assert lines[-1] == ""
    assert len(lines) == 3
    assert "béton" in lines[0]
    first = json.loads(lines[0])
    assert list(first) == sorted(first)
    assert b"\r\n" not in raw
    assert read_calls_jsonl(path) == [
        sample_record(),
        sample_record(call_id="c2", reason_for_call=ReasonForCall.RETRY),
    ]


def test_memory_sink_keeps_records_in_order() -> None:
    """Memory sink keeps records in order."""
    sink = MemoryCallSink()
    sink.write(sample_record())
    sink.write(sample_record(call_id="c2"))
    assert [record.call_id for record in sink.records] == ["c1", "c2"]


def test_attribution_splits_each_attempt_over_its_lines() -> None:
    """Attribution splits each attempt over its lines."""
    records = [
        sample_record(call_id="c1", cost_usd=0.3, latency_ms=900, line_ids=("L1", "L2", "L3")),
        sample_record(call_id="c2", cost_usd=0.1, latency_ms=100, line_ids=("L1",)),
    ]
    attributed = attribute_costs(records)
    assert attributed["L1"].cost_usd == pytest.approx(0.2)
    assert attributed["L1"].latency_ms == 400
    assert attributed["L2"].cost_usd == pytest.approx(0.1)
    assert attributed["L2"].latency_ms == 300
    assert attributed["L1"].call_ids == ("c1", "c2")
    assert sum(item.cost_usd for item in attributed.values()) == pytest.approx(0.4)


def test_system_blocks_hash_is_stable_and_sensitive_to_the_cache_flag() -> None:
    """System blocks hash is stable and sensitive to the cache flag."""
    blocks = (SystemBlock("a", cache=False), SystemBlock("b", cache=True))
    flipped = (SystemBlock("a", cache=False), SystemBlock("b", cache=False))
    assert system_blocks_sha256(blocks) == system_blocks_sha256(blocks)
    assert system_blocks_sha256(blocks) != system_blocks_sha256(flipped)
    assert len(system_blocks_sha256(blocks)) == 64


# ---------------------------------------------------------------- ReplayLLM


def replay_of(*records: CallRecord, strict: bool = True) -> ReplayLLM:
    """Build a replay adapter over the given records."""
    return ReplayLLM(RecordedRun.from_records(records), HAIKU, ALLOWLIST, strict=strict)


async def test_replay_hit_rebuilds_the_recorded_result() -> None:
    """A recorded 200 comes back with its text, usage, latency and metadata."""
    req = make_request()
    text = (await FakeLLM(HAIKU, ALLOWLIST).complete(req)).raw_text
    record = sample_record(request_sha256=req.sha256(), raw_response=text, latency_ms=4321)
    result = await replay_of(record).complete(req)
    assert result.status == LLMStatus.OK
    assert result.raw_text == text
    assert result.latency_ms == 4321
    assert result.usage == Usage(input_tokens=10, output_tokens=5, cache_read=0, cache_write=100)
    assert result.served_model == HAIKU
    assert result.provider_request_id == "req_1"


async def test_replay_miss_raises_in_strict_mode() -> None:
    """Strict replay raises on a request it has no record for."""
    with pytest.raises(ReplayMissError):
        await replay_of().complete(make_request())


async def test_replay_miss_at_runtime_is_a_non_retryable_result() -> None:
    """Runtime replay reports a miss as a result that is never retried."""
    result = await replay_of(strict=False).complete(make_request())
    assert result.status == LLMStatus.API_ERROR
    assert result.error_class == REPLAY_MISS_ERROR_CLASS
    assert result.should_retry is False
    assert result.http_status is None


async def test_replay_truncated_record_replays_as_truncated() -> None:
    """A recorded max_tokens stop replays as truncated."""
    req = make_request()
    record = sample_record(
        request_sha256=req.sha256(), raw_response='{"lines": [', finish_reasons=("max_tokens",)
    )
    assert (await replay_of(record).complete(req)).status == LLMStatus.TRUNCATED


async def test_replay_serves_every_recorded_attempt_of_a_hash_in_order() -> None:
    """The k-th request with a hash gets the k-th recorded attempt, failures included."""
    req = make_request()
    sha = req.sha256()
    throttled = sample_record(
        call_id="c0", request_sha256=sha, http_status=429, error_class="RateLimitError"
    )
    first = sample_record(call_id="c1", request_sha256=sha, raw_response='{"lines": []}')
    second = sample_record(call_id="c2", request_sha256=sha, raw_response='{"lines": [] }')
    replay = replay_of(throttled, first, second)
    results = [await replay.complete(req) for _ in range(3)]
    assert [r.status for r in results] == [LLMStatus.RATE_LIMITED, LLMStatus.OK, LLMStatus.OK]
    assert [r.raw_text for r in results[1:]] == ['{"lines": []}', '{"lines": [] }']
    with pytest.raises(ReplayMissError):
        await replay.complete(req)


@pytest.mark.parametrize(
    ("http_status", "error_class", "status"),
    [
        (None, "APITimeoutError", LLMStatus.TIMEOUT),
        (None, "APIConnectionError", LLMStatus.API_ERROR),
        (429, "RateLimitError", LLMStatus.RATE_LIMITED),
        (529, "OverloadedError", LLMStatus.OVERLOADED),
        (500, "InternalServerError", LLMStatus.API_ERROR),
        (400, "BadRequestError", LLMStatus.API_ERROR),
        (200, "ValidationError", LLMStatus.MALFORMED),
        (None, REPLAY_MISS_ERROR_CLASS, LLMStatus.API_ERROR),
    ],
)
async def test_replay_rebuilds_the_status_of_a_failed_attempt(
    http_status: int | None, error_class: str, status: LLMStatus
) -> None:
    """A failed attempt's status is re-derived from its HTTP status and error class."""
    req = make_request()
    record = sample_record(
        request_sha256=req.sha256(),
        http_status=http_status,
        error_class=error_class,
        finish_reasons=(),
        raw_response="oops",
    )
    result = await replay_of(record).complete(req)
    assert result.status == status
    assert result.error_class == error_class
    assert result.parsed is None


def test_recorded_run_reads_every_attempt_from_calls_jsonl(tmp_path: Path) -> None:
    """A recorded run keeps failed attempts too, in file order per hash."""
    sha = "c" * 64
    throttled = sample_record(
        call_id="c0", request_sha256=sha, http_status=429, error_class="RateLimitError"
    )
    first = sample_record(call_id="c1", request_sha256=sha)
    path = tmp_path / "calls.jsonl"
    sink = JsonlCallSink(path)
    for record in (throttled, first):
        sink.write(record)
    run = RecordedRun.from_calls_jsonl(path)
    assert run.attempts(sha) == (throttled, first)
    assert run.attempts("d" * 64) == ()
    assert len(run) == 2


def test_response_cache_keeps_content_records_per_hash_in_recording_order(tmp_path: Path) -> None:
    """The live cache drops failed attempts and keeps every content response, in order."""
    sha = "c" * 64
    throttled = sample_record(
        call_id="c0", request_sha256=sha, http_status=429, error_class="RateLimitError"
    )
    first = sample_record(call_id="c1", request_sha256=sha)
    second = sample_record(call_id="c2", request_sha256=sha, raw_response='{"lines": [1]}')
    path = tmp_path / "calls.jsonl"
    sink = JsonlCallSink(path)
    for record in (throttled, first, second):
        sink.write(record)
    cache = ResponseCache.from_calls_jsonl(path)
    assert cache.get(sha) == first
    assert cache.get(sha, occurrence=1) == second
    assert cache.get(sha, occurrence=2) is None
    assert cache.get("d" * 64) is None
    assert len(cache) == 1
