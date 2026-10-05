"""``oris doctor``: offline checks, and live checks over an HTTP MockTransport (§11.3, §12)."""

import csv
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from oris_matcher.doctor import (
    SYNTHETIC_BOQ,
    CheckStatus,
    DoctorOptions,
    DoctorReport,
    Runtime,
    infer_tier,
    render_table,
    run_doctor,
)
from oris_matcher.llm.fake_llm import default_answer
from oris_matcher.llm.recording import read_calls_jsonl
from oris_matcher.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
LIBRARIES = {
    "global": ROOT / "data" / "oris_materials_global.csv",
    "fr": ROOT / "data" / "oris_materials_fr.csv",
}
BOQ_INPUTS = (
    ROOT / "input" / "boq_dataset_input_en.csv",
    ROOT / "input" / "boq_dataset_input_fr.csv",
)
HAIKU = "claude-haiku-4-5-20251001"
GPT = "gpt-4o-mini-2024-07-18"
FIXED_NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
ANTHROPIC_SECRET = "sk-ant-doctor-secret-value"
OPENAI_SECRET = "sk-openai-doctor-secret-value"
COUNTED_TOKENS = 5000
MIN_INPUT_TEXT = 12


def fake_git(args: Sequence[str], root: Path) -> str:
    del root
    return "c" * 40 if "rev-parse" in args else ""


async def no_sleep(seconds: float) -> None:
    del seconds


def make_settings(*, anthropic: bool = False, openai: bool = False) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        config_dir=CONFIG,
        libraries=LIBRARIES,
        anthropic_api_key=SecretStr(ANTHROPIC_SECRET) if anthropic else None,
        openai_api_key=SecretStr(OPENAI_SECRET) if openai else None,
    )


def options(tmp_path: Path, *, live: bool) -> DoctorOptions:
    return DoctorOptions(live=live, evidence_dir=tmp_path / "evidence", runs_dir=tmp_path / "runs")


class Api:
    """A MockTransport handler serving Anthropic messages, count_tokens and OpenAI chat."""

    def __init__(self, *, messages_status: int = 200) -> None:
        self.messages_status = messages_status
        self.requests: list[httpx2.Request] = []

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": COUNTED_TOKENS})
        if request.url.host == "api.openai.com":
            return self._openai(body)
        return self._anthropic(body)

    def _answer(self, payload: str) -> str:
        ids = re.findall(r'"id": "(L\d+)"', payload)
        return json.dumps({"lines": [default_answer(line_id) for line_id in ids]})

    def _anthropic(self, body: dict[str, Any]) -> httpx2.Response:
        headers = {
            "request-id": f"req_{len(self.requests)}",
            "anthropic-ratelimit-requests-limit": "1000",
            "anthropic-ratelimit-requests-remaining": "999",
        }
        if self.messages_status != 200:
            error = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}
            return httpx2.Response(self.messages_status, json=error, headers=headers)
        message = {
            "id": "msg_doctor",
            "type": "message",
            "role": "assistant",
            "model": HAIKU,
            "content": [{"type": "text", "text": self._answer(body["messages"][0]["content"])}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 40, "output_tokens": 30, "cache_creation_input_tokens": 0},
        }
        return httpx2.Response(200, json=message, headers=headers)

    def _openai(self, body: dict[str, Any]) -> httpx2.Response:
        completion = {
            "id": "chatcmpl-doctor",
            "object": "chat.completion",
            "created": 1759622400,
            "model": GPT,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": self._answer(body["messages"][-1]["content"]),
                        "refusal": None,
                    },
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70},
        }
        return httpx2.Response(200, json=completion, headers={"x-request-id": "oa_req"})


def runtime_for(tmp_path: Path, api: Api | None = None) -> Runtime:
    client = api.client if api is not None else lambda: None
    return Runtime(
        root=lambda: tmp_path,
        clock=lambda: FIXED_NOW,
        sleep=no_sleep,
        http_client=client,
        git=fake_git,
    )


def statuses(report: DoctorReport) -> dict[str, CheckStatus]:
    return {check.name: check.status for check in report.checks}


def evidence_text(report: DoctorReport) -> str:
    return report.evidence_path.read_text(encoding="utf-8")


def input_texts() -> set[str]:
    texts: set[str] = set()
    for path in BOQ_INPUTS:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                texts |= {value for value in row.values() if len(value or "") >= MIN_INPUT_TEXT}
    return texts


def test_offline_without_keys_fails_on_the_anthropic_key_only(tmp_path: Path) -> None:
    report = run_doctor(make_settings(), options(tmp_path, live=False), runtime_for(tmp_path))

    status = statuses(report)
    assert status["anthropic_key"] == CheckStatus.FAIL
    assert status["openai_key"] == CheckStatus.SKIP
    for name in ("config", "allowlist", "pinned_model", "pricing", "library:global", "library:fr"):
        assert status[name] == CheckStatus.PASS
    assert not report.passed
    assert report.evidence_path == tmp_path / "evidence" / "doctor_2026-10-05.json"
    saved = json.loads(evidence_text(report))
    assert saved["passed"] is False
    assert saved["live"] is False
    assert [check["name"] for check in saved["checks"]] == [c.name for c in report.checks]
    assert not (tmp_path / "runs").exists()


def test_offline_with_keys_passes_and_never_writes_a_key(tmp_path: Path) -> None:
    settings = make_settings(anthropic=True, openai=True)
    report = run_doctor(settings, options(tmp_path, live=False), runtime_for(tmp_path))

    assert report.passed
    table = render_table(report)
    for secret in (ANTHROPIC_SECRET, OPENAI_SECRET):
        assert secret not in table
        assert secret not in evidence_text(report)
    assert "PASS  anthropic_key" in table
    assert "doctor (offline): PASS" in table


def test_offline_with_a_broken_config_reports_it(tmp_path: Path) -> None:
    settings = make_settings().model_copy(update={"config_dir": tmp_path / "missing"})
    report = run_doctor(settings, options(tmp_path, live=True), runtime_for(tmp_path))
    status = statuses(report)
    assert status["config"] == CheckStatus.FAIL
    assert status["live"] == CheckStatus.FAIL


def test_live_checks_pass_over_the_mock_transport(tmp_path: Path) -> None:
    api = Api()
    settings = make_settings(anthropic=True, openai=True)
    report = run_doctor(settings, options(tmp_path, live=True), runtime_for(tmp_path, api))

    status = statuses(report)
    for name in (
        "primary_call",
        "pinned_model_served",
        "rate_limit_tier",
        "count_tokens:global",
        "count_tokens:fr",
        "fallback_call",
    ):
        assert status[name] == CheckStatus.PASS, name
    assert report.passed
    checks = {check.name: check for check in report.checks}
    assert checks["rate_limit_tier"].data["tier"] == "2"
    assert checks["count_tokens:fr"].data == {"tokens": COUNTED_TOKENS, "cache_eligible": True}
    messages = [r for r in api.requests if r.url.path == "/v1/messages"]
    assert len(messages) == 1
    assert len([r for r in api.requests if r.url.host == "api.openai.com"]) == 1
    for secret in (ANTHROPIC_SECRET, OPENAI_SECRET):
        assert secret not in evidence_text(report)
        assert secret not in render_table(report)


def test_live_call_is_recorded_in_a_doctor_run_folder_with_synthetic_lines_only(
    tmp_path: Path,
) -> None:
    api = Api()
    report = run_doctor(
        make_settings(anthropic=True), options(tmp_path, live=True), runtime_for(tmp_path, api)
    )

    folder = Path(next(c for c in report.checks if c.name == "primary_call").data["run_folder"])
    assert folder.parent == tmp_path / "runs"
    assert folder.name.startswith("doctor-")
    records = read_calls_jsonl(folder / "calls.jsonl")
    assert len(records) == 1
    assert records[0].response_model == HAIKU
    assert records[0].provider_request_id is not None
    payload = records[0].user_message
    assert "Ready-mix concrete C30/37 for foundations" in payload
    assert "Site supervision, lump sum" in payload
    assert not [text for text in input_texts() if text in payload]
    sent = b"".join(request.content for request in api.requests)
    assert b"Ready-mix concrete" in sent
    assert not [text for text in input_texts() if text.encode("utf-8") in sent]


def test_live_fallback_is_skipped_without_its_key(tmp_path: Path) -> None:
    api = Api()
    report = run_doctor(
        make_settings(anthropic=True), options(tmp_path, live=True), runtime_for(tmp_path, api)
    )
    assert statuses(report)["fallback_call"] == CheckStatus.SKIP
    assert not [request for request in api.requests if request.url.host == "api.openai.com"]


def test_live_without_the_anthropic_key_fails_without_any_request(tmp_path: Path) -> None:
    api = Api()
    report = run_doctor(make_settings(), options(tmp_path, live=True), runtime_for(tmp_path, api))
    status = statuses(report)
    assert status["primary_call"] == CheckStatus.FAIL
    assert status["count_tokens"] == CheckStatus.FAIL
    assert api.requests == []


def test_live_rate_limited_call_fails_the_call_check(tmp_path: Path) -> None:
    api = Api(messages_status=429)
    report = run_doctor(
        make_settings(anthropic=True), options(tmp_path, live=True), runtime_for(tmp_path, api)
    )
    status = statuses(report)
    assert status["primary_call"] == CheckStatus.FAIL
    assert status["rate_limit_tier"] == CheckStatus.PASS
    assert not report.passed
    assert len([r for r in api.requests if r.url.path == "/v1/messages"]) == 1


class OverCapApi(Api):
    """Answers every line, but with evidence over the 12-word cap (G1 observation O5)."""

    def _answer(self, payload: str) -> str:
        ids = re.findall(r'"id": "(L\d+)"', payload)
        long_evidence = " ".join(["word"] * 14)
        lines = [{**default_answer(line_id), "evidence": long_evidence} for line_id in ids]
        return json.dumps({"lines": lines})


def test_live_schema_invalid_answers_fail_the_call_check(tmp_path: Path) -> None:
    report = run_doctor(
        make_settings(anthropic=True),
        options(tmp_path, live=True),
        runtime_for(tmp_path, OverCapApi()),
    )
    assert statuses(report)["primary_call"] == CheckStatus.FAIL
    assert not report.passed


@pytest.mark.parametrize(
    ("limit", "tier"),
    [("50", "1"), ("1000", "2"), ("2000", "3"), ("4000", "4"), ("9000", "custom (above tier 4)")],
)
def test_infer_tier_from_the_requests_limit(limit: str, tier: str) -> None:
    assert infer_tier({"anthropic-ratelimit-requests-limit": limit}) == tier


def test_infer_tier_is_none_when_unknown() -> None:
    assert infer_tier({}) is None
    assert infer_tier({"anthropic-ratelimit-requests-limit": "77"}) is None
    assert infer_tier({"anthropic-ratelimit-requests-limit": "lots"}) is None


def test_synthetic_lines_are_not_from_the_boq_inputs() -> None:
    synthetic = SYNTHETIC_BOQ.decode("utf-8")
    assert not [text for text in input_texts() if text in synthetic]


def test_doctor_run_manifest_records_the_rate_limit_tier(tmp_path: Path) -> None:
    api = Api()
    report = run_doctor(
        make_settings(anthropic=True), options(tmp_path, live=True), runtime_for(tmp_path, api)
    )
    folder = Path(next(c for c in report.checks if c.name == "primary_call").data["run_folder"])
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rate_limit_tier"] == "2"
    assert manifest["rate_limit_headers"]["anthropic-ratelimit-requests-limit"]
    assert manifest["mode"] == "live"
