"""The /v1 API contract: limits, 422s, auth, X-Request-ID, partial failure, parity (§11.4)."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from oris_matcher.api.app import (
    HARD_LINE_LIMIT,
    REQUEST_ID_HEADER,
    LineIn,
    create_app,
    request_boq,
)
from oris_matcher.doctor import Runtime
from oris_matcher.domain.boq import LineKind
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.llm.base import LLMRequest
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, Fault, FaultKind
from oris_matcher.llm.wrapper import BudgetLedger, LLMWrapper, WrapperDeps
from oris_matcher.service import MatchService, RunOptions, RunProfile
from oris_matcher.settings import Settings, load_models_config, load_pricing

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
LIBRARIES = {
    "global": ROOT / "data" / "oris_materials_global.csv",
    "fr": ROOT / "data" / "oris_materials_fr.csv",
}
SMALL_BOQ = ROOT / "tests" / "fixtures" / "cli" / "small_boq.csv"
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
PRICING = load_pricing(CONFIG / "pricing.toml")
TOKEN = "api-token-for-tests"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "config_dir": CONFIG,
        "libraries": LIBRARIES,
        "anthropic_api_key": None,
        "openai_api_key": None,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def sent_paths(recorder: "Recorder") -> list[str]:
    paths: list[str] = []
    for request in recorder.calls:
        body = request.user_payload.split("<boq_lines>", 1)[1].rsplit("</boq_lines>", 1)[0]
        paths += [line["path"] for line in json.loads(body)["lines"]]
    return paths


class Recorder:
    """An LLM factory that hands out FakeLLMs and keeps them."""

    def __init__(self, behaviour: FakeBehaviour | None = None) -> None:
        self.behaviour = behaviour or FakeBehaviour()
        self.fakes: list[FakeLLM] = []

    def __call__(self, model: str) -> FakeLLM:
        fake = FakeLLM(model, ALLOWLIST, self.behaviour)
        self.fakes.append(fake)
        return fake

    @property
    def calls(self) -> list[LLMRequest]:
        return [request for fake in self.fakes for request in fake.calls]


async def no_sleep(seconds: float) -> None:
    del seconds


def client_for(recorder: Recorder | None = None, **overrides: Any) -> TestClient:
    runtime = Runtime(sleep=no_sleep)
    return TestClient(
        create_app(make_settings(**overrides), recorder or Recorder(), runtime=runtime)
    )


def csv_lines(path: Path = SMALL_BOQ) -> list[dict[str, Any]]:
    boq = read_boq(path)
    return [
        {
            "item_no": line.item_no,
            "short_description": line.short,
            "long_description": line.long,
            "unit": line.unit,
            "qty": line.qty,
        }
        for line in boq.lines
    ]


def post(client: TestClient, body: dict[str, Any], **headers: str) -> Any:
    return client.post("/v1/match", json=body, headers=headers)


def test_health_is_open() -> None:
    response = client_for().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers[REQUEST_ID_HEADER]


def test_ready_reports_libraries_and_the_injected_port_without_a_call() -> None:
    recorder = Recorder()
    response = client_for(recorder).get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "libraries": {"global": True, "fr": True},
        "key_configured": True,
    }
    assert recorder.fakes == []


def test_ready_is_503_without_a_key_or_a_loadable_library(tmp_path: Path) -> None:
    broken = tmp_path / "broken.csv"
    broken.write_bytes(b"\xff\xfe not utf-8")
    settings = make_settings().model_copy(update={"libraries": {**LIBRARIES, "bad": broken}})
    response = TestClient(create_app(settings)).get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["key_configured"] is False
    assert body["libraries"]["bad"] is False
    assert body["libraries"]["fr"] is True


def test_ready_with_a_key_and_no_injected_port() -> None:
    settings = make_settings(anthropic_api_key=SecretStr("sk-test"))
    response = TestClient(create_app(settings)).get("/ready")
    assert response.status_code == 200
    assert response.json()["key_configured"] is True


def test_match_returns_every_line_in_order_with_the_run_id() -> None:
    lines = csv_lines()
    response = post(client_for(), {"library": "fr", "lines": lines})

    assert response.status_code == 200
    body = response.json()
    assert response.headers[REQUEST_ID_HEADER] == body["run_id"]
    assert [d["line_index"] for d in body["decisions"]] == list(range(len(lines)))
    assert [d["item_no"] for d in body["decisions"]] == [line["item_no"] for line in lines]
    assert body["versions"]["policy_resolution"] == "fallback_strictest"
    assert len(body["versions"]["prompt"]) == 2
    assert sum(body["summary"]["counts"].values()) == len(lines)
    assert body["summary"]["failures"] == 0
    first = body["decisions"][0]
    assert first["decision"] == "not_a_material"
    assert first["reason"] == "HEADER"
    assert first["model"] == "rules"
    assert len(first["suggestions"]) == 2


def test_unknown_library_is_422() -> None:
    response = post(client_for(), {"library": "data/oris_materials_fr.csv", "lines": csv_lines()})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "library"]


def test_extra_field_is_422_at_both_levels() -> None:
    client = client_for()
    top = post(client, {"library": "fr", "lines": csv_lines(), "path": "x"})
    line = post(client, {"library": "fr", "lines": [{**csv_lines()[2], "remark": "x"}]})
    assert top.status_code == 422
    assert line.status_code == 422


@pytest.mark.parametrize(
    ("field", "cap"), [("item_no", 64), ("short_description", 1000), ("long_description", 4000)]
)
def test_field_caps_are_422(field: str, cap: int) -> None:
    client = client_for()
    at_cap = {**csv_lines()[2], field: "x" * cap}
    over = {**csv_lines()[2], field: "x" * (cap + 1)}
    assert post(client, {"library": "fr", "lines": [at_cap]}).status_code == 200
    response = post(client, {"library": "fr", "lines": [over]})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == field


def test_more_than_the_hard_limit_is_413_and_the_limit_itself_passes() -> None:
    client = client_for()
    title = {"item_no": "", "short_description": "Section title"}
    at_limit = post(client, {"library": "fr", "lines": [title] * HARD_LINE_LIMIT})
    over = post(client, {"library": "fr", "lines": [title] * (HARD_LINE_LIMIT + 1)})
    assert at_limit.status_code == 200
    assert len(at_limit.json()["decisions"]) == HARD_LINE_LIMIT
    assert over.status_code == 413
    assert over.headers[REQUEST_ID_HEADER]


def test_empty_line_list_is_422() -> None:
    assert post(client_for(), {"library": "fr", "lines": []}).status_code == 422


def test_numeric_qty_is_kept_as_its_text() -> None:
    line = {**csv_lines()[2], "qty": 120}
    response = post(client_for(), {"library": "fr", "lines": [line]})
    assert response.status_code == 200


def test_bearer_token_guards_v1_only() -> None:
    client = client_for(api_token=SecretStr(TOKEN))
    body = {"library": "fr", "lines": csv_lines()}
    assert post(client, body).status_code == 401
    assert post(client, body, Authorization="Bearer wrong").status_code == 401
    assert post(client, body, Authorization=TOKEN).status_code == 401
    assert post(client, body, Authorization=f"Bearer {TOKEN}").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_model_failures_are_still_200_with_every_line() -> None:
    def always_down(call_no: int, req: LLMRequest) -> Fault:
        del call_no, req
        return Fault(FaultKind.SERVER_ERROR)

    recorder = Recorder(FakeBehaviour(rule=always_down))
    lines = csv_lines()
    response = post(client_for(recorder), {"library": "fr", "lines": lines})

    assert response.status_code == 200
    decisions = response.json()["decisions"]
    assert len(decisions) == len(lines)
    items = [d for d in decisions if d["reason"] not in {"HEADER", "EMPTY_ROW"}]
    assert items
    assert all(d["decision"] == "needs_review" for d in items)
    reasons = {d["reason"] for d in items}
    assert all(r.startswith("LLM_FAILURE:") or r == "LLM_UNAVAILABLE" for r in reasons)
    assert response.json()["summary"]["failures"] == len(items)


def test_no_key_and_no_injected_port_is_503() -> None:
    client = TestClient(create_app(make_settings()))
    response = post(client, {"library": "fr", "lines": csv_lines()})
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_section_path_is_derived_from_header_rows_when_absent() -> None:
    recorder = Recorder()
    lines = csv_lines()
    assert post(client_for(recorder), {"library": "fr", "lines": lines}).status_code == 200
    assert "1 Gros oeuvre > 01.01. Fondations" in sent_paths(recorder)


def test_given_section_path_replaces_the_derived_one() -> None:
    recorder = Recorder()
    lines = csv_lines()
    lines[2] = {**lines[2], "section_path": ["Lot 3", "Semelles filantes"]}
    assert post(client_for(recorder), {"library": "fr", "lines": lines}).status_code == 200
    assert "Lot 3 > Semelles filantes" in sent_paths(recorder)


def test_request_boq_classifies_rows_like_the_reader() -> None:
    parsed = read_boq(SMALL_BOQ)
    built = request_boq([LineIn(**line) for line in csv_lines()])
    assert [line.kind for line in built.lines] == [line.kind for line in parsed.lines]
    assert [line.line_id for line in built.lines] == [line.line_id for line in parsed.lines]
    assert [line.section_path for line in built.lines] == [row.section_path for row in parsed.lines]
    assert built.path_mode == parsed.path_mode
    assert LineKind.EMPTY_ROW in {line.kind for line in built.lines}


def test_cli_and_api_give_the_same_decisions_and_request_hashes() -> None:
    recorder = Recorder()
    response = post(client_for(recorder), {"library": "fr", "lines": csv_lines()})
    api_hashes = sorted(request.sha256() for request in recorder.calls)
    api_decisions = [(d["decision"], d["reason"]) for d in response.json()["decisions"]]

    service = MatchService.from_settings(make_settings())
    fake = FakeLLM("claude-haiku-4-5-20251001", ALLOWLIST)
    wrapper = LLMWrapper(fake, PRICING, BudgetLedger(100.0), None, WrapperDeps())
    result = asyncio.run(
        service.match(
            read_boq(SMALL_BOQ),
            "fr",
            profile=RunProfile.B3,
            llm=wrapper,
            options=RunOptions(run_id="cli"),
        )
    )
    cli_hashes = sorted(request.sha256() for request in fake.calls)
    cli_decisions = [(item.decision.decision.value, item.decision.reason) for item in result.lines]

    assert api_hashes == cli_hashes
    assert api_decisions == cli_decisions


ONE_LINE = {
    "item_no": "01.01.0010.",
    "short_description": "Ready-mix concrete C30/37 for foundations",
    "long_description": "",
    "unit": "m3",
    "qty": "10",
}


@pytest.mark.parametrize("library", ["global", "fr"])
def test_a_one_line_request_reaches_the_model(library: str) -> None:
    recorder = Recorder()
    response = post(client_for(recorder), {"library": library, "lines": [ONE_LINE]})

    assert response.status_code == 200
    decision = response.json()["decisions"][0]
    assert decision["reason"] != "BUDGET_CAP"
    assert decision["call_ids"]
    assert len(recorder.calls) == 2


def test_breaker_trip_hands_the_request_to_the_fallback() -> None:
    def down_for_haiku(call_no: int, req: LLMRequest) -> Fault | None:
        del call_no
        return Fault(FaultKind.SERVER_ERROR) if req.model.startswith("claude") else None

    recorder = Recorder(FakeBehaviour(rule=down_for_haiku))
    overrides = {"openai_api_key": SecretStr("sk-openai-test"), "max_retries": 0}
    client = client_for(recorder, breaker_consecutive_failures=1, batch_size=1, **overrides)
    lines = csv_lines()

    response = post(client, {"library": "fr", "lines": lines})

    assert response.status_code == 200
    decisions = response.json()["decisions"]
    assert {d["reason"] for d in decisions}.isdisjoint({"LLM_UNAVAILABLE"})
    assert any(d["model"].startswith("gpt-4o-mini") for d in decisions)
    assert any(request.model.startswith("gpt-4o-mini") for request in recorder.calls)
