"""The ``oris`` CLI: default match command, replay, score passthrough, exit codes (§11.3, §12)."""

import csv
import hashlib
import importlib.util
import io
import json
import re
import shutil
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx2
import pytest
from typer.testing import CliRunner, Result

from oris_matcher import cli
from oris_matcher.doctor import LLMKind, LLMSpec, Runtime, WiringError, parse_llm_spec
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, Fault, FaultKind, default_answer
from oris_matcher.llm.recording import read_calls_jsonl
from oris_matcher.prompts.v1.render import B2, CANONICAL_V1, REVERSE_V1
from oris_matcher.prompts.v1.version import prompt_version
from oris_matcher.settings import Settings, load_models_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
FR_LIBRARY = ROOT / "data" / "oris_materials_fr.csv"
GLOBAL_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
SMALL_BOQ = ROOT / "tests" / "fixtures" / "cli" / "small_boq.csv"
SCORE_FIXTURES = ROOT / "tests" / "fixtures" / "score"
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
HAIKU = "claude-haiku-4-5-20251001"
GPT = "gpt-4o-mini-2024-07-18"
FIXED_NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
SECRET = "sk-ant-test-never-printed"
ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "ORIS_API_TOKEN",
    "ORIS_PRIMARY_MODEL",
    "ORIS_FALLBACK_MODEL",
)
RUNNER = CliRunner()


def fake_git(args: Sequence[str], root: Path) -> str:
    del root
    return "a" * 40 + "\n" if "rev-parse" in args else ""


async def no_sleep(seconds: float) -> None:
    del seconds


def make_runtime(**overrides: Any) -> Runtime:
    values: dict[str, Any] = {"git": fake_git, "clock": lambda: FIXED_NOW, "sleep": no_sleep}
    values.update(overrides)
    return Runtime(**values)


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ORIS_CONFIG_DIR", str(CONFIG))
    libraries = {"global": str(GLOBAL_LIBRARY), "fr": str(FR_LIBRARY)}
    monkeypatch.setenv("ORIS_LIBRARIES", json.dumps(libraries))
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: tmp_path))
    return tmp_path


def invoke(args: list[str]) -> Result:
    return RUNNER.invoke(cli.app, args)


def match_args(output: Path, *extra: str, library: Path = FR_LIBRARY) -> list[str]:
    return [
        "--input",
        str(SMALL_BOQ),
        "--library",
        str(library),
        "--output",
        str(output),
        *extra,
    ]


def only_run(workdir: Path) -> Path:
    folders = sorted((workdir / "runs").iterdir())
    assert len(folders) == 1
    return folders[0]


def without_call_ids(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    drop = rows[0].index("call_ids")
    return [row[:drop] + row[drop + 1 :] for row in rows]


def manifest_of(folder: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    return loaded


def test_brief_literal_form_runs_match_by_default(workdir: Path) -> None:
    output = workdir / "out.csv"
    result = invoke(match_args(output, "--llm", "fake"))

    assert result.exit_code == 0, result.output
    header = output.read_bytes().split(b"\r\n")[0].decode("utf-8")
    assert header.startswith("Item No.,Short Description,Long Description,Unit,BoQ Qty,decision")
    folder = only_run(workdir)
    manifest = manifest_of(folder)
    assert manifest["library_id"] == "fr"
    assert manifest["llm"] == "fake"
    assert manifest["llm_kind"] == "fake"
    assert manifest["mode"] == "fake"
    assert manifest["input_sha256"] == hashlib.sha256(SMALL_BOQ.read_bytes()).hexdigest()
    assert isinstance(manifest["wall_clock_s"], float)
    assert manifest["n_routed"] > 0
    scaled = Settings().budget_usd_per_100_lines * manifest["line_count"] / 100
    assert manifest["budget_cap_usd"] == pytest.approx(max(scaled, 0.10))
    assert {"manifest.json", "calls.jsonl", "audit.jsonl"} <= {p.name for p in folder.iterdir()}
    for needle in ("policy_resolution: fallback_strictest", "decisions:", "reasons:", "p50"):
        assert needle in result.stdout
    assert "per 100 lines" in result.stdout
    assert "cache hits" in result.stdout


def test_manifest_names_no_host_path(workdir: Path) -> None:
    assert invoke(match_args(workdir / "out.csv", "--llm", "fake")).exit_code == 0
    folder = only_run(workdir)
    text = (folder / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(text)
    assert manifest["input_path"] == {"external": True, "path": SMALL_BOQ.name}
    assert manifest["settings_effective"]["libraries"]["fr"] == {
        "external": True,
        "path": FR_LIBRARY.name,
    }
    for host in (ROOT, workdir):
        assert str(host) not in text
        assert host.as_posix() not in text
    copy = folder / "external" / "input" / SMALL_BOQ.name
    assert copy.read_bytes() == SMALL_BOQ.read_bytes()
    replayed = invoke(["replay", str(folder), "--check", str(workdir / "out.csv")])
    assert replayed.exit_code == 0, replayed.output


def test_replay_refuses_a_library_whose_bytes_changed(workdir: Path) -> None:
    copy = workdir / "lib.csv"
    shutil.copyfile(FR_LIBRARY, copy)
    assert invoke(match_args(workdir / "out.csv", "--llm", "fake", library=copy)).exit_code == 0
    folder = only_run(workdir)
    copy.write_bytes(copy.read_bytes() + b"\r\n")
    result = invoke(["replay", str(folder)])
    assert result.exit_code == 2
    assert "is not the one the run used" in result.stderr


def test_replay_of_a_run_with_an_external_custom_library(
    workdir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "lib.csv"
    shutil.copyfile(FR_LIBRARY, outside)
    output = workdir / "out.csv"
    assert invoke(match_args(output, "--llm", "fake", library=outside)).exit_code == 0
    folder = only_run(workdir)
    manifest = manifest_of(folder)
    assert manifest["library_id"] == "custom"
    assert manifest["settings_effective"]["libraries"]["custom"] == {
        "external": True,
        "path": "lib.csv",
    }
    assert (folder / "external" / "library" / "lib.csv").read_bytes() == outside.read_bytes()
    outside.unlink()

    replayed = invoke(["replay", str(folder), "--check", str(output)])
    assert replayed.exit_code == 0, replayed.output
    assert "byte-identical" in replayed.stdout


def test_replay_refuses_a_missing_external_library_copy(
    workdir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "lib.csv"
    shutil.copyfile(FR_LIBRARY, outside)
    assert invoke(match_args(workdir / "out.csv", "--llm", "fake", library=outside)).exit_code == 0
    folder = only_run(workdir)
    shutil.rmtree(folder / "external" / "library")

    result = invoke(["replay", str(folder)])
    assert result.exit_code == 2
    assert "library 'custom'" in result.stderr
    assert "KeyError" not in result.output


def test_match_subcommand_gives_the_same_bytes(workdir: Path) -> None:
    literal, explicit = workdir / "literal.csv", workdir / "explicit.csv"
    assert invoke(match_args(literal, "--llm", "fake", "--no-cache")).exit_code == 0
    assert invoke(["match", *match_args(explicit, "--llm", "fake", "--no-cache")]).exit_code == 0
    assert without_call_ids(literal) == without_call_ids(explicit)


def test_library_path_outside_settings_maps_to_custom(workdir: Path) -> None:
    copy = workdir / "my_library.csv"
    shutil.copyfile(FR_LIBRARY, copy)
    result = invoke(match_args(workdir / "out.csv", "--llm", "fake", library=copy))

    assert result.exit_code == 0, result.output
    manifest = manifest_of(only_run(workdir))
    assert manifest["library_id"] == "custom"
    assert manifest["settings_effective"]["libraries"]["custom"].endswith("my_library.csv")


def test_missing_key_is_a_clear_usage_error(workdir: Path) -> None:
    result = invoke(match_args(workdir / "out.csv"))

    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY is not set" in result.stderr
    assert "Traceback" not in result.output
    assert not (workdir / "runs").exists()


def test_missing_openai_key_is_a_clear_usage_error(workdir: Path) -> None:
    result = invoke(match_args(workdir / "out.csv", "--llm", "openai:gpt-4o-mini"))
    assert result.exit_code == 2
    assert "OPENAI_API_KEY is not set" in result.stderr


@pytest.mark.parametrize(
    ("llm", "message"),
    [
        ("gemini:flash", "unknown adapter"),
        ("anthropic:claude-opus-4-1", "no single priced anthropic snapshot"),
        ("anthropic:gpt-4o-mini", "no single priced anthropic snapshot"),
        ("replay:", "needs a run folder"),
    ],
)
def test_bad_llm_spec_exits_2(workdir: Path, llm: str, message: str) -> None:
    result = invoke(match_args(workdir / "out.csv", "--llm", llm))
    assert result.exit_code == 2
    assert message in result.stderr


def test_missing_input_exits_2(workdir: Path) -> None:
    args = match_args(workdir / "out.csv", "--llm", "fake")
    args[1] = str(workdir / "absent.csv")
    result = invoke(args)
    assert result.exit_code == 2
    assert "error:" in result.stderr


def test_missing_library_exits_2(workdir: Path) -> None:
    result = invoke(match_args(workdir / "o.csv", "--llm", "fake", library=workdir / "nope.csv"))
    assert result.exit_code == 2
    assert "library file not found" in result.stderr


def test_replay_check_is_byte_identical_and_flags_a_mismatch(workdir: Path) -> None:
    output = workdir / "out.csv"
    assert invoke(match_args(output, "--llm", "fake")).exit_code == 0
    folder = only_run(workdir)

    same = invoke(["replay", str(folder), "--check", str(output)])
    assert same.exit_code == 0, same.output
    assert "byte-identical" in same.stdout
    assert "mode replay" in same.stdout
    assert sorted((workdir / "runs").iterdir()) == [folder]

    tampered = workdir / "tampered.csv"
    tampered.write_bytes(output.read_bytes().replace(b"needs_review", b"matched", 1))
    differs = invoke(["replay", str(folder), "--check", str(tampered)])
    assert differs.exit_code == 1
    assert "MISMATCH" in differs.stdout


def test_replay_with_excel_bom_round_trips(workdir: Path) -> None:
    output = workdir / "out.csv"
    assert invoke(match_args(output, "--llm", "fake", "--excel-bom")).exit_code == 0
    assert output.read_bytes().startswith("﻿".encode())
    folder = only_run(workdir)
    assert manifest_of(folder)["excel_bom"] is True
    assert invoke(["replay", str(folder), "--check", str(output)]).exit_code == 0


def test_replay_writes_output_when_asked(workdir: Path) -> None:
    output = workdir / "out.csv"
    assert invoke(match_args(output, "--llm", "fake")).exit_code == 0
    copy = workdir / "replayed.csv"
    assert invoke(["replay", str(only_run(workdir)), "--output", str(copy)]).exit_code == 0
    assert copy.read_bytes() == output.read_bytes()


def test_replay_miss_gives_exit_code_3_and_never_calls_live(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert invoke(match_args(workdir / "first.csv", "--llm", "fake")).exit_code == 0
    folder = only_run(workdir)
    (folder / "calls.jsonl").write_bytes(b"")
    output = workdir / "missed.csv"
    requests: list[httpx2.Request] = []
    _live_runtime(workdir, monkeypatch, requests)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test-never-called")

    result = invoke(match_args(output, "--llm", f"replay:{folder}"))

    assert requests == []
    assert result.exit_code == 3, result.output
    text = output.read_text(encoding="utf-8")
    assert "LLM_FAILURE:replay_miss" in text
    newest = max((workdir / "runs").iterdir(), key=lambda path: path.name != folder.name)
    assert manifest_of(newest)["mode"] == "replay"
    assert manifest_of(newest)["source_run_id"] == folder.name


def _live_runtime(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, requests: list[httpx2.Request]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    transport = httpx2.MockTransport(_answering_handler(requests))
    runtime = make_runtime(
        root=lambda: workdir, http_client=lambda: httpx2.AsyncClient(transport=transport)
    )
    monkeypatch.setattr(cli, "RUNTIME", runtime)


RUN_OUTPUTS: dict[str, str] = {}


def run_and_note(name: str, args: list[str]) -> Result:
    runs = Path.cwd() / "runs"
    before = set(runs.iterdir()) if runs.exists() else set()
    result = invoke(args)
    (new,) = set(runs.iterdir()) - before
    RUN_OUTPUTS[name] = new.name
    return result


def test_second_live_run_is_served_from_cache_unless_no_cache(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx2.Request] = []
    _live_runtime(workdir, monkeypatch, requests)
    assert run_and_note("a.csv", match_args(workdir / "a.csv")).exit_code == 0
    cold_calls = len(requests)
    assert run_and_note("b.csv", match_args(workdir / "b.csv")).exit_code == 0
    assert len(requests) == cold_calls
    assert run_and_note("c.csv", match_args(workdir / "c.csv", "--no-cache")).exit_code == 0
    assert len(requests) == 2 * cold_calls

    names = ("a.csv", "b.csv", "c.csv")
    cold, cached, uncached = (manifest_of(workdir / "runs" / RUN_OUTPUTS[n]) for n in names)
    assert (cold["mode"], cold["cache_hits"], cold["source_run_id"]) == ("live", 0, None)
    assert cached["mode"] == "cached"
    assert cached["cache_hits"] == cached["call_count"] > 0
    assert cached["spend_usd"] == 0
    assert cached["source_run_id"] == cold["run_id"]
    assert (uncached["mode"], uncached["cache_hits"]) == ("live", 0)
    assert without_call_ids(workdir / "a.csv") == without_call_ids(workdir / "b.csv")


def test_fake_runs_never_feed_the_live_cache(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_and_note("f1", match_args(workdir / "f1.csv", "--llm", "fake")).exit_code == 0
    assert run_and_note("f2", match_args(workdir / "f2.csv", "--llm", "fake")).exit_code == 0
    requests: list[httpx2.Request] = []
    _live_runtime(workdir, monkeypatch, requests)

    assert run_and_note("live", match_args(workdir / "live.csv")).exit_code == 0

    assert requests
    fake, fake2, live = (
        manifest_of(workdir / "runs" / RUN_OUTPUTS[n]) for n in ("f1", "f2", "live")
    )
    assert (fake["mode"], fake2["mode"], fake2["cache_hits"]) == ("fake", "fake", 0)
    assert (live["mode"], live["cache_hits"]) == ("live", 0)
    assert len(requests) == live["call_count"]


def test_cache_index_reads_only_live_run_folders(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = cli.CacheKey("anthropic", HAIKU)
    assert invoke(match_args(workdir / "fake.csv", "--llm", "fake")).exit_code == 0
    assert cli.load_cache(workdir / "runs", key) == cli.CacheIndex()
    requests: list[httpx2.Request] = []
    _live_runtime(workdir, monkeypatch, requests)
    assert run_and_note("live", match_args(workdir / "live.csv", "--no-cache")).exit_code == 0
    index = cli.load_cache(workdir / "runs", key)
    assert index.cache is not None
    assert len(index.cache) == len(requests)
    assert set(index.sources.values()) == {RUN_OUTPUTS["live"]}
    assert cli.load_cache(workdir / "runs", cli.CacheKey("openai", GPT)) == cli.CacheIndex()
    assert cli.load_cache(workdir / "runs", cli.CacheKey("anthropic", "x")) == cli.CacheIndex()

    assert run_and_note("warm", match_args(workdir / "warm.csv")).exit_code == 0
    warm = manifest_of(workdir / "runs" / RUN_OUTPUTS["warm"])
    assert warm["mode"] == "cached"
    assert set(cli.load_cache(workdir / "runs", key).sources.values()) == {RUN_OUTPUTS["live"]}


def test_policy_file_is_recorded_as_an_override(workdir: Path) -> None:
    sha = hashlib.sha256(FR_LIBRARY.read_bytes()).hexdigest()
    policy = workdir / "policy.yaml"
    policy.write_text(
        f"policies:\n  {HAIKU}:\n    {sha}: {{policy_id: T8, certified_by: dev_selection}}\n",
        encoding="utf-8",
    )
    result = invoke(match_args(workdir / "out.csv", "--llm", "fake", "--policy", str(policy)))

    assert result.exit_code == 0, result.output
    assert "policy_resolution: override (policy T8)" in result.stdout
    folder = only_run(workdir)
    manifest = manifest_of(folder)
    assert manifest["policy_resolution"] == "override"
    assert manifest["policy_id"] == "T8"
    assert manifest["policy_certified_by"] is None
    assert manifest["policy_claimed_certified_by"] == "dev_selection"
    assert manifest["policy_path"] == "policy.yaml"
    assert manifest["policy_sha256"] == hashlib.sha256(policy.read_bytes()).hexdigest()

    replayed = invoke(["replay", str(folder), "--check", str(workdir / "out.csv")])
    assert replayed.exit_code == 0, replayed.output
    assert "policy_resolution: override (policy T8)" in replayed.stdout


def test_replay_with_a_policy_re_decides_at_zero_cost(workdir: Path) -> None:
    assert invoke(match_args(workdir / "out.csv", "--llm", "fake")).exit_code == 0
    sha = hashlib.sha256(FR_LIBRARY.read_bytes()).hexdigest()
    policy = workdir / "loose.yaml"
    policy.write_text(
        f"policies:\n  {HAIKU}:\n    {sha}: {{policy_id: T8, certified_by: smoke_A10}}\n",
        encoding="utf-8",
    )
    result = invoke(["replay", str(only_run(workdir)), "--policy", str(policy)])
    assert result.exit_code == 0, result.output
    assert "policy_resolution: override (policy T8)" in result.stdout
    assert "$0.000000 spent" in result.stdout


def test_profile_b2_runs_one_raw_pass(workdir: Path) -> None:
    result = invoke(match_args(workdir / "out.csv", "--llm", "fake", "--profile", "b2"))
    assert result.exit_code == 0, result.output
    manifest = manifest_of(only_run(workdir))
    assert manifest["profile"] == "b2"
    assert manifest["policy_resolution"] == "b2_match_all"
    assert manifest["passes_k"] == 1


RATE_LIMIT_HEADERS = {"anthropic-ratelimit-requests-limit": "1000"}


def _answering_handler(requests: list[httpx2.Request]) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        payload = json.loads(request.content)["messages"][0]["content"]
        ids = re.findall(r'"id": "(L\d+)"', payload)
        text = json.dumps({"lines": [default_answer(line_id) for line_id in ids]})
        message = {
            "id": f"msg_{len(requests)}",
            "type": "message",
            "role": "assistant",
            "model": HAIKU,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        remaining = {"anthropic-ratelimit-requests-remaining": str(1000 - len(requests))}
        headers = {"request-id": f"req_{len(requests)}", **RATE_LIMIT_HEADERS, **remaining}
        return httpx2.Response(200, json=message, headers=headers)

    return handler


def test_live_anthropic_adapter_is_built_only_when_selected(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    requests: list[httpx2.Request] = []
    transport = httpx2.MockTransport(_answering_handler(requests))
    runtime = make_runtime(
        root=lambda: workdir, http_client=lambda: httpx2.AsyncClient(transport=transport)
    )
    monkeypatch.setattr(cli, "RUNTIME", runtime)

    result = invoke(match_args(workdir / "out.csv", "--no-cache"))

    assert result.exit_code == 0, result.output
    assert requests
    assert all(request.url.host == "api.anthropic.com" for request in requests)
    records = read_calls_jsonl(only_run(workdir) / "calls.jsonl")
    assert {record.provider_request_id for record in records} == {
        f"req_{n}" for n in range(1, len(requests) + 1)
    }
    assert SECRET not in result.output
    assert SECRET not in (only_run(workdir) / "manifest.json").read_text(encoding="utf-8")
    manifest = manifest_of(only_run(workdir))
    last_call = {
        **RATE_LIMIT_HEADERS,
        "anthropic-ratelimit-requests-remaining": str(1000 - len(requests)),
    }
    assert manifest["rate_limit_headers"] == last_call
    assert manifest["rate_limit_source"] == "last_live_call"
    assert (manifest["rate_limit_tier"], manifest["rate_limit_tier_source"]) == ("unknown", None)
    assert manifest["fallback_configured"] is False


def test_a_live_run_takes_its_tier_from_the_newest_doctor_evidence(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = workdir / "evidence"
    evidence.mkdir()
    for day, tier in (("2026-10-01", "1"), ("2026-10-03", "2")):
        check = {"name": "rate_limit_tier", "status": "pass", "detail": "", "data": {"tier": tier}}
        report = {"checks": [check]}
        (evidence / f"doctor_{day}.json").write_text(json.dumps(report), encoding="utf-8")
    (evidence / "doctor_2026-10-04.json").write_text(json.dumps({"checks": []}), encoding="utf-8")
    requests: list[httpx2.Request] = []
    _live_runtime(workdir, monkeypatch, requests)

    assert invoke(match_args(workdir / "out.csv", "--no-cache")).exit_code == 0

    manifest = manifest_of(only_run(workdir))
    assert manifest["rate_limit_tier"] == "2"
    assert manifest["rate_limit_tier_source"] == "evidence/doctor_2026-10-03.json"


def test_a_fake_run_records_no_rate_limits(workdir: Path) -> None:
    assert invoke(match_args(workdir / "out.csv", "--llm", "fake")).exit_code == 0
    manifest = manifest_of(only_run(workdir))
    assert manifest["rate_limit_headers"] is None
    assert manifest["rate_limit_tier"] is None
    assert manifest["rate_limit_source"] == "none"


def test_runtime_adapter_factory_receives_the_spec_and_model(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[LLMSpec, str]] = []

    def factory(spec: LLMSpec, model: str) -> FakeLLM:
        seen.append((spec, model))
        return FakeLLM(model, ALLOWLIST, FakeBehaviour())

    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: workdir, adapter_factory=factory))
    assert invoke(match_args(workdir / "out.csv", "--llm", "openai:gpt-4o-mini")).exit_code == 0
    assert seen == [(LLMSpec(LLMKind.OPENAI, model="gpt-4o-mini"), GPT)]
    assert manifest_of(only_run(workdir))["requested_model"] == GPT


def test_score_passes_through_to_the_scorer(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: ROOT))
    output, reference = SCORE_FIXTURES / "output.csv", SCORE_FIXTURES / "reference.csv"
    result = invoke(["score", "--output", str(output), "--reference", str(reference)])
    assert result.exit_code == 0, result.output
    assert "matched precision" in result.stdout

    missing = invoke(["score", "--output", str(workdir / "x.csv"), "--reference", str(reference)])
    assert missing.exit_code == 2
    assert "error:" in missing.stderr


def test_score_without_the_scorer_exits_2(workdir: Path) -> None:
    result = invoke(["score", "--help"])
    assert result.exit_code == 2
    assert "scorer not found" in result.stderr


def test_doctor_command_offline_prints_the_table(workdir: Path) -> None:
    result = invoke(["doctor", "--evidence-dir", str(workdir / "evidence")])
    assert result.exit_code == 1
    assert "FAIL  anthropic_key" in result.stdout
    assert "PASS  allowlist" in result.stdout
    assert (workdir / "evidence" / "doctor_2026-10-05.json").is_file()


def test_no_arguments_shows_help() -> None:
    result = invoke([])
    assert "match" in result.output
    assert "replay" in result.output


def test_configure_stdout_switches_to_utf8_with_replacement() -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    cli.configure_stdout(stream)
    assert stream.encoding == "utf-8"
    assert stream.errors == "replace"
    cli.configure_stdout(object())


def test_parse_llm_spec_forms() -> None:
    assert parse_llm_spec("fake") == LLMSpec(LLMKind.FAKE)
    assert parse_llm_spec("anthropic") == LLMSpec(LLMKind.ANTHROPIC)
    assert parse_llm_spec(f"anthropic:{HAIKU}") == LLMSpec(LLMKind.ANTHROPIC, model=HAIKU)
    assert parse_llm_spec("replay:runs/x") == LLMSpec(LLMKind.REPLAY, run_dir=Path("runs/x"))
    assert parse_llm_spec("replay:runs/x").text == "replay:runs/x"
    with pytest.raises(WiringError):
        parse_llm_spec("fake:model")


def test_percentile_is_nearest_rank() -> None:
    assert cli.percentile([], 50) is None
    assert cli.percentile([5], 95) == 5
    assert cli.percentile([1, 2, 3, 4], 50) == 2
    assert cli.percentile(list(range(1, 101)), 95) == 95


def test_run_summary_fields(workdir: Path) -> None:
    job = cli.MatchJob(
        input_path=SMALL_BOQ, library_path=FR_LIBRARY, llm=LLMSpec(LLMKind.FAKE), use_cache=False
    )
    settings = Settings(_env_file=None, config_dir=CONFIG, libraries={"fr": FR_LIBRARY})  # type: ignore[call-arg]
    outcome = cli.execute_match(job, settings, make_runtime(root=lambda: workdir))
    summary = cli.run_summary(outcome.result)

    boq = read_boq(SMALL_BOQ)
    assert summary["lines"] == len(boq.lines)
    assert sum(summary["decision_counts"].values()) == len(boq.lines)
    assert summary["retries"] == 0
    assert summary["policy_resolution"] == "fallback_strictest"
    assert summary["latency_p50_ms"] is not None
    expected = outcome.result.attributed_cost_usd * 100 / len(boq.lines)
    assert summary["usd_per_100_lines"] == pytest.approx(expected)
    assert outcome.output_path == outcome.folder / "output.csv"


EN_INPUT = ROOT / "input" / "boq_dataset_input_en.csv"


def _down(call_no: int, req: Any) -> Fault:
    del call_no, req
    return Fault(FaultKind.SERVER_ERROR)


class TwoProviderFactory:
    """Hands out a failing primary and an answering fallback, keeping every request."""

    def __init__(self, primary_fails: bool = True) -> None:
        self.primary_fails = primary_fails
        self.fakes: dict[LLMKind, list[FakeLLM]] = {}

    def __call__(self, spec: LLMSpec, model: str) -> FakeLLM:
        failing = spec.kind == LLMKind.ANTHROPIC and self.primary_fails
        behaviour = FakeBehaviour(rule=_down) if failing else FakeBehaviour()
        fake = FakeLLM(model, ALLOWLIST, behaviour)
        self.fakes.setdefault(spec.kind, []).append(fake)
        return fake

    def calls(self, kind: LLMKind) -> int:
        return sum(len(fake.calls) for fake in self.fakes.get(kind, []))


def test_split_sha256_is_recorded_and_checked(workdir: Path) -> None:
    sha = "c" * 64
    result = invoke(match_args(workdir / "out.csv", "--llm", "fake", "--split-sha256", sha))
    assert result.exit_code == 0, result.output
    assert manifest_of(only_run(workdir))["split_sha256"] == sha

    bad = invoke(match_args(workdir / "bad.csv", "--llm", "fake", "--split-sha256", "eval.json"))
    assert bad.exit_code == 2
    assert "--split-sha256" in bad.stderr


def test_breaker_trip_switches_to_the_fallback_and_replays(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    for name, value in (("BREAKER_CONSECUTIVE_FAILURES", "1"), ("MAX_RETRIES", "0")):
        monkeypatch.setenv(f"ORIS_{name}", value)
    monkeypatch.setenv("ORIS_BATCH_SIZE", "1")
    factory = TwoProviderFactory()
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: workdir, adapter_factory=factory))
    output = workdir / "out.csv"

    result = invoke(match_args(output, "--no-cache"))

    assert result.exit_code == 0, result.output
    assert factory.calls(LLMKind.OPENAI) > 0
    manifest = manifest_of(only_run(workdir))
    assert manifest["fallback_configured"] is True
    assert manifest["fallback_engaged"] is True
    assert manifest["fallback_policy_resolution"] == "fallback_strictest"
    assert GPT in manifest["served_models"]
    assert "LLM_UNAVAILABLE" not in output.read_text(encoding="utf-8")
    assert f",{GPT}," in output.read_text(encoding="utf-8")

    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: workdir))
    replayed = invoke(["replay", str(only_run(workdir)), "--check", str(output)])
    assert replayed.exit_code == 0, replayed.output
    assert "mode replay" in replayed.stdout


def test_missing_anthropic_key_runs_on_the_fallback_when_its_key_is_set(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    factory = TwoProviderFactory(primary_fails=False)
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: workdir, adapter_factory=factory))

    result = invoke(match_args(workdir / "out.csv", "--no-cache"))

    assert result.exit_code == 0, result.output
    assert factory.calls(LLMKind.ANTHROPIC) == 0
    assert factory.calls(LLMKind.OPENAI) > 0
    manifest = manifest_of(only_run(workdir))
    assert (manifest["llm"], manifest["requested_model"]) == ("openai", GPT)
    assert manifest["policy_resolution"] == "fallback_strictest"


@pytest.mark.parametrize(("tags", "code"), [("", 2), ("eval-freeze\n", 0)])
def test_a_live_run_over_an_exercise_input_needs_the_freeze_tag(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, tags: str, code: int
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    factory = TwoProviderFactory(primary_fails=False)

    def git(args: Sequence[str], root: Path) -> str:
        del root
        if "tag" in args:
            return tags
        return fake_git(args, workdir)

    runtime = make_runtime(root=lambda: workdir, adapter_factory=factory, git=git)
    monkeypatch.setattr(cli, "RUNTIME", runtime)
    args = match_args(workdir / "out.csv", "--no-cache", library=GLOBAL_LIBRARY)
    args[1] = str(EN_INPUT)

    result = invoke(args)

    assert result.exit_code == code, result.output
    if code:
        assert "eval-freeze" in result.stderr
        assert factory.fakes == {}
        assert not (workdir / "runs").exists()


def test_an_offline_run_over_an_exercise_input_is_allowed(workdir: Path) -> None:
    args = match_args(workdir / "out.csv", "--llm", "fake", library=GLOBAL_LIBRARY)
    args[1] = str(EN_INPUT)
    assert invoke(args).exit_code == 0


def test_a_submission_run_may_not_read_files_outside_the_repository(workdir: Path) -> None:
    submission = workdir / "runs" / "submission"
    result = invoke(match_args(workdir / "out.csv", "--llm", "fake", "--run-dir", str(submission)))
    assert result.exit_code == 2
    assert "outside the repository" in result.stderr
    assert not submission.exists()


def test_a_replay_spec_is_recorded_repo_relative(workdir: Path) -> None:
    assert invoke(match_args(workdir / "first.csv", "--llm", "fake")).exit_code == 0
    folder = only_run(workdir)
    result = invoke(match_args(workdir / "again.csv", "--llm", f"replay:{folder}"))
    assert result.exit_code == 0, result.output
    replayed = [manifest_of(path) for path in (workdir / "runs").iterdir() if path != folder]
    assert replayed[0]["llm"] == f"replay:runs/{folder.name}"
    assert replayed[0]["mode"] == "replay"


CHECK_REQUIREMENTS = ROOT / "eval" / "check_requirements.py"
ORIS_IN_PROCESS = (sys.executable, "-c", "from oris_matcher.cli import app; app()")
B0_REVIEW_REASON = "LOW_SIGNAL:v+b+confidence"
EXERCISE_LINES = 319
EXERCISE_HEADERS = 37


def load_check_requirements() -> ModuleType:
    spec = importlib.util.spec_from_file_location("oris_eval_check_b0", CHECK_REQUIREMENTS)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RecordingFactory:
    """Records every adapter the CLI asks for; a B0 run must ask for none."""

    def __init__(self) -> None:
        self.built: list[tuple[LLMSpec, str]] = []

    def __call__(self, spec: LLMSpec, model: str) -> FakeLLM:
        self.built.append((spec, model))
        return FakeLLM(model, ALLOWLIST, FakeBehaviour())


def output_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize("llm", [None, "anthropic", "openai:gpt-4o-mini", "fake"])
def test_profile_b0_needs_no_key_and_builds_no_adapter(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, llm: str | None
) -> None:
    factory = RecordingFactory()
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: workdir, adapter_factory=factory))
    extra = ["--profile", "b0", *(["--llm", llm] if llm else [])]

    result = invoke(match_args(workdir / "out.csv", *extra))

    assert result.exit_code == 0, result.output
    assert factory.built == []
    folder = only_run(workdir)
    manifest = manifest_of(folder)
    assert manifest["profile"] == "b0"
    assert manifest["mode"] == "rules"
    assert manifest["policy_resolution"] == "b0_rules_only"
    assert manifest["call_count"] == 0
    assert manifest["fallback_configured"] is False
    assert manifest["rate_limit_source"] == "none"
    assert manifest["rate_limit_tier"] is None
    assert manifest["rate_limit_tier_source"] is None
    assert (folder / "calls.jsonl").read_bytes() == b""
    rows = output_rows(workdir / "out.csv")
    assert {row["model"] for row in rows} == {"rules"}
    assert {row["cost_usd"] for row in rows} == {"0.000000"}
    assert "mode rules" in result.stdout


def test_b0_runs_the_literal_command_over_an_exercise_input_before_the_freeze(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    factory = RecordingFactory()
    runtime = make_runtime(root=lambda: workdir, adapter_factory=factory)
    monkeypatch.setattr(cli, "RUNTIME", runtime)
    args = match_args(workdir / "out.csv", "--profile", "b0", library=GLOBAL_LIBRARY)
    args[1] = str(EN_INPUT)

    result = invoke(args)

    assert result.exit_code == 0, result.output
    assert factory.built == []
    manifest = manifest_of(only_run(workdir))
    assert (manifest["rate_limit_tier"], manifest["rate_limit_tier_source"]) == (None, None)
    assert manifest["rate_limit_source"] == "none"
    rows = output_rows(workdir / "out.csv")
    assert len(rows) == EXERCISE_LINES
    outcomes = Counter((row["decision"], row["reason"]) for row in rows)
    assert outcomes == {
        ("not_a_material", "HEADER"): EXERCISE_HEADERS,
        ("needs_review", B0_REVIEW_REASON): EXERCISE_LINES - EXERCISE_HEADERS,
    }


def test_b0_keeps_the_typed_llm_and_never_swaps_to_the_fallback(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    factory = RecordingFactory()
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: workdir, adapter_factory=factory))

    result = invoke(match_args(workdir / "out.csv", "--profile", "b0", "--llm", "anthropic"))

    assert result.exit_code == 0, result.output
    assert factory.built == []
    manifest = manifest_of(only_run(workdir))
    assert (manifest["llm"], manifest["llm_kind"]) == ("anthropic", "anthropic")
    assert "fallback adapter" not in result.stderr


def test_b0_over_a_replay_spec_claims_no_source_run(workdir: Path) -> None:
    assert invoke(match_args(workdir / "first.csv", "--llm", "fake")).exit_code == 0
    recorded = only_run(workdir)

    result = invoke(
        match_args(workdir / "b0.csv", "--profile", "b0", "--llm", f"replay:{recorded}")
    )

    assert result.exit_code == 0, result.output
    (b0_folder,) = [path for path in (workdir / "runs").iterdir() if path != recorded]
    manifest = manifest_of(b0_folder)
    assert manifest["mode"] == "rules"
    assert manifest["source_run_id"] is None
    assert manifest["llm_run_dir"] is None


def test_b0_never_loads_the_live_cache(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx2.Request] = []
    _live_runtime(workdir, monkeypatch, requests)
    assert run_and_note("seed", match_args(workdir / "seed.csv", "--no-cache")).exit_code == 0
    assert cli.load_cache(workdir / "runs", cli.CacheKey("anthropic", HAIKU)).cache is not None
    seeded = len(requests)
    loads: list[Path] = []
    real_load = cli.load_cache

    def spy(runs_dir: Path, key: cli.CacheKey) -> cli.CacheIndex:
        loads.append(runs_dir)
        return real_load(runs_dir, key)

    monkeypatch.setattr(cli, "load_cache", spy)

    assert run_and_note("b0", match_args(workdir / "b0.csv", "--profile", "b0")).exit_code == 0

    assert loads == []
    assert len(requests) == seeded
    manifest = manifest_of(workdir / "runs" / RUN_OUTPUTS["b0"])
    assert (manifest["call_count"], manifest["cache_hits"]) == (0, 0)
    assert (manifest["mode"], manifest["source_run_id"]) == ("rules", None)


def test_a_b0_run_replays_byte_identically(workdir: Path) -> None:
    output = workdir / "out.csv"
    assert invoke(match_args(output, "--profile", "b0")).exit_code == 0
    replayed = invoke(["replay", str(only_run(workdir)), "--check", str(output)])
    assert replayed.exit_code == 0, replayed.output
    assert "byte-identical" in replayed.stdout


@pytest.mark.parametrize(
    ("source", "library"),
    [
        (Path("input") / "boq_dataset_input_en.csv", Path("data") / "oris_materials_global.csv"),
        (Path("input") / "boq_dataset_input_fr.csv", Path("data") / "oris_materials_fr.csv"),
    ],
)
def test_a_b0_run_folder_passes_check_requirements(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, source: Path, library: Path
) -> None:
    monkeypatch.chdir(ROOT)
    factory = RecordingFactory()
    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: ROOT, adapter_factory=factory))
    output, runs = workdir / "out.csv", workdir / "runs"
    args = ["--input", str(source), "--library", str(library), "--output", str(output)]
    result = invoke([*args, "--profile", "b0", "--run-dir", str(runs)])
    assert result.exit_code == 0, result.output
    assert factory.built == []
    check = load_check_requirements()
    inputs = check.Inputs(
        output=output,
        input=ROOT / source,
        library=ROOT / library,
        reference=ROOT / "data" / "boq_dataset_matched_GT.csv",
        run=only_run(workdir),
        config_dir=CONFIG,
        oris=ORIS_IN_PROCESS,
    )

    results = check.run_checks(inputs)

    failed = [(item.rq, item.detail) for item in results if item.status == check.FAIL]
    assert failed == []
    statuses = {item.rq: item.status for item in results}
    assert statuses["RQ11"] == check.PASS
    assert statuses["RQ9"] == check.PASS


def _rendered_tokens_evidence(workdir: Path, model: str = HAIKU) -> None:
    versions = {prompt_version(variant): 3380 for variant in (CANONICAL_V1, REVERSE_V1, B2)}
    data = {
        "library_sha256": hashlib.sha256(FR_LIBRARY.read_bytes()).hexdigest(),
        "model": model,
        "by_prompt_version": versions,
    }
    check = {"name": "rendered_tokens:fr", "status": "pass", "detail": "", "data": data}
    evidence = workdir / "evidence" / "doctor_2026-10-05.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"checks": [check]}), encoding="utf-8")


def test_a_live_run_records_the_measured_library_size(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rendered_tokens_evidence(workdir)
    requests: list[httpx2.Request] = []
    _live_runtime(workdir, monkeypatch, requests)

    assert run_and_note("live", match_args(workdir / "live.csv", "--no-cache")).exit_code == 0

    size = manifest_of(workdir / "runs" / RUN_OUTPUTS["live"])["library_rendered_tokens"]
    assert size["estimated"] is False
    assert size["tokens"] == 3380
    assert size["source"] == "evidence/doctor_2026-10-05.json"
    assert set(size["by_variant"]) == {prompt_version(v) for v in (CANONICAL_V1, REVERSE_V1)}


def test_a_fake_run_keeps_the_estimate(workdir: Path) -> None:
    _rendered_tokens_evidence(workdir)
    assert invoke(match_args(workdir / "out.csv", "--llm", "fake")).exit_code == 0
    assert manifest_of(only_run(workdir))["library_rendered_tokens"]["estimated"] is True


OVERSIZED_PREFIX_TOKENS = 1_000_000


def _oversized_prefix_evidence(workdir: Path) -> None:
    """Record a doctor measurement whose prefix alone costs more than any small run's cap."""
    versions = {prompt_version(v): OVERSIZED_PREFIX_TOKENS for v in (CANONICAL_V1, REVERSE_V1, B2)}
    data = {
        "library_sha256": hashlib.sha256(FR_LIBRARY.read_bytes()).hexdigest(),
        "model": HAIKU,
        "by_prompt_version": versions,
    }
    check = {"name": "rendered_tokens:fr", "status": "pass", "detail": "", "data": data}
    evidence = workdir / "evidence" / "doctor_2026-10-06.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"checks": [check]}), encoding="utf-8")


def test_a_live_run_reserves_its_prefix_from_the_doctor_measurement(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A63: a live run's reservation counts the measured prefix, so a huge one refuses calls."""
    requests: list[httpx2.Request] = []
    _live_runtime(workdir, monkeypatch, requests)
    assert run_and_note("estimated", match_args(workdir / "a.csv", "--no-cache")).exit_code == 0
    assert requests

    _oversized_prefix_evidence(workdir)
    requests.clear()
    assert run_and_note("measured", match_args(workdir / "b.csv", "--no-cache")).exit_code == 0

    assert requests == []
    with (workdir / "b.csv").open(encoding="utf-8", newline="") as handle:
        reasons = {
            row["reason"] for row in csv.DictReader(handle) if row["decision"] != "not_a_material"
        }
    assert "BUDGET_CAP" in reasons


def test_a_fake_run_reserves_from_the_estimate_despite_a_measurement(workdir: Path) -> None:
    """A63: only a run that reaches the measured model reserves from the doctor's count."""
    _oversized_prefix_evidence(workdir)
    output = workdir / "out.csv"
    assert invoke(match_args(output, "--llm", "fake")).exit_code == 0
    assert "BUDGET_CAP" not in output.read_text(encoding="utf-8")


def _always_down(spec: LLMSpec, model: str) -> FakeLLM:
    del spec
    return FakeLLM(model, ALLOWLIST, FakeBehaviour(rule=lambda n, r: Fault(FaultKind.SERVER_ERROR)))


def test_a_breaker_trip_is_recorded_and_replayed_from_the_manifest(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORIS_BREAKER_CONSECUTIVE_FAILURES", "1")
    monkeypatch.setenv("ORIS_MAX_RETRIES", "0")
    monkeypatch.setattr(
        cli, "RUNTIME", make_runtime(root=lambda: workdir, adapter_factory=_always_down)
    )
    output = workdir / "out.csv"

    assert invoke(match_args(output, "--llm", "fake")).exit_code == 3
    folder = only_run(workdir)
    declined = manifest_of(folder)["declined_attempts"]
    assert declined
    assert {record["reason"] for record in declined} == {"LLM_UNAVAILABLE"}
    assert "LLM_UNAVAILABLE" in output.read_text(encoding="utf-8")

    monkeypatch.setattr(cli, "RUNTIME", make_runtime(root=lambda: workdir))
    replayed = invoke(["replay", str(folder), "--check", str(output)])
    assert replayed.exit_code == 3, replayed.output
    assert "byte-identical" in replayed.output
    again = invoke(match_args(workdir / "again.csv", "--llm", f"replay:{folder}"))
    assert again.exit_code == 3
    newest = max((workdir / "runs").iterdir(), key=lambda path: path.name != folder.name)
    assert manifest_of(newest)["declined_attempts"] == declined
