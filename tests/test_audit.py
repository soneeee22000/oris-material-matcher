"""Run folder: manifest.json, calls.jsonl, audit.jsonl and prompts/ (DESIGN.md §9.6, §11.5)."""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.boq import BoqFile, LineKind
from oris_matcher.io.audit import (
    CodeVersion,
    ManifestContext,
    build_manifest,
    code_version,
    is_committable,
    require_inside_root,
    run_folder,
    write_run,
)
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.io.writer import render_csv
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, default_answer
from oris_matcher.llm.recording import MemoryCallSink, read_calls_jsonl
from oris_matcher.llm.replay_llm import RecordedRun, ReplayLLM
from oris_matcher.llm.wrapper import BudgetLedger, LLMWrapper, WrapperDeps
from oris_matcher.service import MatchService, RunOptions, RunProfile, RunResult
from oris_matcher.settings import Settings, load_models_config, load_pricing

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
LIBRARIES = {
    "global": ROOT / "data" / "oris_materials_global.csv",
    "fr": ROOT / "data" / "oris_materials_fr.csv",
}
FR_INPUT = ROOT / "input" / "boq_dataset_input_fr.csv"
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
PRICING = load_pricing(CONFIG / "pricing.toml")
HAIKU = "claude-haiku-4-5-20251001"
FIXED_NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None, config_dir=CONFIG, libraries=LIBRARIES)  # type: ignore[call-arg]
SERVICE = MatchService.from_settings(SETTINGS)
CODE = CodeVersion(sha="a" * 40, dirty=False)
SPLIT_SHA = "b" * 64
MANIFEST_KEYS = {
    "run_id",
    "mode",
    "source_run_id",
    "profile",
    "requested_model",
    "fallback_model",
    "served_models",
    "library_id",
    "library_sha256",
    "library_rendered_tokens",
    "prompt_version",
    "enrichment_sha256",
    "config_sha256",
    "policy_id",
    "policy_resolution",
    "path_mode",
    "encoding",
    "code_sha",
    "code_dirty",
    "split_sha256",
    "price_date",
    "temperature",
    "max_tokens",
    "sdk_versions",
    "otel_semconv_version",
    "rate_limit_tier",
    "rate_limit_headers",
    "rate_limit_source",
    "spend_usd",
    "attributed_cost_usd",
    "cache_hits",
    "cache_read_tokens",
    "cache_write_tokens",
    "settings_effective",
    "excel_bom",
    "exit_code",
    "decision_counts",
    "reason_counts",
    "wall_clock_s",
    "n_routed",
    "fallback_engaged",
}
AUDIT_KEYS = {
    "line_id",
    "position",
    "item_no",
    "decision",
    "rule",
    "reason",
    "signals",
    "top1",
    "top2",
    "attribute_result",
    "raw_line_response",
    "call_ids",
    "context",
    "flags",
}


def _answers(boq: BoqFile) -> dict[str, dict[str, Any]]:
    codes = [row.code for row in SERVICE.library("fr").rows]
    answers: dict[str, dict[str, Any]] = {}
    for line in boq.lines:
        if line.kind == LineKind.ITEM:
            transport = transport_id(line)
            evidence = line.short.split()[0] if line.short.split() else ""
            answers[transport] = {
                **default_answer(transport),
                "evidence": evidence,
                "top1": codes[line.position % len(codes)],
            }
    return answers


def _wrapper(adapter: Any) -> LLMWrapper:
    deps = WrapperDeps(sink=MemoryCallSink(), now=lambda: FIXED_NOW)
    return LLMWrapper(adapter, PRICING, BudgetLedger(100.0), None, deps)


async def _live() -> RunResult:
    boq = read_boq(FR_INPUT)
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=_answers(boq)))
    return await SERVICE.match(
        boq, "fr", profile=RunProfile.B3, llm=_wrapper(fake), options=RunOptions(run_id="r1")
    )


def _context() -> ManifestContext:
    return ManifestContext(
        root=ROOT, settings=SETTINGS, pricing=PRICING, code=CODE, split_sha256=SPLIT_SHA
    )


async def test_run_folder_layout(tmp_path: Path) -> None:
    result = await _live()
    folder = write_run(result, tmp_path, build_manifest(result, _context()))
    assert folder == run_folder(tmp_path, "r1") == tmp_path / "r1"
    assert {path.name for path in folder.iterdir()} == {
        "manifest.json",
        "calls.jsonl",
        "audit.jsonl",
        "prompts",
    }


async def test_manifest_fields_sorted_keys_and_explicit_nulls(tmp_path: Path) -> None:
    result = await _live()
    folder = write_run(result, tmp_path, build_manifest(result, _context()))
    text = (folder / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(text)
    assert set(manifest) >= MANIFEST_KEYS
    assert text == json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert manifest["rate_limit_tier"] is None
    assert manifest["rate_limit_headers"] is None
    assert manifest["enrichment_sha256"] is None
    assert manifest["code_sha"] == CODE.sha
    assert manifest["code_dirty"] is False
    assert manifest["split_sha256"] == SPLIT_SHA
    assert manifest["mode"] == "fake"
    assert manifest["policy_resolution"] == "fallback_strictest"
    assert manifest["library_sha256"] == SERVICE.library("fr").sha256
    assert manifest["price_date"] == "2026-10-05"
    assert manifest["otel_semconv_version"]
    assert set(manifest["sdk_versions"]) == {"anthropic", "openai"}
    paths = list(manifest["config_sha256"])
    assert "config/models.toml" in paths
    assert "src/oris_matcher/prompts/v1/glossary.yaml" in paths
    assert all("\\" not in path and not Path(path).is_absolute() for path in paths)
    effective = manifest["settings_effective"]
    assert "anthropic_api_key" not in effective
    assert effective["config_dir"] == "config"
    assert effective["libraries"]["fr"] == "data/oris_materials_fr.csv"
    config_bytes = (CONFIG / "models.toml").read_bytes()
    assert (
        manifest["config_sha256"]["config/models.toml"] == hashlib.sha256(config_bytes).hexdigest()
    )


async def test_calls_jsonl_round_trips_and_replays_byte_identical(tmp_path: Path) -> None:
    result = await _live()
    folder = write_run(result, tmp_path, build_manifest(result, _context()))
    records = read_calls_jsonl(folder / "calls.jsonl")
    assert records == list(result.calls)
    replay = ReplayLLM(RecordedRun.from_calls_jsonl(folder / "calls.jsonl"), HAIKU, ALLOWLIST)
    boq = read_boq(FR_INPUT)
    replayed = await SERVICE.match(
        boq,
        "fr",
        profile=RunProfile.B3,
        llm=_wrapper(replay),
        options=RunOptions(run_id="r2", source_run_id="r1"),
    )
    assert render_csv(replayed) == render_csv(result)
    manifest = build_manifest(replayed, _context())
    assert manifest["mode"] == "replay"
    assert manifest["source_run_id"] == "r1"


async def test_system_blocks_are_stored_once_by_hash(tmp_path: Path) -> None:
    result = await _live()
    folder = write_run(result, tmp_path, build_manifest(result, _context()))
    stored = {path.stem: path.read_bytes() for path in (folder / "prompts").iterdir()}
    assert len(stored) == 2
    for sha, content in stored.items():
        assert hashlib.sha256(content).hexdigest() == sha
    assert {record.system_blocks_sha256 for record in result.calls} == set(stored)


async def test_audit_jsonl_has_one_record_per_output_line(tmp_path: Path) -> None:
    result = await _live()
    folder = write_run(result, tmp_path, build_manifest(result, _context()))
    lines = (folder / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["line_id"] for record in records] == [item.line.line_id for item in result.lines]
    for record, item in zip(records, result.lines, strict=True):
        assert set(record) >= AUDIT_KEYS
        assert record["reason"] == item.decision.reason
        assert record["call_ids"] == list(item.call_ids)
        if item.line.kind == LineKind.ITEM:
            assert len(record["raw_line_response"]) == 2
            assert all(
                json.loads(raw)["id"] == transport_id(item.line)
                for raw in record["raw_line_response"]
            )
        else:
            assert record["raw_line_response"] == []
        assert record["context"] in {"section_path", "none"}


def test_code_version_reads_git_through_the_runner() -> None:
    calls: list[Sequence[str]] = []

    def runner(args: Sequence[str], root: Path) -> str:
        calls.append(tuple(args))
        return "f" * 40 + "\n" if "rev-parse" in args else " M src/x.py\n"

    version = code_version(ROOT, runner=runner)
    assert version == CodeVersion(sha="f" * 40, dirty=True)
    assert calls == [("git", "rev-parse", "HEAD"), ("git", "status", "--porcelain")]


def test_code_version_is_null_when_git_is_unavailable() -> None:
    def runner(args: Sequence[str], root: Path) -> str:
        raise OSError("no git")

    assert code_version(ROOT, runner=runner) == CodeVersion(sha=None, dirty=None)


async def test_rate_limit_tier_and_headers_are_recorded_when_seen() -> None:
    result = await _live()
    headers = {"anthropic-ratelimit-requests-limit": "1000"}
    context = ManifestContext(
        root=ROOT,
        settings=SETTINGS,
        pricing=PRICING,
        code=CODE,
        rate_limit_tier="2",
        rate_limit_headers=headers,
    )
    manifest = build_manifest(result, context)
    assert manifest["rate_limit_tier"] == "2"
    assert manifest["rate_limit_headers"] == headers


def test_only_runs_under_runs_submission_are_committable(tmp_path: Path) -> None:
    assert is_committable(Path("runs/submission/r1"), tmp_path)
    assert is_committable(tmp_path / "runs" / "submission", tmp_path)
    assert not is_committable(Path("runs"), tmp_path)
    assert not is_committable(tmp_path.parent / "runs" / "submission", tmp_path)


def test_a_committable_run_may_not_name_a_host_path(tmp_path: Path) -> None:
    require_inside_root([Path("input/x.csv"), tmp_path / "data" / "lib.csv"], tmp_path)
    with pytest.raises(ValueError, match="outside the repository"):
        require_inside_root([tmp_path.parent / "elsewhere.csv"], tmp_path)
