"""Contract tests every LLM adapter must pass (DESIGN.md §11.9)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from oris_matcher.llm.anthropic_llm import AnthropicLLM
from oris_matcher.llm.base import (
    AdapterOptions,
    LLMPort,
    LLMRequest,
    LLMResult,
    LLMStatus,
    SystemBlock,
)
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, Fault, FaultKind
from oris_matcher.llm.openai_llm import OpenAIChatLLM
from oris_matcher.llm.recording import CallRecord, ReasonForCall
from oris_matcher.llm.replay_llm import RecordedRun, ReplayLLM
from oris_matcher.prompts.v1.schema import output_json_schema
from oris_matcher.settings import load_models_config

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "llm"
ALLOWLIST = load_models_config(ROOT / "config" / "models.toml").allowlist.patterns
HAIKU = "claude-haiku-4-5-20251001"
GPT = "gpt-4o-mini-2024-07-18"
OK, RATE_LIMITED, OVERLOADED, TRUNCATED, REFUSAL, TIMEOUT = (
    "ok",
    "rate_limited",
    "overloaded",
    "truncated",
    "refusal",
    "timeout",
)
EXPECTED = {
    OK: LLMStatus.OK,
    RATE_LIMITED: LLMStatus.RATE_LIMITED,
    OVERLOADED: LLMStatus.OVERLOADED,
    TRUNCATED: LLMStatus.TRUNCATED,
    REFUSAL: LLMStatus.REFUSAL,
    TIMEOUT: LLMStatus.TIMEOUT,
}
RETRY_AFTER_S = 7.0

PortFactory = Callable[[str, LLMRequest], LLMPort]


def make_request(model: str = HAIKU, provider: str = "anthropic") -> LLMRequest:
    """Build a request for the given line ids."""
    payload = json.dumps({"lines": [{"id": "L1"}, {"id": "L2"}]}, sort_keys=True)
    return LLMRequest(
        provider=provider,
        model=model,
        system_blocks=(SystemBlock("vocabulary", cache=False), SystemBlock("library", cache=True)),
        schema_json=json.dumps(output_json_schema()),
        user_payload=f"<boq_lines>{payload}</boq_lines>",
        max_tokens=800,
        temperature=0.0,
        line_ids=("L1", "L2"),
    )


def fixture_json(name: str) -> dict[str, Any]:
    """Load a JSON fixture from tests/fixtures/llm."""
    with open(FIXTURES / name, encoding="utf-8") as handle:
        loaded: dict[str, Any] = json.load(handle)
    return loaded


def mock_client(response: httpx2.Response | Exception) -> httpx2.AsyncClient:
    """Build an HTTP client whose mock transport serves one response."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        """Serve the response, or raise it when it is an exception."""
        if isinstance(response, Exception):
            raise response
        return response

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def anthropic_response(scenario: str) -> httpx2.Response | Exception:
    """Build the Anthropic mock response for a scenario."""
    body = fixture_json("anthropic_message_ok.json")
    retry = {"retry-after": str(int(RETRY_AFTER_S)), "request-id": "req_x"}
    error = {"type": "error", "error": {"type": "x", "message": "x"}}
    if scenario == RATE_LIMITED:
        return httpx2.Response(429, json=error, headers=retry)
    if scenario == OVERLOADED:
        return httpx2.Response(529, json=error)
    if scenario == TIMEOUT:
        return httpx2.ReadTimeout("slow")
    if scenario == TRUNCATED:
        body["stop_reason"] = "max_tokens"
        body["content"][0]["text"] = '{"lines": [{"id": "L1"'
    if scenario == REFUSAL:
        body["stop_reason"] = "refusal"
        body["content"] = []
    return httpx2.Response(200, json=body, headers={"request-id": "req_x"})


def openai_response(scenario: str) -> httpx2.Response:
    """Build the OpenAI mock response for a scenario."""
    if scenario == RATE_LIMITED:
        error = {"error": {"message": "slow", "type": "rate_limit"}}
        return httpx2.Response(429, json=error, headers={"retry-after": str(int(RETRY_AFTER_S))})
    return httpx2.Response(200, json=fixture_json("openai_completion_ok.json"))


def make_fake(scenario: str, req: LLMRequest) -> LLMPort:
    """Build a fake that injects the scenario's fault on the first call."""
    faults = {
        RATE_LIMITED: Fault(FaultKind.RATE_LIMITED, retry_after_s=RETRY_AFTER_S),
        OVERLOADED: Fault(FaultKind.OVERLOADED),
        TRUNCATED: Fault(FaultKind.TRUNCATED),
        REFUSAL: Fault(FaultKind.REFUSAL),
        TIMEOUT: Fault(FaultKind.TIMEOUT),
    }
    return FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(script=(faults.get(scenario),)))


RECORD_TEMPLATE: dict[str, Any] = {
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
    "input_tokens": 1,
    "output_tokens": 1,
    "cache_read": 0,
    "cache_creation": 0,
    "system_blocks_sha256": "b" * 64,
    "http_status": 200,
    "error_class": None,
    "cost_usd": 0.0,
    "latency_ms": 10,
    "cache_hit": False,
    "source_call_id": None,
}


def make_replay(scenario: str, req: LLMRequest) -> LLMPort:
    """Build a strict replay holding one recorded 200 for the scenario's stop reason."""
    finish = {TRUNCATED: ("max_tokens",), REFUSAL: ("refusal",)}.get(scenario, ("end_turn",))
    record = CallRecord(
        **RECORD_TEMPLATE,
        finish_reasons=finish,
        request_sha256=req.sha256(),
        user_message=req.user_payload,
        raw_response=json.dumps({"lines": []}),
        line_ids=req.line_ids,
    )
    return ReplayLLM(RecordedRun.from_records([record]), HAIKU, ALLOWLIST, strict=True)


def make_anthropic(scenario: str, req: LLMRequest) -> LLMPort:
    """Build the Anthropic adapter over the scenario's mock response."""
    options = AdapterOptions(http_client=mock_client(anthropic_response(scenario)))
    return AnthropicLLM(HAIKU, ALLOWLIST, "test-key", options)


def make_openai(scenario: str, req: LLMRequest) -> LLMPort:
    """Build the OpenAI adapter over the scenario's mock response."""
    options = AdapterOptions(http_client=mock_client(openai_response(scenario)))
    return OpenAIChatLLM(GPT, ALLOWLIST, "test-key", options)


ADAPTERS: dict[str, tuple[PortFactory, str, str, frozenset[str]]] = {
    "fake": (make_fake, HAIKU, "anthropic", frozenset(EXPECTED)),
    "replay": (make_replay, HAIKU, "anthropic", frozenset({OK, TRUNCATED, REFUSAL})),
    "anthropic": (make_anthropic, HAIKU, "anthropic", frozenset(EXPECTED)),
    "openai": (make_openai, GPT, "openai", frozenset({OK, RATE_LIMITED})),
}
CASES = [
    pytest.param(adapter, scenario, id=f"{adapter}-{scenario}")
    for adapter, (_, _, _, scenarios) in ADAPTERS.items()
    for scenario in EXPECTED
    if scenario in scenarios
]


async def run_case(adapter: str, scenario: str) -> LLMResult:
    """Run one adapter on one scenario."""
    factory, model, provider, _ = ADAPTERS[adapter]
    req = make_request(model, provider)
    return await factory(scenario, req).complete(req)


@pytest.mark.parametrize(("adapter", "scenario"), CASES)
async def test_port_reports_failures_in_status_and_never_raises(
    adapter: str, scenario: str
) -> None:
    """Port reports failures in status and never raises."""
    result = await run_case(adapter, scenario)
    assert isinstance(result, LLMResult)
    assert result.status == EXPECTED[scenario]
    assert isinstance(result.raw_text, str)
    assert result.latency_ms >= 0


@pytest.mark.parametrize(("adapter", "scenario"), [c for c in CASES if c.values[1] == OK])
async def test_port_ok_result_parses_its_raw_text(adapter: str, scenario: str) -> None:
    """Port ok result parses its raw text."""
    result = await run_case(adapter, scenario)
    assert result.parsed is not None
    assert dict(result.parsed) == json.loads(result.raw_text)
    assert result.served_model
    assert result.finish_reasons


@pytest.mark.parametrize(
    ("adapter", "scenario"), [c for c in CASES if c.values[1] in {RATE_LIMITED}]
)
async def test_port_rate_limit_carries_retry_after(adapter: str, scenario: str) -> None:
    """Port rate limit carries retry after."""
    result = await run_case(adapter, scenario)
    assert result.retry_after_s == RETRY_AFTER_S
    assert result.parsed is None


@pytest.mark.parametrize(
    ("adapter", "scenario"), [c for c in CASES if c.values[1] in {TRUNCATED, REFUSAL}]
)
async def test_port_content_failures_carry_no_parsed_answer(adapter: str, scenario: str) -> None:
    """Port content failures carry no parsed answer."""
    result = await run_case(adapter, scenario)
    assert result.parsed is None
    assert result.finish_reasons
