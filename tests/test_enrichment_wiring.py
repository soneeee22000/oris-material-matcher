"""A68.4: turning enrichment on, binding it to a policy entry, the manifest and replays."""

import asyncio
import dataclasses
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from oris_matcher import cli
from oris_matcher.doctor import LLMKind, LLMSpec, read_manifest
from oris_matcher.domain.library import load_library
from oris_matcher.enrichment import EnrichmentSources, dump_enrichment, generate
from oris_matcher.io.writer import render_csv
from oris_matcher.llm.base import LLMRequest
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, Fault, FaultKind
from oris_matcher.llm.recording import read_calls_jsonl
from oris_matcher.llm.replay_llm import RecordedRun, ReplayLLM
from oris_matcher.llm.wrapper import WrapperPolicy
from oris_matcher.prompts.v1.render import CANONICAL_V1, REVERSE_V1
from oris_matcher.prompts.v1.version import prompt_version
from oris_matcher.service import (
    MatchService,
    PolicyQuery,
    PolicyResolution,
    RunOptions,
    RunProfile,
    RunResult,
    as_override,
    resolve_policy,
)
from oris_matcher.settings import ConfigError, PolicyConfig, PolicyEntry, Settings
from test_cli import TwoProviderFactory
from test_run_experiment import Git, RecordingFactory
from test_run_experiment import argv as experiment_argv
from test_run_experiment import make_runtime as experiment_runtime
from test_service import GPT, make_wrapper
from test_verifier_service import BOQ, SELECT, _wrapper
from test_verifier_wiring import (
    ALLOWLIST,
    CONFIG,
    EN_INPUT,
    FR_LIBRARY,
    GLOBAL_LIBRARY,
    HAIKU,
    MAIN_ANSWERS,
    Factory,
    argv,
    ledger_rows,
    make_runtime,
    runner,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "enrichment"
SMALL_LIBRARY = ROOT / "tests" / "fixtures" / "prompts" / "small_library.csv"
LIBRARIES = {"global": GLOBAL_LIBRARY, "fr": FR_LIBRARY}
ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "ORIS_PRIMARY_MODEL",
    "ORIS_VERIFIER_ADOPTED",
    "ORIS_ENRICHMENT",
)
ALSO_MARK = " [also: "
GLOBAL_ALSO = "Quicklime [also: chaux vive]"
SESSION = ("--side", "all", "--lockbox-session", "--llm", "anthropic")


def _generated(library: Path, folder: Path, name: str = "global.yaml") -> Path:
    """Generate an enrichment of a library from the fixture term map and supplement."""
    sources = EnrichmentSources(
        term_map=(FIXTURES / "term_map_global.yaml").read_bytes(),
        supplement=(FIXTURES / "supplement_small.yaml").read_bytes(),
        reviewed_by="tests",
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    content = generate(load_library(library.read_bytes()), sources)
    path.write_bytes(dump_enrichment(content).encode("utf-8"))
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "config_dir": CONFIG,
        "libraries": LIBRARIES,
        "verifier_adopted": False,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _service(root: Path | None = None, **overrides: Any) -> MatchService:
    service = MatchService.from_settings(make_settings(**overrides))
    if root is not None:
        service.resources = dataclasses.replace(service.resources, root=root)
    return service


def _fake() -> FakeLLM:
    return FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=MAIN_ANSWERS))


def _match(service: MatchService, fake: FakeLLM, profile: RunProfile = RunProfile.B3) -> RunResult:
    options = RunOptions(select=SELECT, run_id="a68")
    return asyncio.run(
        service.match(BOQ, "global", profile=profile, llm=_wrapper(fake), options=options)
    )


def _library_text(request: LLMRequest) -> str:
    return request.system_blocks[-1].text


@pytest.fixture(autouse=True)
def _no_enrichment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


# Settings and policy entries


def test_the_setting_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_settings().enrichment is None
    monkeypatch.setenv("ORIS_ENRICHMENT", "none")
    assert make_settings().enrichment == Path("none")


def test_an_entry_names_its_enrichment_with_its_hash() -> None:
    entry = PolicyEntry(
        policy_id="T8",
        certified_by="dev_selection",
        enrichment="data/enrichment/global.yaml",
        enrichment_sha256="a" * 64,
    )
    assert entry.enrichment == "data/enrichment/global.yaml"
    assert PolicyEntry(policy_id="T8", certified_by="dev_selection").enrichment is None


@pytest.mark.parametrize(
    "fields",
    [
        {"enrichment": "data/enrichment/global.yaml"},
        {"enrichment_sha256": "a" * 64},
        {"enrichment": "/abs/global.yaml", "enrichment_sha256": "a" * 64},
        {"enrichment": "../outside.yaml", "enrichment_sha256": "a" * 64},
        {"enrichment": "data/global.yaml", "enrichment_sha256": "A" * 64},
    ],
)
def test_an_entry_refuses_a_partial_or_non_portable_binding(fields: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="enrichment"):
        PolicyEntry(policy_id="T8", certified_by="dev_selection", **fields)


def _bound_config(sha: str, path: str = "enrich/global.yaml") -> PolicyConfig:
    library_sha = _sha(GLOBAL_LIBRARY)
    entry = PolicyEntry(
        policy_id="T8", certified_by="dev_selection", enrichment=path, enrichment_sha256=sha
    )
    return PolicyConfig(policies={HAIKU: {library_sha: entry}})


def test_an_exact_hit_and_an_override_carry_the_binding() -> None:
    config = _bound_config("b" * 64)
    exact = resolve_policy(config, PolicyQuery(HAIKU, _sha(GLOBAL_LIBRARY), 2))
    assert (exact.enrichment, exact.enrichment_sha256) == ("enrich/global.yaml", "b" * 64)
    overridden = as_override(exact)
    assert overridden.resolution == PolicyResolution.OVERRIDE
    assert (overridden.enrichment, overridden.enrichment_sha256) == ("enrich/global.yaml", "b" * 64)
    other = resolve_policy(config, PolicyQuery("gpt-4o-mini-2024-07-18", _sha(GLOBAL_LIBRARY), 2))
    assert other.enrichment is None


# The service


def test_a_forced_enrichment_renders_every_pass_and_is_recorded(tmp_path: Path) -> None:
    path = _generated(GLOBAL_LIBRARY, tmp_path)
    fake = _fake()
    result = _match(_service(enrichment=path), fake)
    assert fake.calls
    assert all(GLOBAL_ALSO in _library_text(request) for request in fake.calls)
    assert all("semelle [fr]" in _library_text(request) for request in fake.calls)
    sha = _sha(path)
    assert result.manifest["enrichment_sha256"] == sha
    assert result.enrichment_path == path
    assert result.prompt_versions == (
        prompt_version(CANONICAL_V1, sha),
        prompt_version(REVERSE_V1, sha),
    )
    assert any(ALSO_MARK in text for text in result.system_prompts.values())


def test_without_enrichment_nothing_changes() -> None:
    fake = _fake()
    result = _match(_service(enrichment=Path("none")), fake)
    assert not any(ALSO_MARK in _library_text(request) for request in fake.calls)
    assert result.manifest["enrichment_sha256"] is None
    assert result.enrichment_path is None
    assert result.prompt_versions == (prompt_version(CANONICAL_V1), prompt_version(REVERSE_V1))


@pytest.mark.parametrize("profile", [RunProfile.B2, RunProfile.B0])
def test_b0_and_b2_never_render_enrichment(tmp_path: Path, profile: RunProfile) -> None:
    fake = _fake()
    result = _match(_service(enrichment=_generated(GLOBAL_LIBRARY, tmp_path)), fake, profile)
    assert not any(ALSO_MARK in _library_text(request) for request in fake.calls)
    assert result.manifest["enrichment_sha256"] is None


def test_an_enrichment_of_another_library_is_refused_before_any_call(tmp_path: Path) -> None:
    fake = _fake()
    service = _service(enrichment=_generated(SMALL_LIBRARY, tmp_path))
    with pytest.raises(ConfigError, match="library"):
        _match(service, fake)
    assert fake.calls == []


def _bound(tmp_path: Path, sha: str | None = None) -> MatchService:
    path = _generated(GLOBAL_LIBRARY, tmp_path / "enrich")
    service = _service(root=tmp_path)
    config = _bound_config(sha or _sha(path))
    service.resources = dataclasses.replace(service.resources, policy=config)
    return service


def test_a_run_uses_the_entry_enrichment_after_checking_its_hash(tmp_path: Path) -> None:
    fake = _fake()
    result = _match(_bound(tmp_path), fake)
    assert result.policy.resolution == PolicyResolution.EXACT
    assert result.manifest["enrichment_sha256"] == _sha(tmp_path / "enrich" / "global.yaml")
    assert all(GLOBAL_ALSO in _library_text(request) for request in fake.calls)


def test_an_entry_whose_file_changed_is_a_config_error(tmp_path: Path) -> None:
    fake = _fake()
    with pytest.raises(ConfigError, match="sha256"):
        _match(_bound(tmp_path, sha="c" * 64), fake)
    assert fake.calls == []


def test_none_forces_the_entry_enrichment_off(tmp_path: Path) -> None:
    service = _bound(tmp_path)
    settings = service.resources.settings.model_copy(update={"enrichment": Path("none")})
    service.resources = dataclasses.replace(service.resources, settings=settings)
    fake = _fake()
    result = _match(service, fake)
    assert result.manifest["enrichment_sha256"] is None
    assert not any(ALSO_MARK in _library_text(request) for request in fake.calls)


def test_a_forced_file_wins_over_the_entry(tmp_path: Path) -> None:
    service = _bound(tmp_path, sha="c" * 64)
    forced = _generated(GLOBAL_LIBRARY, tmp_path / "forced", "other.yaml")
    settings = service.resources.settings.model_copy(update={"enrichment": forced})
    service.resources = dataclasses.replace(service.resources, settings=settings)
    result = _match(service, _fake())
    assert result.manifest["enrichment_sha256"] == _sha(forced)


# The A33 fallback renders the enrichment of the entry it decides under (A68.4, as in A67)


def _always_down(call_no: int, req: LLMRequest) -> Fault:
    del call_no, req
    return Fault(FaultKind.SERVER_ERROR)


def _backup() -> FakeLLM:
    return FakeLLM(GPT, ALLOWLIST, FakeBehaviour(answers=MAIN_ANSWERS))


def _rescued(service: MatchService, backup: Any, primary: Any = None) -> RunResult:
    primary = primary or FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(rule=_always_down))
    wrapper = make_wrapper(primary, policy=WrapperPolicy(max_retries=0, breaker_threshold=1))
    options = RunOptions(select=SELECT, run_id="a68", fallback=backup)
    return asyncio.run(
        service.match(BOQ, "global", profile=RunProfile.B3, llm=wrapper, options=options)
    )


def _versions(sha: str | None = None) -> list[str]:
    return [prompt_version(CANONICAL_V1, sha), prompt_version(REVERSE_V1, sha)]


def _assert_rescued_versions(result: RunResult, primary: list[str], fallback: list[str]) -> None:
    """Each pass of a rescued line carries the version of whichever model it was sent to."""
    served = [item.prompt_version.split(";") for item in result.lines if item.model == GPT]
    assert served
    for versions in served:
        assert all(
            version in pair
            for version, pair in zip(versions, zip(primary, fallback, strict=True), strict=True)
        )
    assert any(
        version == fallback[index] != primary[index]
        for versions in served
        for index, version in enumerate(versions)
    )


def _replays_identically(service: MatchService, result: RunResult) -> None:
    replay = ReplayLLM(RecordedRun.from_records(result.calls), HAIKU, ALLOWLIST, strict=True)
    replayed = _rescued(service, replay, replay)
    assert render_csv(replayed) == render_csv(result)


def _fallback_bound_config(path: Path) -> PolicyConfig:
    entry = PolicyEntry(
        policy_id="T8",
        certified_by="dev_selection",
        enrichment="enrich/global.yaml",
        enrichment_sha256=_sha(path),
    )
    return PolicyConfig(policies={GPT: {_sha(GLOBAL_LIBRARY): entry}})


def test_a_fallback_without_an_entry_renders_no_enrichment(tmp_path: Path) -> None:
    service = _bound(tmp_path)
    backup = _backup()
    result = _rescued(service, backup)
    assert backup.calls
    assert not any(ALSO_MARK in _library_text(request) for request in backup.calls)
    manifest = result.manifest
    assert manifest["fallback_engaged"] is True
    assert manifest["fallback_policy_resolution"] == "fallback_strictest"
    assert manifest["enrichment_sha256"] == _sha(tmp_path / "enrich" / "global.yaml")
    assert manifest["fallback_enrichment_sha256"] is None
    assert manifest["fallback_prompt_version"] == _versions()
    assert result.fallback_enrichment_path is None
    sha = _sha(tmp_path / "enrich" / "global.yaml")
    _assert_rescued_versions(result, _versions(sha), _versions())
    assert any(ALSO_MARK not in text for text in result.system_prompts.values())
    _replays_identically(service, result)


def test_a_fallback_renders_its_own_entry_enrichment(tmp_path: Path) -> None:
    path = _generated(GLOBAL_LIBRARY, tmp_path / "enrich")
    service = _service(root=tmp_path)
    service.resources = dataclasses.replace(service.resources, policy=_fallback_bound_config(path))
    backup = _backup()
    result = _rescued(service, backup)
    assert all(GLOBAL_ALSO in _library_text(request) for request in backup.calls)
    manifest = result.manifest
    assert manifest["enrichment_sha256"] is None
    assert manifest["fallback_enrichment_sha256"] == _sha(path)
    assert manifest["fallback_prompt_version"] == _versions(_sha(path))
    assert result.fallback_enrichment_path == path
    _assert_rescued_versions(result, _versions(), _versions(_sha(path)))
    assert any(ALSO_MARK in text for text in result.system_prompts.values())
    _replays_identically(service, result)


def test_a_fallback_entry_whose_file_changed_is_refused_before_any_call(tmp_path: Path) -> None:
    path = _generated(GLOBAL_LIBRARY, tmp_path / "enrich")
    service = _service(root=tmp_path)
    service.resources = dataclasses.replace(service.resources, policy=_fallback_bound_config(path))
    path.write_bytes(path.read_bytes() + b"# edited\n")
    primary = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(rule=_always_down))
    with pytest.raises(ConfigError, match="sha256"):
        _rescued(service, _backup(), primary)
    assert primary.calls == []


def test_a_forced_enrichment_renders_the_fallback_too(tmp_path: Path) -> None:
    forced = _generated(GLOBAL_LIBRARY, tmp_path / "forced")
    backup = _backup()
    result = _rescued(_service(enrichment=forced), backup)
    assert all(GLOBAL_ALSO in _library_text(request) for request in backup.calls)
    assert result.manifest["fallback_enrichment_sha256"] == _sha(forced)
    assert result.manifest["enrichment_sha256"] == _sha(forced)


def test_a_run_the_fallback_never_rescued_records_no_fallback_enrichment(tmp_path: Path) -> None:
    result = _rescued(_bound(tmp_path), _backup(), _fake())
    assert result.manifest["fallback_engaged"] is False
    assert result.manifest["fallback_enrichment_sha256"] is None
    assert result.manifest["fallback_prompt_version"] is None
    assert result.fallback_enrichment_path is None


# The CLI and the runner


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ORIS_CONFIG_DIR", str(CONFIG))
    libraries = {"global": str(GLOBAL_LIBRARY), "fr": str(FR_LIBRARY)}
    monkeypatch.setenv("ORIS_LIBRARIES", json.dumps(libraries))
    monkeypatch.setenv("ORIS_VERIFIER_ADOPTED", "false")
    shutil.copytree(ROOT / "data" / "enrichment", tmp_path / "data" / "enrichment")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _fake_factory() -> Factory:
    return Factory(FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=MAIN_ANSWERS)))


def _run(tmp_path: Path, llm: str, run_id: str, *extra: str) -> Path:
    factory = None if llm.startswith("replay:") else _fake_factory()
    runtime = make_runtime(tmp_path, factory)
    code = runner.main(argv(tmp_path, llm, run_id, *extra), runtime)
    assert code == 0
    return tmp_path / "runs" / ledger_rows(tmp_path)[-1]["run_id"]


def _replay_check(tmp_path: Path, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(tmp_path))
    result = CliRunner().invoke(
        cli.app, ["replay", str(folder), "--check", str(folder / "output.csv")]
    )
    assert result.exit_code == 0, result.output
    assert "byte-identical" in result.stdout


def test_the_runner_records_an_external_enrichment_and_copies_it(isolated: Path) -> None:
    path = _generated(GLOBAL_LIBRARY, isolated.parent / f"{isolated.name}-outside")
    folder = _run(isolated, "fake", "E-01-C", "--enrichment", str(path))
    manifest = read_manifest(folder)
    assert manifest["enrichment_sha256"] == _sha(path)
    assert manifest["enrichment_path"] == {"path": "global.yaml", "external": True}
    copy = folder / "external" / "enrichment" / "global.yaml"
    assert copy.read_bytes() == path.read_bytes()
    assert manifest["config_sha256"]["global.yaml"] == _sha(path)
    prompts = [path.read_text(encoding="utf-8") for path in (folder / "prompts").iterdir()]
    assert any(GLOBAL_ALSO in text for text in prompts)


def test_a_replay_re_renders_the_recorded_enrichment(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = isolated.parent / f"{isolated.name}-outside"
    path = _generated(GLOBAL_LIBRARY, outside)
    folder = _run(isolated, "fake", "E-01-C", "--enrichment", str(path))
    shutil.rmtree(outside)
    monkeypatch.setenv("ORIS_ENRICHMENT", "none")
    _replay_check(isolated, folder, monkeypatch)
    again = _run(isolated, f"replay:{folder.as_posix()}", "E-01-C-again")
    assert read_manifest(again)["enrichment_sha256"] == read_manifest(folder)["enrichment_sha256"]
    assert (again / "output.csv").read_bytes() == (folder / "output.csv").read_bytes()


def test_a_replay_of_a_run_without_enrichment_stays_unenriched(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORIS_ENRICHMENT", "none")
    folder = _run(isolated, "fake", "B3-src")
    manifest = read_manifest(folder)
    assert manifest["enrichment_sha256"] is None
    assert manifest["enrichment_path"] is None
    monkeypatch.setenv("ORIS_ENRICHMENT", str(_generated(GLOBAL_LIBRARY, isolated / "e")))
    _replay_check(isolated, folder, monkeypatch)
    settings = cli.settings_from_manifest(manifest)
    job = cli.replay_job(folder, settings, None, isolated)
    prepared = cli.prepare_run(job, settings, make_runtime(isolated))
    assert prepared.service.resources.settings.enrichment == Path("none")


def test_a_legacy_manifest_without_the_keys_replays_unenriched(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORIS_ENRICHMENT", "none")
    folder = _run(isolated, "fake", "B3-src")
    manifest = read_manifest(folder)
    for key in ("enrichment_sha256", "enrichment_path"):
        del manifest[key]
    del manifest["settings_effective"]["enrichment"]
    text = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    (folder / "manifest.json").write_bytes(text.encode("utf-8"))
    monkeypatch.setenv("ORIS_ENRICHMENT", str(_generated(GLOBAL_LIBRARY, isolated / "e")))
    _replay_check(isolated, folder, monkeypatch)


def test_a_replay_refuses_a_recorded_file_that_changed(isolated: Path) -> None:
    path = _generated(GLOBAL_LIBRARY, isolated / "enrich")
    folder = _run(isolated, "fake", "E-01-C", "--enrichment", str(path))
    assert read_manifest(folder)["enrichment_path"] == "enrich/global.yaml"
    path.write_bytes(path.read_bytes() + b"# edited\n")
    code = runner.main(
        argv(isolated, f"replay:{folder.as_posix()}", "again"), make_runtime(isolated)
    )
    assert code == 2


def test_a_policy_file_binding_resolves_against_the_repository_root(isolated: Path) -> None:
    path = _generated(GLOBAL_LIBRARY, isolated / "enrich")
    policy = isolated / "policy.yaml"
    entry = {
        "policy_id": "T8",
        "certified_by": "dev_selection",
        "enrichment": "enrich/global.yaml",
        "enrichment_sha256": _sha(path),
    }
    payload = {"policies": {HAIKU: {_sha(GLOBAL_LIBRARY): entry}}}
    policy.write_text(json.dumps(payload), encoding="utf-8")
    folder = _run(isolated, "fake", "E-01-C", "--policy", str(policy))
    manifest = read_manifest(folder)
    assert manifest["policy_resolution"] == "override"
    assert manifest["enrichment_sha256"] == _sha(path)
    assert manifest["enrichment_path"] == "enrich/global.yaml"


def test_a_replay_re_renders_what_the_rescued_lines_rendered(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _generated(GLOBAL_LIBRARY, isolated / "enrich")
    policy = isolated / "policy.yaml"
    entry = {
        "policy_id": "T8",
        "certified_by": "dev_selection",
        "enrichment": "enrich/global.yaml",
        "enrichment_sha256": _sha(path),
    }
    policy.write_text(
        json.dumps({"policies": {HAIKU: {_sha(GLOBAL_LIBRARY): entry}}}), encoding="utf-8"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    for name, value in (("BREAKER_CONSECUTIVE_FAILURES", "1"), ("MAX_RETRIES", "0")):
        monkeypatch.setenv(f"ORIS_{name}", value)
    factory = TwoProviderFactory()
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(isolated, factory))  # type: ignore[arg-type]
    output = isolated / "out.csv"
    small = ROOT / "tests" / "fixtures" / "cli" / "small_boq.csv"
    args = ["match", "--input", str(small), "--library", str(GLOBAL_LIBRARY)]
    result = CliRunner().invoke(
        cli.app, [*args, "--output", str(output), "--policy", str(policy), "--no-cache"]
    )
    assert result.exit_code == 0, result.output
    assert factory.calls(LLMKind.OPENAI) > 0
    folder = next((isolated / "runs").iterdir())
    manifest = read_manifest(folder)
    assert manifest["fallback_engaged"] is True
    assert manifest["enrichment_sha256"] == _sha(path)
    assert manifest["fallback_enrichment_sha256"] is None
    assert manifest["fallback_enrichment_path"] is None
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(isolated))
    replayed = CliRunner().invoke(cli.app, ["replay", str(folder), "--check", str(output)])
    assert replayed.exit_code == 0, replayed.output
    assert "byte-identical" in replayed.stdout


def test_the_runner_refuses_an_enrichment_of_another_library(isolated: Path) -> None:
    path = _generated(SMALL_LIBRARY, isolated / "enrich")
    port = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=MAIN_ANSWERS))
    runtime = make_runtime(isolated, Factory(port))
    code = runner.main(argv(isolated, "fake", "E-01-C", "--enrichment", str(path)), runtime)
    assert code == 2
    assert port.calls == []
    assert not (isolated / "experiments.jsonl").exists()


def test_the_mixed_e08_replay_of_an_enriched_source_still_works(isolated: Path) -> None:
    path = _generated(GLOBAL_LIBRARY, isolated / "enrich")
    source = _run(isolated, "fake", "E-01-C", "--enrichment", str(path))
    arm = _run(
        isolated,
        f"replay:{source.as_posix()}",
        "E-08",
        "--verifier-adopted",
        "--llm-verifier",
        "fake",
    )
    manifest = read_manifest(arm)
    assert manifest["enrichment_sha256"] == _sha(path)
    records = read_calls_jsonl(arm / "calls.jsonl")
    main = [record for record in records if '"candidates"' not in record.user_message]
    assert main
    assert len(main) < len(records)
    assert all(record.cache_hit for record in main)


def test_oris_match_takes_the_flag(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _generated(GLOBAL_LIBRARY, isolated / "enrich")
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(isolated, _fake_factory()))
    small = ROOT / "tests" / "fixtures" / "cli" / "small_boq.csv"
    common = ["match", "--input", str(small), "--library", str(GLOBAL_LIBRARY), "--llm", "fake"]
    on = CliRunner().invoke(
        cli.app,
        [*common, "--output", str(isolated / "on.csv"), "--enrichment", str(path)],
    )
    assert on.exit_code == 0, on.output
    off = CliRunner().invoke(
        cli.app,
        [*common, "--output", str(isolated / "off.csv"), "--enrichment", "none"],
    )
    assert off.exit_code == 0, off.output
    manifests = [read_manifest(folder) for folder in sorted((isolated / "runs").iterdir())]
    assert sorted(str(m["enrichment_sha256"]) for m in manifests) == sorted(["None", _sha(path)])


def test_a_committable_run_refuses_an_enrichment_outside_the_repository(tmp_path: Path) -> None:
    job = cli.MatchJob(
        input_path=EN_INPUT,
        library_path=GLOBAL_LIBRARY,
        llm=LLMSpec(LLMKind.FAKE),
        runs_dir=Path("runs") / "submission",
        enrichment=tmp_path / "global.yaml",
    )
    with pytest.raises(ValueError, match="outside the repository"):
        cli.require_committable_inputs(job, ROOT)
    inside = cli.MatchJob(
        input_path=EN_INPUT,
        library_path=GLOBAL_LIBRARY,
        llm=LLMSpec(LLMKind.FAKE),
        runs_dir=Path("runs") / "submission",
        enrichment=Path("none"),
    )
    cli.require_committable_inputs(inside, ROOT)


def test_a_committable_run_refuses_the_setting_naming_a_file_outside(isolated: Path) -> None:
    small = ROOT / "tests" / "fixtures" / "cli" / "small_boq.csv"
    shutil.copyfile(small, isolated / "boq.csv")
    shutil.copyfile(GLOBAL_LIBRARY, isolated / "library.csv")
    outside = _generated(GLOBAL_LIBRARY, isolated.parent / f"{isolated.name}-outside")
    job = cli.MatchJob(
        input_path=isolated / "boq.csv",
        library_path=isolated / "library.csv",
        llm=LLMSpec(LLMKind.FAKE),
        runs_dir=Path("runs") / "submission",
    )
    runtime = make_runtime(isolated, _fake_factory())
    with pytest.raises(ValueError, match="outside the repository"):
        cli.prepare_run(job, make_settings(enrichment=outside), runtime)
    inside = _generated(GLOBAL_LIBRARY, isolated / "enrich")
    prepared = cli.prepare_run(job, make_settings(enrichment=inside), runtime)
    assert prepared.service.resources.settings.enrichment == inside


# The lockbox


def test_a_lockbox_session_refuses_an_enrichment_flag(isolated: Path) -> None:
    factory = RecordingFactory()
    args = experiment_argv(isolated, "en", *SESSION, "--enrichment", "none")
    code = runner.main(args, experiment_runtime(isolated, factory, Git("eval-freeze\n")))
    assert code == runner.EXIT_REFUSED
    assert factory.fakes == []


def test_a_lockbox_session_refuses_the_enrichment_setting(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORIS_ENRICHMENT", "none")
    factory = RecordingFactory()
    args = experiment_argv(isolated, "en", *SESSION)
    code = runner.main(args, experiment_runtime(isolated, factory, Git("eval-freeze\n")))
    assert code == runner.EXIT_REFUSED
    assert factory.fakes == []
