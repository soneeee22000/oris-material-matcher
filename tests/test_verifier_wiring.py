"""E-08 wiring: the runner's mixed run, ``oris replay``, legacy replays and the API (A65.2).

The measurement replays the main passes of a recorded run at $0 and sends only the verifier
requests to an adapter: ``--llm replay:<run> --verifier-adopted`` (the live primary by default,
``--llm-verifier fake`` here). The arm run's ``calls.jsonl`` then holds the copied main-pass
records and the new verifier records, so a plain replay of it reproduces it byte for byte.
"""

import importlib.util
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from oris_matcher import cli
from oris_matcher.api.app import LineIn, create_app, request_boq
from oris_matcher.doctor import LLMKind, LLMSpec, Runtime, read_manifest
from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.decision import VERIFIER_NONE
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.llm.base import LLMRequest, LLMResult
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM
from oris_matcher.llm.recording import read_calls_jsonl
from oris_matcher.llm.routing import RoutingLLM
from oris_matcher.prompts.v1.verifier import is_verifier_request
from oris_matcher.service import DECISION_PROFILE_KEY, MatchService
from oris_matcher.settings import Settings, load_models_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
GLOBAL_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
FR_LIBRARY = ROOT / "data" / "oris_materials_fr.csv"
LIBRARIES = {"global": GLOBAL_LIBRARY, "fr": FR_LIBRARY}
EN_INPUT = ROOT / "input" / "boq_dataset_input_en.csv"
SPLIT = ROOT / "eval" / "split_v1.json"
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
HAIKU = "claude-haiku-4-5-20251001"
FIXED_NOW = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
ENV_FLAG = "ORIS_VERIFIER_ADOPTED"
ENV_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ORIS_PRIMARY_MODEL", ENV_FLAG)
LIMIT = "24"
API_LINES = 30
OFF_PROFILE = {"drop_conflicting_votes": False}
ON_PROFILE = {"drop_conflicting_votes": False, "verifier_adopted": True}


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load("oris_eval_run_experiment_e08", ROOT / "eval" / "run_experiment.py")


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "config_dir": CONFIG,
        "libraries": LIBRARIES,
        "anthropic_api_key": None,
        "openai_api_key": None,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _main_answers() -> dict[str, dict[str, Any]]:
    from test_service import answers_for  # noqa: PLC0415

    service = MatchService.from_settings(make_settings())
    return answers_for(read_boq(EN_INPUT), service.library("global"))


MAIN_ANSWERS = _main_answers()


class LivePort:
    """A port that is neither FakeLLM nor ReplayLLM, as a live adapter is; it keeps its calls."""

    def __init__(self, behaviour: FakeBehaviour | None = None) -> None:
        self.inner = FakeLLM(HAIKU, ALLOWLIST, behaviour or FakeBehaviour(answers=MAIN_ANSWERS))
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest) -> LLMResult:
        self.calls.append(req)
        return await self.inner.complete(req)


class Factory:
    """Hands out one adapter per spec kind and keeps them."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.specs: list[LLMSpec] = []

    def __call__(self, spec: LLMSpec, model: str) -> Any:
        del model
        self.specs.append(spec)
        return self.adapter


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
    return "e" * 40 if "rev-parse" in args else ""


async def _no_sleep(seconds: float) -> None:
    del seconds


def make_runtime(tmp_path: Path, factory: Factory | None = None) -> Runtime:
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
        "--limit",
        LIMIT,
        "--llm",
        llm,
        "--id",
        run_id,
        "--hypothesis",
        "a sibling verifier removes usage confusers from the matches",
        "--change",
        "E-08",
        "--runs-dir",
        str(tmp_path / "runs"),
        "--ledger",
        str(tmp_path / "experiments.jsonl"),
        *extra,
    ]


def ledger_rows(tmp_path: Path) -> list[dict[str, Any]]:
    text = (tmp_path / "experiments.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _run(
    tmp_path: Path, llm: str, run_id: str, *extra: str, factory: Factory | None = None
) -> Path:
    code = runner.main(argv(tmp_path, llm, run_id, *extra), make_runtime(tmp_path, factory))
    assert code == 0
    return tmp_path / "runs" / ledger_rows(tmp_path)[-1]["run_id"]


def _source(tmp_path: Path, *, live: bool = False) -> Path:
    """Record a verifier-off B3 run: fake, or through a non-fake port so its mode is live."""
    if live:
        return _run(tmp_path, "anthropic", "B3-src", factory=Factory(LivePort()))
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=MAIN_ANSWERS))
    return _run(tmp_path, "fake", "B3-src", factory=Factory(fake))


def _calls(folder: Path) -> list[Any]:
    return read_calls_jsonl(folder / "calls.jsonl")


def _is_verifier(record: Any) -> bool:
    return '"candidates"' in record.user_message


def _audit(folder: Path) -> list[dict[str, Any]]:
    text = (folder / "audit.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


# The mixed run


def test_the_mixed_run_copies_the_main_passes_and_records_new_verifier_calls(
    isolated: Path,
) -> None:
    source = _source(isolated)
    arm = _run(
        isolated,
        f"replay:{source.as_posix()}",
        "E-08",
        "--verifier-adopted",
        "--llm-verifier",
        "fake",
    )
    manifest = read_manifest(arm)
    assert manifest[DECISION_PROFILE_KEY] == ON_PROFILE
    assert manifest["llm_verifier"] == "fake"
    assert manifest["mode"] == "fake"
    assert manifest["source_run_id"] == read_manifest(source)["run_id"]
    source_calls, arm_calls = _calls(source), _calls(arm)
    main = [record for record in arm_calls if not _is_verifier(record)]
    verifier = [record for record in arm_calls if _is_verifier(record)]
    assert verifier
    assert all(record.cache_hit for record in main)
    assert not any(record.cache_hit for record in verifier)
    assert sorted(record.request_sha256 for record in main) == sorted(
        record.request_sha256 for record in source_calls
    )
    assert {record.source_call_id for record in main} == {record.call_id for record in source_calls}
    assert manifest["spend_usd"] == pytest.approx(sum(record.cost_usd for record in verifier))
    assert manifest["verifier"]["call_count"] == len(verifier)


def test_the_mixed_run_decides_unflagged_lines_as_the_source_did(isolated: Path) -> None:
    source = _source(isolated)
    arm = _run(
        isolated,
        f"replay:{source.as_posix()}",
        "E-08",
        "--verifier-adopted",
        "--llm-verifier",
        "fake",
    )
    before = {record["line_id"]: record for record in _audit(source)}
    flagged = 0
    for record in _audit(arm):
        if record["verifier_flagged"]:
            flagged += 1
            assert before[record["line_id"]]["rule"] == "D9"
            continue
        assert record["reason"] == before[record["line_id"]]["reason"]
    assert flagged == sum(record["rule"] == "D9" for record in before.values())
    assert flagged > 0


def test_a_plain_replay_of_the_arm_run_is_byte_identical_at_zero(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(isolated)
    arm = _run(
        isolated,
        f"replay:{source.as_posix()}",
        "E-08",
        "--verifier-adopted",
        "--llm-verifier",
        "fake",
    )
    again = _run(isolated, f"replay:{arm.as_posix()}", "E-08-again")
    assert read_manifest(again)[DECISION_PROFILE_KEY] == ON_PROFILE
    assert read_manifest(again)["mode"] == "replay"
    assert read_manifest(again)["spend_usd"] == 0
    assert (again / "output.csv").read_bytes() == (arm / "output.csv").read_bytes()
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(isolated))
    result = CliRunner().invoke(cli.app, ["replay", str(arm), "--check", str(arm / "output.csv")])
    assert result.exit_code == 0, result.output
    assert "byte-identical" in result.stdout


def test_the_mixed_run_with_a_live_verifier_is_cached_and_spends_only_on_the_verifier(
    isolated: Path,
) -> None:
    source = _source(isolated, live=True)
    assert read_manifest(source)["mode"] == "live"
    port = LivePort()
    factory = Factory(port)
    arm = _run(
        isolated, f"replay:{source.as_posix()}", "E-08", "--verifier-adopted", factory=factory
    )
    manifest = read_manifest(arm)
    assert [spec.kind for spec in factory.specs] == [LLMKind.ANTHROPIC]
    assert port.calls
    assert all(is_verifier_request(request) for request in port.calls)
    assert manifest["mode"] == "cached"
    assert manifest["source_run_id"] == read_manifest(source)["run_id"]
    assert manifest["llm_verifier"] == "anthropic"
    verifier = [record for record in _calls(arm) if _is_verifier(record)]
    assert len(verifier) == len(port.calls)
    assert manifest["spend_usd"] == pytest.approx(sum(record.cost_usd for record in verifier))
    assert manifest["cache_hits"] == len(_calls(source))


def test_a_live_verifier_is_refused_over_a_fake_source(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(isolated)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")
    code = runner.main(
        argv(isolated, f"replay:{source.as_posix()}", "E-08", "--verifier-adopted"),
        make_runtime(isolated),
    )
    assert code == 2


def test_llm_verifier_without_a_mixed_run_is_refused(isolated: Path) -> None:
    code = runner.main(
        argv(isolated, "fake", "E-08", "--llm-verifier", "fake"), make_runtime(isolated)
    )
    assert code == 2


# Replays of runs recorded without the verifier


def test_a_verifier_off_run_replays_unchanged(isolated: Path) -> None:
    source = _source(isolated)
    again = _run(isolated, f"replay:{source.as_posix()}", "B3-again")
    manifest = read_manifest(again)
    assert manifest[DECISION_PROFILE_KEY] == OFF_PROFILE
    assert "verifier" not in manifest
    assert not any(_is_verifier(record) for record in _calls(again))
    assert (again / "output.csv").read_bytes() == (source / "output.csv").read_bytes()
    assert all("verifier_flagged" not in record for record in _audit(again))


def test_the_environment_flag_does_not_change_a_replay_of_a_verifier_off_run(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(isolated)
    monkeypatch.setenv(ENV_FLAG, "true")
    settings = cli.settings_from_manifest(read_manifest(source))
    assert settings.verifier_adopted is False
    job = cli.replay_job(source, settings, None, isolated)
    prepared = cli.prepare_run(job, settings, make_runtime(isolated))
    assert prepared.service.resources.settings.verifier_adopted is False
    assert not isinstance(prepared.adapter, RoutingLLM)


def test_an_explicit_job_value_makes_a_replay_mixed(isolated: Path) -> None:
    source = _source(isolated)
    job = cli.MatchJob(
        input_path=EN_INPUT,
        library_path=GLOBAL_LIBRARY,
        llm=LLMSpec(LLMKind.REPLAY, run_dir=source),
        verifier_adopted=True,
        llm_verifier=LLMSpec(LLMKind.FAKE),
    )
    prepared = cli.prepare_run(job, Settings(), make_runtime(isolated))
    assert prepared.service.resources.settings.verifier_adopted is True
    assert isinstance(prepared.adapter, RoutingLLM)
    assert prepared.adapter.main.strict  # type: ignore[attr-defined]


# The API decides as the CLI does


def _api_lines() -> list[dict[str, Any]]:
    boq = read_boq(EN_INPUT)
    items = boq.lines[:API_LINES]
    return [
        {
            "item_no": line.item_no,
            "short_description": line.short,
            "long_description": line.long,
            "unit": line.unit,
            "qty": line.qty,
        }
        for line in items
    ]


def _api(tmp_path: Path, flag: bool) -> tuple[list[dict[str, Any]], dict[str, Any], list[Any]]:
    from test_service import answers_for  # noqa: PLC0415

    lines = _api_lines()
    boq = request_boq([LineIn.model_validate(line) for line in lines])
    service = MatchService.from_settings(make_settings())
    answers = answers_for(boq, service.library("global"))
    verdicts = {key: {"id": key, "evidence": "x", "code": VERIFIER_NONE} for key in answers}
    behaviour = FakeBehaviour(answers=answers, verifier_answers=verdicts)

    def factory(model: str) -> FakeLLM:
        return FakeLLM(model, ALLOWLIST, behaviour)

    runtime = Runtime(root=lambda: tmp_path, sleep=_no_sleep, git=_git)
    client = TestClient(create_app(make_settings(verifier_adopted=flag), factory, runtime=runtime))
    body = client.post("/v1/match", json={"library": "global", "lines": lines}).json()
    folder = tmp_path / "runs" / body["run_id"]
    return body["decisions"], read_manifest(folder), _calls(folder)


def test_the_api_runs_the_verifier_from_its_settings(tmp_path: Path) -> None:
    on_rows, on_manifest, on_calls = _api(tmp_path / "on", True)
    off_rows, off_manifest, off_calls = _api(tmp_path / "off", False)
    assert on_manifest[DECISION_PROFILE_KEY] == ON_PROFILE
    assert off_manifest[DECISION_PROFILE_KEY] == OFF_PROFILE
    assert any(_is_verifier(record) for record in on_calls)
    assert not any(_is_verifier(record) for record in off_calls)
    matched_off = [row for row in off_rows if row["decision"] == "matched"]
    assert matched_off
    for on, off in zip(on_rows, off_rows, strict=True):
        if off["decision"] == "matched":
            assert on["reason"] == "VERIFIER_DISAGREES"
        else:
            assert on["reason"] == off["reason"]


def test_transport_ids_tie_verifier_calls_to_their_lines(isolated: Path) -> None:
    source = _source(isolated)
    arm = _run(
        isolated,
        f"replay:{source.as_posix()}",
        "E-08",
        "--verifier-adopted",
        "--llm-verifier",
        "fake",
    )
    boq = read_boq(EN_INPUT)
    by_line = {line.line_id: transport_id(line) for line in boq.lines}
    asked = {
        line_id for record in _calls(arm) if _is_verifier(record) for line_id in record.line_ids
    }
    flagged = {by_line[record["line_id"]] for record in _audit(arm) if record["verifier_flagged"]}
    assert asked == flagged


# A mixed run is a B3 run: B0 and B2 never run the verifier


def _b2_source(tmp_path: Path) -> Path:
    """Record a fake B2 run."""
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=MAIN_ANSWERS))
    return _run(tmp_path, "fake", "B2-src", "--profile", "b2", factory=Factory(fake))


def test_a_b2_replay_with_the_verifier_adopted_stays_a_plain_replay(isolated: Path) -> None:
    source = _b2_source(isolated)
    again = _run(
        isolated, f"replay:{source.as_posix()}", "B2-again", "--profile", "b2", "--verifier-adopted"
    )
    manifest = read_manifest(again)
    assert manifest["mode"] == "replay"
    assert "llm_verifier" not in manifest
    assert not any(_is_verifier(record) for record in _calls(again))
    assert (again / "output.csv").read_bytes() == (source / "output.csv").read_bytes()


def test_a_b2_replay_job_with_the_verifier_adopted_builds_no_routing_port(isolated: Path) -> None:
    source = _b2_source(isolated)
    job = cli.MatchJob(
        input_path=EN_INPUT,
        library_path=GLOBAL_LIBRARY,
        llm=LLMSpec(LLMKind.REPLAY, run_dir=source),
        profile=cli.RunProfile.B2,
        verifier_adopted=True,
    )
    prepared = cli.prepare_run(job, Settings(), make_runtime(isolated))
    assert prepared.verifier_spec is None
    assert not isinstance(prepared.adapter, RoutingLLM)


def test_llm_verifier_on_a_b2_replay_is_refused_before_any_call(
    isolated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _b2_source(isolated)
    capsys.readouterr()
    extra = ("--profile", "b2", "--verifier-adopted", "--llm-verifier", "fake")
    code = runner.main(
        argv(isolated, f"replay:{source.as_posix()}", "E-08", *extra), make_runtime(isolated)
    )
    assert code == 2
    assert "--llm-verifier applies only" in capsys.readouterr().err


# A live verifier needs measured main passes at the root of the replay chain


def test_a_live_verifier_is_refused_over_a_replay_of_a_fake_run(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source(isolated)
    replayed = _run(isolated, f"replay:{source.as_posix()}", "B3-again")
    assert read_manifest(replayed)["mode"] == "replay"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-used")
    port = LivePort()
    capsys.readouterr()
    code = runner.main(
        argv(isolated, f"replay:{replayed.as_posix()}", "E-08", "--verifier-adopted"),
        make_runtime(isolated, Factory(port)),
    )
    assert code == 2
    assert "fake" in capsys.readouterr().err
    assert port.calls == []


def test_a_live_verifier_is_allowed_over_a_replay_of_a_live_run(isolated: Path) -> None:
    source = _source(isolated, live=True)
    replayed = _run(isolated, f"replay:{source.as_posix()}", "B3-dev-sel")
    port = LivePort()
    arm = _run(
        isolated,
        f"replay:{replayed.as_posix()}",
        "E-08",
        "--verifier-adopted",
        factory=Factory(port),
    )
    manifest = read_manifest(arm)
    assert port.calls
    assert all(is_verifier_request(request) for request in port.calls)
    assert manifest["mode"] == "cached"
    assert manifest["source_run_id"] == read_manifest(replayed)["run_id"]


# The cache serves the source's main passes only when that equals replaying them


def _rewrite_calls(folder: Path, change: Any) -> None:
    """Rewrite a run's calls.jsonl through ``change``, a function of the list of records."""
    path = folder / "calls.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    text = "".join(json.dumps(record) + "\n" for record in change(records))
    path.write_text(text, encoding="utf-8", newline="")


def _failed_first(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failed = {**records[0], "http_status": 500, "error_class": "APIStatusError"}
    return [failed, *records[1:]]


def _duplicated_first(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [*records, {**records[0], "call_id": "f" * 16}]


def _declined(folder: Path) -> None:
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    first = _calls(folder)[0]
    manifest["declined_attempts"] = [
        {
            "request_sha256": first.request_sha256,
            "parent_call_id": first.call_id,
            "attempt_no": 2,
            "reason": "LLM_UNAVAILABLE",
        }
    ]
    path.write_text(json.dumps(manifest), encoding="utf-8", newline="")


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(lambda folder: _rewrite_calls(folder, _failed_first), id="failed-attempt"),
        pytest.param(lambda folder: _rewrite_calls(folder, _duplicated_first), id="repeated-hash"),
        pytest.param(_declined, id="declined-attempt"),
    ],
)
def test_a_source_the_cache_cannot_serve_exactly_is_refused(
    isolated: Path, capsys: pytest.CaptureFixture[str], tamper: Any
) -> None:
    source = _source(isolated)
    tamper(source)
    fake = FakeLLM(HAIKU, ALLOWLIST)
    capsys.readouterr()
    extra = ("--verifier-adopted", "--llm-verifier", "fake")
    code = runner.main(
        argv(isolated, f"replay:{source.as_posix()}", "E-08", *extra),
        make_runtime(isolated, Factory(fake)),
    )
    assert code == 2
    assert "cannot be served exactly" in capsys.readouterr().err
    assert fake.calls == []
