"""``oris explain`` and ``oris demo``: read-only, $0 views of recorded runs (DESIGN.md §12)."""

import csv
import hashlib
import json
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from oris_matcher import cli
from oris_matcher.doctor import LLMSpec, Runtime
from oris_matcher.domain.library import load_library
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM
from oris_matcher.settings import load_models_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
FR_LIBRARY = ROOT / "data" / "oris_materials_fr.csv"
GLOBAL_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
SMALL_BOQ = ROOT / "tests" / "fixtures" / "cli" / "small_boq.csv"
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
HAIKU = "claude-haiku-4-5-20251001"
FIXED_NOW = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "ORIS_API_TOKEN",
    "ORIS_PRIMARY_MODEL",
    "ORIS_FALLBACK_MODEL",
    "ORIS_VERIFIER_ADOPTED",
    "ORIS_ENRICHMENT",
)
EN_RUN = ROOT / "runs" / "submission" / "20261008T023928Z-56f85fb8"
EN_OUTPUT = ROOT / "output" / "improved_output_en.csv"
EN_MATCHED_ITEM = "01.02.0010."
EN_HEADER_ITEM = "0"
RUNNER = CliRunner()


def fake_git(args: Sequence[str], root: Path) -> str:
    del root
    return "a" * 40 + "\n" if "rev-parse" in args else ""


async def no_sleep(seconds: float) -> None:
    del seconds


def make_runtime(root: Path, factory: Any = None) -> Runtime:
    return Runtime(
        root=lambda: root,
        clock=lambda: FIXED_NOW,
        sleep=no_sleep,
        git=fake_git,
        adapter_factory=factory,
    )


def invoke(args: list[str]) -> Result:
    return RUNNER.invoke(cli.app, args)


def tree_digest(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def audit_of(folder: Path) -> list[dict[str, Any]]:
    text = (folder / "audit.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _fake_with_answers() -> FakeLLM:
    from test_service import answers_for  # noqa: PLC0415

    library = load_library(GLOBAL_LIBRARY.read_bytes(), catalogue="global")
    answers = answers_for(read_boq(SMALL_BOQ), library)
    verdicts = {
        transport: {"id": transport, "evidence": answer["evidence"], "code": answer["top1"]}
        for transport, answer in answers.items()
        if answer.get("top1")
    }
    return FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=answers, verifier_answers=verdicts))


class NoModel:
    """An adapter factory that fails the test if any command builds an adapter."""

    def __call__(self, spec: LLMSpec, model: str) -> Any:
        raise AssertionError(f"a model adapter was built: {spec} {model}")


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ORIS_CONFIG_DIR", str(CONFIG))
    monkeypatch.setenv("ORIS_VERIFIER_ADOPTED", "true")
    shutil.copytree(ROOT / "data" / "enrichment", tmp_path / "data" / "enrichment")
    libraries = {"global": str(GLOBAL_LIBRARY), "fr": str(FR_LIBRARY)}
    monkeypatch.setenv("ORIS_LIBRARIES", json.dumps(libraries))
    fake = _fake_with_answers()
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(tmp_path, lambda spec, model: fake))
    return tmp_path


@pytest.fixture
def fake_run(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    output = workdir / "out.csv"
    args = ["--input", str(SMALL_BOQ), "--library", str(GLOBAL_LIBRARY), "--output", str(output)]
    result = invoke([*args, "--llm", "fake"])
    assert result.exit_code == 0, result.output
    folders = sorted((workdir / "runs").iterdir())
    assert len(folders) == 1
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(workdir, NoModel()))
    return folders[0]


@pytest.fixture
def repo_root(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(ROOT)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ORIS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ORIS_LIBRARIES", raising=False)
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(ROOT))
    return ROOT


@pytest.fixture
def lockbox(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(repo_root, NoModel()))
    return EN_RUN


def explain(run: Path, item: str, *extra: str) -> Result:
    return invoke(["explain", "--run", str(run), "--item", item, *extra])


def matched_record(run: Path) -> dict[str, Any]:
    matched = [record for record in audit_of(run) if record["decision"] == "matched"]
    assert matched, "the fake run matched no line"
    return matched[0]


# explain, on a fake run


def test_explain_a_matched_item_shows_the_whole_trace(fake_run: Path) -> None:
    record = matched_record(fake_run)
    before = tree_digest(fake_run.parent.parent)

    result = explain(fake_run, record["item_no"])

    assert result.exit_code == 0, result.output
    text = result.stdout
    line = next(item for item in read_boq(SMALL_BOQ).lines if item.line_id == record["line_id"])
    for needle in (
        record["item_no"],
        line.short,
        "1 Gros oeuvre > 01.01. Fondations",
        "decision: matched",
        record["reason"],
        "rule D9",
        "policy T8",
        "exact",
        "pass 1",
        "pass 2",
        "verifier: flagged",
        record["top1"],
        "prompts/",
        *record["call_ids"],
    ):
        assert needle in text, needle
    assert tree_digest(fake_run.parent.parent) == before


def test_explain_json_is_one_sorted_object_with_every_field(fake_run: Path) -> None:
    record = matched_record(fake_run)

    result = explain(fake_run, record["item_no"], "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert result.stdout.strip() == json.dumps(payload, sort_keys=True, ensure_ascii=False)
    assert payload["item_no"] == record["item_no"]
    assert payload["line_id"] == record["line_id"]
    assert payload["decision"] == "matched"
    assert payload["rule"] == "D9"
    assert payload["reason"] == record["reason"]
    assert payload["model_called"] is True
    assert payload["input"]["section_path"] == "1 Gros oeuvre > 01.01. Fondations"
    assert payload["policy"]["policy_id"] == "T8"
    assert payload["policy"]["threshold_minimum"] == {
        "b": "no_evidence",
        "confidence_bucket": "<70",
        "v": 2,
    }
    assert [item["top1"] for item in payload["passes"]] == [record["top1"], record["top1"]]
    assert payload["verifier"]["flagged"] is True
    assert payload["verifier"]["top1"] == record["top1"]
    assert payload["matched_row"]["code"] == record["top1"]
    assert payload["matched_row"]["row_id"] == record["suggested_row_id"]
    assert payload["suggestions"][0]["code"] == record["top1"]
    assert payload["cost_usd"] == record["cost_usd"]
    assert payload["latency_ms"] == record["latency_ms"]
    calls = payload["calls"]
    assert [call["call_id"] for call in calls] == record["call_ids"]
    recorded = {
        json.loads(text)["call_id"]: json.loads(text)
        for text in (fake_run / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    }
    for call in calls:
        source = recorded[call["call_id"]]
        assert call["request_sha256"] == source["request_sha256"]
        assert call["system_prompt_file"] == f"prompts/{source['system_blocks_sha256']}.txt"
        assert (fake_run / call["system_prompt_file"]).is_file()
        assert call["provider_request_id"] == source["provider_request_id"]
    assert {call["kind"] for call in calls} == {"main", "verifier"}


def test_explain_a_header_says_no_model_call_was_made(fake_run: Path) -> None:
    result = explain(fake_run, "1")

    assert result.exit_code == 0, result.output
    assert "no model call was made" in result.stdout
    assert "rule D0" in result.stdout
    assert "HEADER" in result.stdout
    payload = json.loads(explain(fake_run, "1", "--json").stdout)
    assert payload["model_called"] is False
    assert payload["calls"] == []
    assert payload["passes"] == []


def test_explain_accepts_a_line_id(fake_run: Path) -> None:
    record = matched_record(fake_run)

    result = explain(fake_run, record["line_id"], "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["item_no"] == record["item_no"]


def test_explain_an_unknown_item_is_a_usage_error(fake_run: Path) -> None:
    result = explain(fake_run, "99.99.9999.")

    assert result.exit_code == 2
    assert "99.99.9999." in result.stderr
    assert "not in run" in result.stderr


def test_explain_a_missing_run_folder_is_a_usage_error(workdir: Path) -> None:
    result = explain(workdir / "runs" / "nope", "1")

    assert result.exit_code == 2
    assert "error:" in result.stderr


# explain, on the committed lockbox run


def _output_row(path: Path, item_no: str) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return next(row for row in csv.DictReader(handle) if row["Item No."] == item_no)


def test_explain_a_committed_lockbox_match(lockbox: Path) -> None:
    result = explain(lockbox, EN_MATCHED_ITEM, "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    row = _output_row(EN_OUTPUT, EN_MATCHED_ITEM)
    assert payload["decision"] == row["decision"] == "matched"
    assert payload["reason"] == row["reason"] == "SIGNAL:T8"
    assert payload["policy"]["policy_id"] == "T8"
    assert payload["policy"]["policy_resolution"] == "exact"
    matched = payload["matched_row"]
    assert matched["material_type"] == row["material_type"]
    assert matched["material_usage"] == row["material_usage"]
    assert matched["material_subtype"] == row["material_subtype"]
    second = payload["suggestions"][1]
    assert second["material_type"] == row["suggested2_type"]
    assert second["material_subtype"] == row["suggested2_subtype"]
    assert [call["call_id"] for call in payload["calls"]] == row["call_ids"].split(";")
    assert payload["verifier"]["flagged"] is True
    assert "req_011CfoziZYfaMPGaF72BKDf3" in {
        call["provider_request_id"] for call in payload["calls"]
    }
    text = explain(lockbox, EN_MATCHED_ITEM).stdout
    assert "Break out existing carriageway slabs" in text
    assert "01.02. Demolition, dismantling and waste" in text


def test_explain_a_committed_lockbox_header(lockbox: Path) -> None:
    result = explain(lockbox, EN_HEADER_ITEM)

    assert result.exit_code == 0, result.output
    assert "no model call was made" in result.stdout
    assert "Preliminaries and general items" in result.stdout


# demo


def _demo_runs(
    fake_run: Path, expected: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, cli.DemoRun]:
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(fake_run.parent.parent))
    return {"en": cli.DemoRun(fake_run, expected), "fr": cli.DemoRun(fake_run, expected)}


def test_demo_replays_each_language_and_reports_byte_identity(
    fake_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = fake_run.parent.parent / "out.csv"
    monkeypatch.setattr(cli, "DEMO_RUNS", _demo_runs(fake_run, expected, monkeypatch))
    before = tree_digest(fake_run)

    result = invoke(["demo"])

    assert result.exit_code == 0, result.output
    text = result.stdout
    assert text.count("byte-identical") >= 2
    for needle in ("demo en", "demo fr", "6 rows", "matched", "needs_review", "not_a_material"):
        assert needle in text, needle
    assert "flip rate" in text
    assert tree_digest(fake_run) == before


def test_demo_one_language_replays_only_that_one(
    fake_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = fake_run.parent.parent / "out.csv"
    monkeypatch.setattr(cli, "DEMO_RUNS", _demo_runs(fake_run, expected, monkeypatch))

    result = invoke(["demo", "--lang", "fr"])

    assert result.exit_code == 0, result.output
    assert "demo fr" in result.stdout
    assert "demo en" not in result.stdout


def test_demo_exits_1_when_a_replay_is_not_byte_identical(
    fake_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = fake_run.parent.parent / "changed.csv"
    changed.write_bytes((fake_run.parent.parent / "out.csv").read_bytes() + b"extra\r\n")
    monkeypatch.setattr(cli, "DEMO_RUNS", _demo_runs(fake_run, changed, monkeypatch))

    result = invoke(["demo", "--lang", "en"])

    assert result.exit_code == 1
    assert "MISMATCH" in result.stdout


def test_demo_names_the_committed_lockbox_runs() -> None:
    assert cli.DEMO_RUNS["en"] == cli.DemoRun(
        Path("runs/submission/20261008T023928Z-56f85fb8"), Path("output/improved_output_en.csv")
    )
    assert cli.DEMO_RUNS["fr"] == cli.DemoRun(
        Path("runs/submission/20261008T024244Z-de394c39"), Path("output/improved_output_fr.csv")
    )


def test_demo_on_the_committed_runs_is_byte_identical(repo_root: Path) -> None:
    result = invoke(["demo"])

    assert result.exit_code == 0, result.output
    assert "319 rows" in result.stdout
    assert result.stdout.count("byte-identical") >= 2


# explain, review findings on the text rendering


def _record_run(workdir: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> Path:
    output = workdir / "out.csv"
    args = ["--input", str(SMALL_BOQ), "--library", str(GLOBAL_LIBRARY), "--output", str(output)]
    invoke([*args, "--llm", "fake", *extra])
    folders = sorted((workdir / "runs").iterdir())
    assert len(folders) == 1
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(workdir, NoModel()))
    return folders[0]


@pytest.fixture
def failing_run(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from oris_matcher.llm.fake_llm import Fault, FaultKind  # noqa: PLC0415

    def always_500(number: int, request: Any) -> Fault:
        del number, request
        return Fault(FaultKind.SERVER_ERROR)

    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(rule=always_500))
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(workdir, lambda spec, model: fake))
    return _record_run(workdir, monkeypatch)


def _first_routed_item(run: Path) -> dict[str, Any]:
    return next(record for record in audit_of(run) if record.get("call_ids"))


def test_explain_text_shows_each_calls_status_attempt_and_no_valid_answer(
    failing_run: Path,
) -> None:
    record = _first_routed_item(failing_run)
    calls = {
        json.loads(text)["call_id"]: json.loads(text)
        for text in (failing_run / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    }

    text = explain(failing_run, record["item_no"]).stdout

    assert "HTTP 500" in text
    for call_id in record["call_ids"]:
        source = calls[call_id]
        assert str(source["error_class"]) in text
        assert f"attempt {source['attempt_no']}" in text
    assert "no valid answer was received" in text


def test_explain_text_states_the_threshold_as_a_lexicographic_minimum(fake_run: Path) -> None:
    record = matched_record(fake_run)

    text = explain(fake_run, record["item_no"]).stdout

    assert "s >= (v 2, b no_evidence, confidence <70)" in text
    assert "lexicographically" in text
    assert "confidence <70\n" not in text


def test_explain_text_says_the_verifier_was_asked_and_whether_it_agrees(fake_run: Path) -> None:
    record = matched_record(fake_run)
    raw = json.loads(record["verifier_raw"])

    text = explain(fake_run, record["item_no"]).stdout

    verifier = next(line for line in text.splitlines() if line.startswith("verifier:"))
    assert "asked" in verifier
    assert "agrees" in verifier
    assert "flagged True" not in verifier
    assert repr(raw["evidence"]) in verifier


def test_explain_text_says_not_asked_for_a_line_the_verifier_skipped(fake_run: Path) -> None:
    record = next(
        item
        for item in audit_of(fake_run)
        if item.get("call_ids") and item.get("verifier_flagged") is False
    )

    text = explain(fake_run, record["item_no"]).stdout

    verifier = next(line for line in text.splitlines() if line.startswith("verifier:"))
    assert "not asked" in verifier
    assert "flagged False" not in verifier


def test_explain_json_carries_each_calls_raw_response(fake_run: Path) -> None:
    record = matched_record(fake_run)
    recorded = {
        json.loads(text)["call_id"]: json.loads(text)
        for text in (fake_run / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    }

    payload = json.loads(explain(fake_run, record["item_no"], "--json").stdout)

    for call in payload["calls"]:
        assert call["raw_response"] == recorded[call["call_id"]]["raw_response"]


def test_explain_full_prints_each_calls_user_message_and_raw_response(fake_run: Path) -> None:
    record = matched_record(fake_run)
    recorded = {
        json.loads(text)["call_id"]: json.loads(text)
        for text in (fake_run / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    }

    short = explain(fake_run, record["item_no"]).stdout
    full = explain(fake_run, record["item_no"], "--full")

    assert full.exit_code == 0, full.output
    assert "--full" in short
    for call_id in record["call_ids"]:
        assert recorded[call_id]["raw_response"] in full.stdout
        assert recorded[call_id]["user_message"] in full.stdout


def test_explain_a_b2_match_does_not_cite_a_score_threshold(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _record_run(workdir, monkeypatch, "--profile", "b2")
    record = matched_record(run)

    text = explain(run, record["item_no"]).stdout

    assert "B2 matches every valid answer" in text
    assert "at or above the frozen threshold" not in text


def test_explain_a_b0_line_does_not_cite_a_score_threshold(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _record_run(workdir, monkeypatch, "--profile", "b0")
    record = next(item for item in audit_of(run) if item["rule"] == "D10")

    text = explain(run, record["item_no"]).stdout

    assert "rules-only floor" in text
    assert "below the threshold" not in text


# demo, review findings


def test_demo_writes_nothing_anywhere_under_the_working_root(
    fake_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fake_run.parent.parent
    monkeypatch.setattr(cli, "DEMO_RUNS", _demo_runs(fake_run, root / "out.csv", monkeypatch))
    before = tree_digest(root)

    result = invoke(["demo"])

    assert result.exit_code == 0, result.output
    assert tree_digest(root) == before


def test_demo_on_the_committed_runs_adds_no_run_folder(repo_root: Path) -> None:
    runs = repo_root / "runs"
    before = sorted(path.relative_to(runs).as_posix() for path in runs.rglob("*"))

    result = invoke(["demo"])

    assert result.exit_code == 0, result.output
    assert sorted(path.relative_to(runs).as_posix() for path in runs.rglob("*")) == before


def test_demo_note_gives_the_measured_flip_rate_and_where_it_is_recorded(
    fake_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = fake_run.parent.parent / "out.csv"
    monkeypatch.setattr(cli, "DEMO_RUNS", _demo_runs(fake_run, expected, monkeypatch))

    text = invoke(["demo"]).stdout

    assert "0 decision flips in 199 lines" in text
    assert "docs/gates/G3.md" in text
