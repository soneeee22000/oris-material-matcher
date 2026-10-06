"""A64 wiring: setting, manifest, service, API, ``oris replay`` and the experiment runner."""

import asyncio
import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner, Result

from oris_matcher import cli
from oris_matcher.api.app import LineIn, create_app, request_boq
from oris_matcher.doctor import LLMKind, LLMSpec, Runtime, read_manifest
from oris_matcher.domain.attributes import AttrResult, compare, extract
from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.boq import BoqFile, BoqLine, LineKind
from oris_matcher.domain.decision import DecisionProfile, ReasonCode, Rule
from oris_matcher.domain.library import Library
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.llm.base import LLMRequest, LLMResult, SystemBlock
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, default_answer
from oris_matcher.llm.recording import MemoryCallSink
from oris_matcher.llm.wrapper import BudgetLedger, LLMWrapper, WrapperDeps
from oris_matcher.service import (
    DECISION_PROFILE_KEY,
    MatchService,
    RunOptions,
    RunProfile,
    RunResult,
    decision_profile_fields,
    recorded_decision_profile,
)
from oris_matcher.settings import Settings, load_models_config, load_pricing

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
GLOBAL_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
FR_LIBRARY = ROOT / "data" / "oris_materials_fr.csv"
LIBRARIES = {"global": GLOBAL_LIBRARY, "fr": FR_LIBRARY}
EN_INPUT = ROOT / "input" / "boq_dataset_input_en.csv"
SPLIT = ROOT / "eval" / "split_v1.json"
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
PRICING = load_pricing(CONFIG / "pricing.toml")
HAIKU = "claude-haiku-4-5-20251001"
FIXED_NOW = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
CONFIDENCE = 95
SPLIT_LINES = 3
ENV_FLAG = "ORIS_DROP_CONFLICTING_VOTES"
ENV_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ORIS_PRIMARY_MODEL", ENV_FLAG)
SPLIT_IDS = frozenset(json.loads(SPLIT.read_text(encoding="utf-8"))["item_ids_dev"])


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load("oris_eval_run_experiment_a64", ROOT / "eval" / "run_experiment.py")


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "config_dir": CONFIG,
        "libraries": LIBRARIES,
        "anthropic_api_key": None,
        "openai_api_key": None,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


GLOBAL = MatchService.from_settings(make_settings()).library("global")


def _split_codes(line: BoqLine, library: Library) -> tuple[str, str] | None:
    """Return (conflicting, agreeing) codes for a line with a stated hard attribute, if any."""
    attributes = extract(f"{line.short} {line.long}")
    usable = [row for row in library.rows if not library.is_never_match(row.code)]
    results = [(row, compare(attributes, row.attributes)) for row in usable]
    agreeing = [row for row, result in results if result == AttrResult.AGREE]
    specific = [row for row in agreeing if not row.is_blank_leaf]
    conflicting = [row for row, result in results if result == AttrResult.CONFLICT]
    if not specific or not conflicting:
        return None
    return conflicting[0].code, specific[0].code


def _answer(line: BoqLine, top1: str, top2: str) -> dict[str, Any]:
    words = line.short.split()
    return {
        **default_answer(transport_id(line)),
        "evidence": words[0] if words else "",
        "top1": top1,
        "top2": top2,
        "confidence": CONFIDENCE,
    }


def split_answers(
    lines: Sequence[BoqLine], library: Library
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    """Pass 1 picks a conflicting sibling, pass 2 the agreeing row, for every split line.

    Returns:
        The two passes' answers by transport id, and line id -> the agreeing code.

    """
    first: dict[str, dict[str, Any]] = {}
    second: dict[str, dict[str, Any]] = {}
    agreeing: dict[str, str] = {}
    for line in lines:
        codes = _split_codes(line, library) if line.kind == LineKind.ITEM else None
        if codes is None:
            continue
        conflict, agree = codes
        first[transport_id(line)] = _answer(line, conflict, agree)
        second[transport_id(line)] = _answer(line, agree, conflict)
        agreeing[line.line_id] = agree
    return first, second, agreeing


class PerPassFake(FakeLLM):
    """A FakeLLM whose canned answers depend on the pass, told apart by the system blocks."""

    def __init__(self, model: str, per_pass: Sequence[Mapping[str, Mapping[str, Any]]]) -> None:
        super().__init__(model, ALLOWLIST)
        self.per_pass = per_pass
        self.renderings: list[tuple[SystemBlock, ...]] = []

    async def complete(self, req: LLMRequest) -> LLMResult:
        if req.system_blocks not in self.renderings:
            self.renderings.append(req.system_blocks)
        index = self.renderings.index(req.system_blocks)
        self.behaviour = FakeBehaviour(answers=self.per_pass[index])
        return await super().complete(req)


def _en_split() -> tuple[BoqFile, list[BoqLine], dict[str, str], PerPassFake]:
    boq = read_boq(EN_INPUT)
    items = [line for line in boq.lines if line.kind == LineKind.ITEM]
    first, second, agreeing = split_answers(items, GLOBAL)
    chosen = [line for line in items if line.line_id in agreeing][:SPLIT_LINES]
    assert len(chosen) == SPLIT_LINES
    return boq, chosen, agreeing, PerPassFake(HAIKU, (first, second))


async def _no_sleep(seconds: float) -> None:
    del seconds


def _run(service: MatchService, boq: BoqFile, chosen: list[BoqLine], fake: FakeLLM) -> RunResult:
    deps = WrapperDeps(sink=MemoryCallSink(), sleep=_no_sleep, now=lambda: FIXED_NOW)
    wrapper = LLMWrapper(fake, PRICING, BudgetLedger(100.0), None, deps)
    options = RunOptions(select=frozenset(line.line_id for line in chosen), run_id="a64")
    return asyncio.run(
        service.match(boq, "global", profile=RunProfile.B3, llm=wrapper, options=options)
    )


# Settings and the manifest fields


def test_setting_defaults_off_and_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert make_settings().drop_conflicting_votes is False
    monkeypatch.setenv(ENV_FLAG, "true")
    assert make_settings().drop_conflicting_votes is True
    monkeypatch.setenv(ENV_FLAG, "0")
    assert make_settings().drop_conflicting_votes is False


def test_service_builds_its_decision_profile_from_settings() -> None:
    on = MatchService.from_settings(make_settings(drop_conflicting_votes=True))
    off = MatchService.from_settings(make_settings())
    assert on.resources.decision_profile == DecisionProfile(drop_conflicting_votes=True)
    assert off.resources.decision_profile == DecisionProfile()


@pytest.mark.parametrize("flag", [False, True])
def test_manifest_fields_round_trip(flag: bool) -> None:
    profile = DecisionProfile(drop_conflicting_votes=flag)
    fields = decision_profile_fields(profile)
    assert fields == {DECISION_PROFILE_KEY: {"drop_conflicting_votes": flag}}
    assert recorded_decision_profile(json.loads(json.dumps(fields))) == profile


def test_a_manifest_recorded_before_a64_reads_back_off() -> None:
    assert recorded_decision_profile({}) == DecisionProfile()
    assert recorded_decision_profile({DECISION_PROFILE_KEY: None}) == DecisionProfile()
    assert recorded_decision_profile({DECISION_PROFILE_KEY: {}}) == DecisionProfile()


# The service re-decides the split lines


@pytest.mark.parametrize("flag", [False, True])
def test_service_records_the_flag_in_its_manifest(flag: bool) -> None:
    boq, chosen, _, fake = _en_split()
    service = MatchService.from_settings(make_settings(drop_conflicting_votes=flag))
    result = _run(service, boq, chosen, fake)
    assert result.manifest[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": flag}


def test_service_drops_the_conflicting_pass_only_when_the_flag_is_on() -> None:
    boq, chosen, agreeing, fake = _en_split()
    off = _run(MatchService.from_settings(make_settings()), boq, chosen, fake)
    _, _, _, fake_again = _en_split()
    on_service = MatchService.from_settings(make_settings(drop_conflicting_votes=True))
    on = _run(on_service, boq, chosen, fake_again)
    for before, after in zip(off.lines, on.lines, strict=True):
        assert (before.decision.rule, before.decision.reason) == (
            Rule.D7,
            ReasonCode.ATTR_CONFLICT,
        )
        assert (after.decision.rule, after.decision.reason) == (Rule.D10, "LOW_SIGNAL:v")
        assert after.decision.top1 == agreeing[after.line.line_id]
        assert after.call_ids == before.call_ids


# The API path


def _api_rows(flag: bool, tmp_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _, chosen, _, _ = _en_split()
    lines = [
        {
            "item_no": line.item_no,
            "short_description": line.short,
            "long_description": line.long,
            "unit": line.unit,
            "qty": line.qty,
        }
        for line in chosen
    ]
    request = request_boq_for(lines)
    first, second, _ = split_answers(request.lines, GLOBAL)

    def factory(model: str) -> FakeLLM:
        return PerPassFake(model, (first, second))

    runtime = Runtime(root=lambda: tmp_path, sleep=_no_sleep, git=lambda args, root: "a" * 40)
    settings = make_settings(drop_conflicting_votes=flag)
    client = TestClient(create_app(settings, factory, runtime=runtime))
    body = client.post("/v1/match", json={"library": "global", "lines": lines}).json()
    manifest = read_manifest(tmp_path / "runs" / body["run_id"])
    return body["decisions"], manifest


def request_boq_for(lines: list[dict[str, Any]]) -> BoqFile:
    """Build the BoQ the API builds from these request lines."""
    return request_boq([LineIn.model_validate(line) for line in lines])


@pytest.mark.parametrize(("flag", "reason"), [(False, "ATTR_CONFLICT"), (True, "LOW_SIGNAL:v")])
def test_api_path_behaves_as_the_cli(flag: bool, reason: str, tmp_path: Path) -> None:
    rows, manifest = _api_rows(flag, tmp_path)
    assert manifest[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": flag}
    assert {row["reason"] for row in rows} == {reason}


# oris replay and the experiment runner read the recorded flag back


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ORIS_CONFIG_DIR", str(CONFIG))
    libraries = {"global": str(GLOBAL_LIBRARY), "fr": str(FR_LIBRARY)}
    monkeypatch.setenv("ORIS_LIBRARIES", json.dumps(libraries))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _git(args: Sequence[str], root: Path) -> str:
    del root
    return "d" * 40 if "rev-parse" in args else ""


def make_runtime(tmp_path: Path, fake: FakeLLM | None = None) -> Runtime:
    factory = None if fake is None else (lambda spec, model: fake)
    return Runtime(
        root=lambda: tmp_path,
        clock=lambda: FIXED_NOW,
        sleep=_no_sleep,
        git=_git,
        adapter_factory=factory,
    )


def argv(tmp_path: Path, llm: str, run_id: str, *extra: str) -> list[str]:
    return [
        "--lang",
        "en",
        "--input",
        str(EN_INPUT),
        "--library",
        str(GLOBAL_LIBRARY),
        "--split",
        str(SPLIT),
        "--llm",
        llm,
        "--id",
        run_id,
        "--hypothesis",
        "a conflicting pass casts no vote",
        "--change",
        "A64",
        "--runs-dir",
        str(tmp_path / "runs"),
        "--ledger",
        str(tmp_path / "experiments.jsonl"),
        *extra,
    ]


def ledger_rows(tmp_path: Path) -> list[dict[str, Any]]:
    text = (tmp_path / "experiments.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _dev_fake() -> PerPassFake:
    boq = read_boq(EN_INPUT)
    items = [line for line in boq.lines if line.kind == LineKind.ITEM and line.item_no in SPLIT_IDS]
    first, second, agreeing = split_answers(items, GLOBAL)
    assert agreeing
    return PerPassFake(HAIKU, (first, second))


def _recorded_run(tmp_path: Path, *extra: str) -> tuple[dict[str, Any], Path]:
    code = runner.main(argv(tmp_path, "fake", "E-rec", *extra), make_runtime(tmp_path, _dev_fake()))
    assert code == 0
    row = ledger_rows(tmp_path)[-1]
    return row, tmp_path / "runs" / row["run_id"]


def _replay(tmp_path: Path, folder: Path, run_id: str, *extra: str) -> dict[str, Any]:
    llm = f"replay:{folder.as_posix()}"
    code = runner.main(argv(tmp_path, llm, run_id, *extra), make_runtime(tmp_path))
    assert code == 0
    return ledger_rows(tmp_path)[-1]


def _manifest(tmp_path: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    return read_manifest(tmp_path / "runs" / row["run_id"])


def test_runner_flag_sets_the_profile_and_the_ledger_records_it(isolated: Path) -> None:
    plain, folder = _recorded_run(isolated)
    assert plain[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": False}
    dropped = _replay(isolated, folder, "E-subtype-drop", "--drop-conflicting-votes")
    assert dropped[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": True}
    assert _manifest(isolated, dropped)[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": True}
    assert dropped["change"] == plain["change"]
    before = _manifest(isolated, plain)["reason_counts"]
    after = _manifest(isolated, dropped)["reason_counts"]
    assert after.get("ATTR_CONFLICT", 0) < before["ATTR_CONFLICT"]
    assert after["LOW_SIGNAL:v"] > before.get("LOW_SIGNAL:v", 0)


def test_runner_replay_without_the_flag_re_decides_as_the_run_did(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_FLAG, "true")
    recorded, folder = _recorded_run(isolated)
    assert recorded[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": True}
    monkeypatch.delenv(ENV_FLAG)
    replayed = _replay(isolated, folder, "E-again")
    assert replayed[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": True}
    original = (folder / "output.csv").read_bytes()
    assert (isolated / replayed["output"]).read_bytes() == original


def test_runner_replay_of_a_run_without_the_field_decides_off(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, folder = _recorded_run(isolated)
    _strip_decision_profile(folder)
    monkeypatch.setenv(ENV_FLAG, "true")
    replayed = _replay(isolated, folder, "E-old")
    assert replayed[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": False}
    assert (isolated / replayed["output"]).read_bytes() == (folder / "output.csv").read_bytes()


def _strip_decision_profile(folder: Path) -> None:
    """Make a run folder look recorded before A64: no ``decision_profile``, no setting."""
    manifest = read_manifest(folder)
    manifest.pop(DECISION_PROFILE_KEY)
    manifest["settings_effective"].pop("drop_conflicting_votes")
    text = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    (folder / "manifest.json").write_text(text, encoding="utf-8")


def _prepared_flag(folder: Path, root: Path) -> bool:
    settings = cli.settings_from_manifest(read_manifest(folder))
    job = cli.replay_job(folder, settings, None, root)
    prepared = cli.prepare_run(job, settings, make_runtime(root))
    return prepared.service.resources.settings.drop_conflicting_votes


def test_oris_replay_honours_the_recorded_flag(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_FLAG, "true")
    _, folder = _recorded_run(isolated)
    monkeypatch.delenv(ENV_FLAG)
    assert _prepared_flag(folder, isolated) is True
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(isolated))
    result = cli_invoke(["replay", str(folder), "--check", str(folder / "output.csv")])
    assert result.exit_code == 0, result.output
    assert "byte-identical" in result.stdout


def test_oris_replay_of_a_run_without_the_field_uses_off(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, folder = _recorded_run(isolated)
    _strip_decision_profile(folder)
    monkeypatch.setenv(ENV_FLAG, "true")
    assert cli.settings_from_manifest(read_manifest(folder)).drop_conflicting_votes is False
    assert _prepared_flag(folder, isolated) is False


def test_an_explicit_job_value_wins_over_the_recorded_one(isolated: Path) -> None:
    _, folder = _recorded_run(isolated)
    job = cli.MatchJob(
        input_path=EN_INPUT,
        library_path=GLOBAL_LIBRARY,
        llm=LLMSpec(LLMKind.REPLAY, run_dir=folder),
        drop_conflicting_votes=True,
    )
    prepared = cli.prepare_run(job, Settings(), make_runtime(isolated))
    assert prepared.service.resources.settings.drop_conflicting_votes is True


def cli_invoke(args: list[str]) -> Result:
    return CliRunner().invoke(cli.app, args)
