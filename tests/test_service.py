"""MatchService end to end under FakeLLM and ReplayLLM (DESIGN.md §5.2, §9.5, §10.6, §11.3)."""

import asyncio
import csv
import dataclasses
import json
import logging
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from oris_matcher.candidates.whole_library import WholeLibrary
from oris_matcher.domain.attributes import AttrResult, compare, extract
from oris_matcher.domain.batching import plan_batches, transport_id
from oris_matcher.domain.boq import BoqFile, BoqLine, LineKind
from oris_matcher.domain.decision import (
    NOT_A_MATERIAL_REASONS,
    Decision,
    ReasonCode,
    Rule,
    SignalName,
    Signals,
    Threshold,
    candidate_thresholds,
    is_service_unit,
    low_signal_reason,
    strictest_threshold,
)
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.io.writer import render_csv
from oris_matcher.llm.base import LLMRequest, LLMResult
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, Fault, FaultKind, default_answer
from oris_matcher.llm.recording import MemoryCallSink
from oris_matcher.llm.replay_llm import RecordedRun, ReplayLLM, ResponseCache
from oris_matcher.llm.wrapper import BudgetLedger, LLMWrapper, WrapperDeps, WrapperPolicy
from oris_matcher.prompts.v1.render import B2_LIBRARY_TEMPLATE, read_template
from oris_matcher.service import (
    B2_THRESHOLD,
    EXIT_LLM_UNAVAILABLE,
    EXIT_OK,
    MIN_RUN_BUDGET_USD,
    RULES_MODEL,
    MatchService,
    PolicyQuery,
    PolicyResolution,
    RunMode,
    RunOptions,
    RunProfile,
    RunResult,
    as_override,
    budget_cap_usd,
    cache_source_run_id,
    has_supply_marker,
    make_run_id,
    resolve_policy,
    run_mode,
)
from oris_matcher.settings import (
    PolicyConfig,
    PolicyEntry,
    Settings,
    load_models_config,
    load_policy,
    load_pricing,
    load_supply_markers,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
LIBRARIES = {
    "global": ROOT / "data" / "oris_materials_global.csv",
    "fr": ROOT / "data" / "oris_materials_fr.csv",
}
EN_INPUT = ROOT / "input" / "boq_dataset_input_en.csv"
FR_INPUT = ROOT / "input" / "boq_dataset_input_fr.csv"
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
PRICING = load_pricing(CONFIG / "pricing.toml")
MARKERS = load_supply_markers(CONFIG / "supply_markers.yaml").markers
HAIKU = "claude-haiku-4-5-20251001"
GPT = "gpt-4o-mini-2024-07-18"
FIXED_NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
BIG_CAP = 100.0
CONFIDENCE = 95
RUN_ID = "test-run"


def make_settings(**overrides: Any) -> Settings:
    overrides.setdefault("config_dir", CONFIG)
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        libraries=LIBRARIES,
        **overrides,
    )


SERVICE = MatchService.from_settings(make_settings())
SELECTION = ROOT / "eval" / "selection_v1.json"
EMPTY_POLICY = "policies: {}\n"


def selected_threshold_id() -> str:
    """Return the threshold the G2 dev selection chose (eval/selection_v1.json)."""
    payload = json.loads(SELECTION.read_text(encoding="utf-8"))
    return str(payload["selected"]["threshold_id"])


def service_without_policy(tmp_path: Path) -> MatchService:
    """Build the service from a copy of config/ whose policy file lists no entry."""
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    (config / "policy.yaml").write_text(EMPTY_POLICY, encoding="utf-8")
    return MatchService.from_settings(make_settings(config_dir=config))


async def _no_sleep(seconds: float) -> None:
    del seconds


def make_wrapper(
    adapter: Any, *, policy: WrapperPolicy | None = None, sink: MemoryCallSink | None = None
) -> LLMWrapper:
    deps = WrapperDeps(sink=sink or MemoryCallSink(), sleep=_no_sleep, now=lambda: FIXED_NOW)
    return LLMWrapper(adapter, PRICING, BudgetLedger(BIG_CAP), policy, deps)


def _pick_row(line: BoqLine, library: Library) -> LibraryRow:
    attributes = extract(f"{line.short} {line.long}")
    usable = [row for row in library.rows if not library.is_never_match(row.code)]
    agreeing = [row for row in usable if compare(attributes, row.attributes) == AttrResult.AGREE]
    specific = [row for row in agreeing if not row.is_blank_leaf]
    pool = specific or agreeing or usable
    return pool[line.position % len(pool)]


def answers_for(boq: BoqFile, library: Library) -> dict[str, dict[str, Any]]:
    """Return one plausible answer per routed line, keyed by transport id; no labels used."""
    units = SERVICE.resources.service_units
    answers: dict[str, dict[str, Any]] = {}
    for line in boq.lines:
        if line.kind != LineKind.ITEM:
            continue
        transport = transport_id(line)
        words = line.short.split()
        base = {**default_answer(transport), "evidence": words[0] if words else ""}
        if is_service_unit(line.unit, units):
            base.update(kind="non_material", nm_category="service", top1="")
        else:
            base.update(top1=_pick_row(line, library).code, confidence=CONFIDENCE)
        answers[transport] = base
    return answers


def fake_for(boq: BoqFile, library_id: str, **behaviour: Any) -> FakeLLM:
    answers = answers_for(boq, SERVICE.library(library_id))
    return FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=answers, **behaviour))


def seen_transport_ids(fake: FakeLLM) -> Counter[str]:
    return Counter(line_id for request in fake.calls for line_id in request.line_ids)


async def run(
    boq: BoqFile,
    library_id: str,
    llm: LLMWrapper,
    profile: RunProfile = RunProfile.B3,
    service: MatchService = SERVICE,
    **options: Any,
) -> RunResult:
    options.setdefault("run_id", RUN_ID)
    return await service.match(
        boq, library_id, profile=profile, llm=llm, options=RunOptions(**options)
    )


@pytest.mark.parametrize(("source", "library_id"), [(EN_INPUT, "global"), (FR_INPUT, "fr")])
async def test_full_file_b3_strictest(
    source: Path, library_id: str, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    service = service_without_policy(tmp_path)
    boq = read_boq(source)
    library = service.library(library_id)
    fake = fake_for(boq, library_id)
    with caplog.at_level(logging.WARNING):
        result = await run(boq, library_id, make_wrapper(fake), service=service)

    assert [item.line.line_id for item in result.lines] == [line.line_id for line in boq.lines]
    assert [item.line.position for item in result.lines] == list(range(len(boq.lines)))
    triples = {(r.material_type, r.material_usage, r.material_subtype) for r in library.rows}
    matched = [item for item in result.lines if item.decision.decision == Decision.MATCHED]
    assert matched
    for item in matched:
        row = item.decision.row
        assert row is not None
        assert row is library.by_code[item.decision.top1]
        assert (row.material_type, row.material_usage, row.material_subtype) in triples
    skipped = [item for item in result.lines if item.decision.decision == Decision.NOT_A_MATERIAL]
    assert skipped
    assert {item.decision.reason for item in skipped} <= set(NOT_A_MATERIAL_REASONS)

    run_total = sum(record.cost_usd for record in result.calls)
    assert run_total > 0
    assert sum(item.cost_usd for item in result.lines) == pytest.approx(run_total, abs=1e-12)
    assert result.attributed_cost_usd == pytest.approx(run_total, abs=1e-12)

    assert len(result.audit) == len(boq.lines)
    assert [record["line_id"] for record in result.audit] == [line.line_id for line in boq.lines]

    assert result.policy.resolution == PolicyResolution.FALLBACK_STRICTEST
    assert result.policy.threshold == strictest_threshold(2)
    assert "fallback_strictest" in caplog.text
    assert result.manifest["policy_resolution"] == "fallback_strictest"
    assert result.exit_code == EXIT_OK

    routed = [line for line in boq.lines if line.kind == LineKind.ITEM]
    assert seen_transport_ids(fake) == Counter({transport_id(line): 2 for line in routed})
    prefixes = {request.system_blocks for request in fake.calls}
    assert len(prefixes) == 2


async def test_rule_rows_and_llm_rows_carry_their_provenance() -> None:
    boq = read_boq(EN_INPUT)
    result = await run(boq, "global", make_wrapper(fake_for(boq, "global")))
    for item in result.lines:
        if item.line.kind == LineKind.ITEM:
            assert item.call_ids
            assert item.model == HAIKU
            assert len(item.raw_line_responses) == 2
        else:
            assert item.model == RULES_MODEL
            assert item.cost_usd == 0.0
            assert item.latency_ms == 0
            assert item.call_ids == ()
    versions = result.prompt_versions
    assert len(versions) == 2
    assert len(set(versions)) == 2


async def test_select_routes_and_outputs_only_the_selected_lines() -> None:
    boq = read_boq(EN_INPUT)
    items = [line for line in boq.lines if line.kind == LineKind.ITEM]
    chosen = [items[0], items[1], items[40], items[41], items[200]]
    headers = [line for line in boq.lines if line.kind == LineKind.HEADER]
    select = {line.line_id for line in [*chosen, *headers]}
    fake = fake_for(boq, "global")
    result = await run(boq, "global", make_wrapper(fake), select=select)

    expected = [line.line_id for line in boq.lines if line.line_id in select]
    assert [item.line.line_id for item in result.lines] == expected
    assert set(seen_transport_ids(fake)) == {transport_id(line) for line in chosen}
    assert len(result.audit) == len(expected)
    paths = {item.line.line_id: item.line.section_path for item in result.lines}
    assert all(paths[line.line_id] == line.section_path for line in chosen)


class TrackingLLM:
    """Wraps FakeLLM and records when each call starts and ends."""

    def __init__(self, inner: FakeLLM) -> None:
        self.inner = inner
        self.events: list[tuple[str, tuple[str, ...]]] = []
        self.in_flight = 0
        self.peak = 0

    async def complete(self, req: LLMRequest) -> LLMResult:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.events.append(("start", req.line_ids))
        for _ in range(3):
            await asyncio.sleep(0)
        result = await self.inner.complete(req)
        self.events.append(("end", req.line_ids))
        self.in_flight -= 1
        return result


async def test_first_batch_goes_alone_then_the_rest_fan_out() -> None:
    boq = read_boq(EN_INPUT)
    tracker = TrackingLLM(fake_for(boq, "global"))
    await run(boq, "global", make_wrapper(tracker))

    first_batch = plan_batches(boq.lines)[0]
    assert tracker.events[0] == ("start", first_batch.transport_ids)
    assert tracker.events[1] == ("end", first_batch.transport_ids)
    assert 1 < tracker.peak <= SERVICE.resources.settings.concurrency


async def test_replay_of_a_recorded_run_is_byte_identical() -> None:
    boq = read_boq(EN_INPUT)
    live = await run(boq, "global", make_wrapper(fake_for(boq, "global")))
    replay = ReplayLLM(RecordedRun.from_records(live.calls), HAIKU, ALLOWLIST, strict=True)
    replayed = await run(boq, "global", make_wrapper(replay))

    assert render_csv(replayed) == render_csv(live)
    assert all(record.cache_hit for record in replayed.calls)
    assert replayed.manifest["mode"] == "replay"
    assert live.manifest["mode"] == "fake"


async def test_breaker_trip_gives_llm_unavailable_and_exit_code_3() -> None:
    boq = read_boq(EN_INPUT)

    def always_down(call_no: int, req: LLMRequest) -> Fault:
        del call_no, req
        return Fault(FaultKind.SERVER_ERROR)

    fake = fake_for(boq, "global", rule=always_down)
    policy = WrapperPolicy(max_retries=0, breaker_threshold=3)
    result = await run(boq, "global", make_wrapper(fake, policy=policy))

    assert len(result.lines) == len(boq.lines)
    reasons = Counter(item.decision.reason for item in result.lines)
    assert reasons[ReasonCode.LLM_UNAVAILABLE] > 0
    assert reasons["LLM_FAILURE:api_error"] > 0
    assert result.exit_code == EXIT_LLM_UNAVAILABLE
    assert not any(item.decision.decision == Decision.MATCHED for item in result.lines)


async def test_b2_profile_single_raw_pass_matches_every_valid_answer() -> None:
    boq = read_boq(EN_INPUT)
    items = [line for line in boq.lines if line.kind == LineKind.ITEM][:12]
    fake = fake_for(boq, "global")
    result = await run(
        boq,
        "global",
        make_wrapper(fake),
        profile=RunProfile.B2,
        select={line.line_id for line in items},
    )

    assert seen_transport_ids(fake) == Counter({transport_id(line): 1 for line in items})
    b2_header = read_template(B2_LIBRARY_TEMPLATE).split("$library", 1)[0]
    assert all(request.system_blocks[1].text.startswith(b2_header) for request in fake.calls)
    assert all('"path"' not in request.user_payload for request in fake.calls)
    assert result.policy.resolution == PolicyResolution.B2_MATCH_ALL
    for item in result.lines:
        if item.line.unit.strip() in SERVICE.resources.service_units:
            assert item.decision.decision == Decision.NEEDS_REVIEW
        else:
            assert item.decision.decision == Decision.MATCHED
            assert item.decision.reason == f"SIGNAL:{B2_THRESHOLD.threshold_id}"
    assert len(result.prompt_versions) == 1


async def test_truncated_fields_are_capped_for_the_prompt_and_flagged() -> None:
    long_text = "Concrete " * 600
    data = (
        "Item No.,Short Description,Long Description,Unit,BoQ Qty\r\n"
        f"01.01.0010.,Concrete C30/37 walls,{long_text},m³,10\r\n"
    ).encode()
    boq = read_boq(data)
    fake = fake_for(boq, "global")
    result = await run(boq, "global", make_wrapper(fake))
    assert "TRUNCATED" in result.lines[0].flags
    assert result.audit[0]["flags"] == ["TRUNCATED"]
    assert all(len(request.user_payload) < len(long_text) for request in fake.calls)


def test_more_passes_than_renderings_is_refused() -> None:
    service = MatchService.from_settings(make_settings(passes_k=3))
    boq = read_boq(EN_INPUT)
    fake = fake_for(boq, "global")
    with pytest.raises(ValueError, match="passes"):
        asyncio.run(service.match(boq, "global", profile=RunProfile.B3, llm=make_wrapper(fake)))


def test_unknown_library_id_is_refused() -> None:
    with pytest.raises(KeyError, match="nope"):
        SERVICE.library("nope")


def test_policy_resolution_exact_hit_override_and_no_path() -> None:
    library = SERVICE.library("global")
    entry = PolicyEntry(policy_id="T3", certified_by="dev_selection")
    config = PolicyConfig(policies={HAIKU: {library.sha256: entry}})
    exact = resolve_policy(config, PolicyQuery(HAIKU, library.sha256, 2, path_derived=True))
    assert exact.resolution == PolicyResolution.EXACT
    assert exact.threshold == candidate_thresholds(2)[2]
    assert exact.certified_by == "dev_selection"

    other = resolve_policy(config, PolicyQuery("gpt-4o-mini-2024-07-18", library.sha256, 2))
    assert other.resolution == PolicyResolution.FALLBACK_STRICTEST
    assert other.threshold == strictest_threshold(2)

    no_path = resolve_policy(config, PolicyQuery(HAIKU, library.sha256, 2, path_derived=False))
    assert no_path.resolution == PolicyResolution.NO_PATH_STRICTEST
    assert no_path.threshold == strictest_threshold(2)

    loose = candidate_thresholds(2)[-1]
    forced = resolve_policy(config, PolicyQuery(HAIKU, library.sha256, 2, override=loose))
    assert forced.resolution == PolicyResolution.OVERRIDE
    assert forced.threshold == loose


async def test_repo_policy_resolves_exact_for_haiku_and_the_global_library() -> None:
    """The shipped config/policy.yaml certifies the G2 dev selection for (Haiku, global)."""
    boq = read_boq(EN_INPUT)
    result = await run(boq, "global", make_wrapper(fake_for(boq, "global")))

    assert result.policy.resolution == PolicyResolution.EXACT
    assert result.policy.threshold.threshold_id == selected_threshold_id()
    assert result.manifest["policy_resolution"] == "exact"
    assert result.manifest["policy_id"] == selected_threshold_id()
    assert result.manifest["policy_certified_by"] == "dev_selection"


def test_repo_policy_falls_back_for_a_changed_library_and_for_gpt() -> None:
    """Any pair the shipped policy does not list resolves to the strictest threshold."""
    config = load_policy(CONFIG / "policy.yaml")
    library = SERVICE.library("global")
    changed = resolve_policy(config, PolicyQuery(HAIKU, "0" * 64, 2, path_derived=True))
    gpt = resolve_policy(config, PolicyQuery(GPT, library.sha256, 2, path_derived=True))
    exact = resolve_policy(config, PolicyQuery(HAIKU, library.sha256, 2, path_derived=True))

    assert changed.resolution == PolicyResolution.FALLBACK_STRICTEST
    assert gpt.resolution == PolicyResolution.FALLBACK_STRICTEST
    assert changed.threshold == gpt.threshold == strictest_threshold(2)
    assert exact.resolution == PolicyResolution.EXACT


def test_policy_entry_naming_an_unknown_threshold_is_refused() -> None:
    library = SERVICE.library("global")
    entry = PolicyEntry(policy_id="T99", certified_by="dev_selection")
    config = PolicyConfig(policies={HAIKU: {library.sha256: entry}})
    with pytest.raises(ValueError, match="T99"):
        resolve_policy(config, PolicyQuery(HAIKU, library.sha256, 2))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Supply and lay geotextile", True),
        ("Fourniture et pose de bordures", True),
        ("Oversupply of water", False),
        ("Labour only, no materials", False),
        ("y compris livraison sur site", True),
    ],
)
def test_supply_markers_match_whole_words(text: str, expected: bool) -> None:
    assert has_supply_marker(text, MARKERS) is expected


def test_run_id_is_deterministic_for_the_same_moment_and_seed() -> None:
    first = make_run_id(FIXED_NOW, "seed")
    assert first == make_run_id(FIXED_NOW, "seed")
    assert first != make_run_id(FIXED_NOW, "other")
    assert first.startswith("20261005T100000Z-")


async def test_override_threshold_is_recorded() -> None:
    boq = read_boq(EN_INPUT)
    loose = Threshold(
        "T8", Signals(2, AttrResult.NO_EVIDENCE, candidate_thresholds(2)[-1].minimum.confidence)
    )
    result = await run(boq, "global", make_wrapper(fake_for(boq, "global")), threshold=loose)
    assert result.policy.resolution == PolicyResolution.OVERRIDE
    assert result.manifest["policy_id"] == "T8"
    strict = await run(boq, "global", make_wrapper(fake_for(boq, "global")))
    matched = sum(item.decision.decision == Decision.MATCHED for item in result.lines)
    strict_matched = sum(item.decision.decision == Decision.MATCHED for item in strict.lines)
    assert matched >= strict_matched


def test_whole_library_offers_every_row_with_full_inclusion() -> None:
    library = SERVICE.library("fr")
    provider = WholeLibrary(library)
    lines = read_boq(FR_INPUT).lines[:3]
    assert provider.candidates(lines) == library.rows
    assert provider.inclusion(lines) == 1.0
    assert provider.name == "whole_library"


class LivePort:
    """A port that is neither FakeLLM nor ReplayLLM, as a live adapter is."""

    def __init__(self, inner: FakeLLM) -> None:
        self.inner = inner

    async def complete(self, req: LLMRequest) -> LLMResult:
        return await self.inner.complete(req)


def _cached_wrapper(adapter: Any, cache: ResponseCache) -> LLMWrapper:
    deps = WrapperDeps(sleep=_no_sleep, now=lambda: FIXED_NOW, cache=cache)
    return LLMWrapper(adapter, PRICING, BudgetLedger(BIG_CAP), None, deps)


def _first_items(boq: BoqFile, count: int) -> set[str]:
    items = [line for line in boq.lines if line.kind == LineKind.ITEM][:count]
    return {line.line_id for line in items}


async def test_mode_is_live_without_hits_and_cached_with_hits_naming_the_source_run() -> None:
    boq = read_boq(FR_INPUT)
    select = _first_items(boq, 4)
    cold_wrapper = _cached_wrapper(LivePort(fake_for(boq, "fr")), ResponseCache())
    cold = await run(boq, "fr", cold_wrapper, select=select)
    assert cold.manifest["mode"] == "live"
    assert cold.manifest["cache_hits"] == 0
    assert cold.manifest["source_run_id"] is None

    cache = ResponseCache.from_records(cold.calls)
    sources = {record.call_id: "20261005T090000Z-cold" for record in cold.calls}
    warm_wrapper = _cached_wrapper(LivePort(fake_for(boq, "fr")), cache)
    warm = await run(boq, "fr", warm_wrapper, select=select, cache_sources=sources)
    assert warm.manifest["mode"] == "cached"
    assert warm.manifest["cache_hits"] == warm.manifest["call_count"] > 0
    assert warm.manifest["source_run_id"] == "20261005T090000Z-cold"


def test_run_mode_and_cache_sources_units() -> None:
    fake = FakeLLM(HAIKU, ALLOWLIST)
    replay = ReplayLLM(RecordedRun.from_records(()), HAIKU, ALLOWLIST)
    assert run_mode(fake, ()) == RunMode.FAKE
    assert run_mode(replay, ()) == RunMode.REPLAY
    assert run_mode(LivePort(fake), ()) == RunMode.LIVE
    assert cache_source_run_id((), {}) is None


async def test_manifest_records_wall_clock_and_routed_lines() -> None:
    ticks = iter([10.0, 12.5])
    service = MatchService(SERVICE.resources, timer=lambda: next(ticks))
    boq = read_boq(EN_INPUT)
    result = await service.match(
        boq, "global", profile=RunProfile.B3, llm=make_wrapper(fake_for(boq, "global"))
    )
    routed = sum(line.kind == LineKind.ITEM for line in boq.lines)
    assert result.manifest["wall_clock_s"] == 2.5
    assert result.manifest["n_routed"] == routed == result.manifest["routed_count"]


def _always_down(call_no: int, req: LLMRequest) -> Fault:
    del call_no, req
    return Fault(FaultKind.SERVER_ERROR)


async def test_breaker_trip_hands_pending_lines_to_the_fallback() -> None:
    boq = read_boq(EN_INPUT)
    primary = fake_for(boq, "global", rule=_always_down)
    answers = answers_for(boq, SERVICE.library("global"))
    backup = FakeLLM(GPT, ALLOWLIST, FakeBehaviour(answers=answers))
    policy = WrapperPolicy(max_retries=0, breaker_threshold=3)
    result = await run(boq, "global", make_wrapper(primary, policy=policy), fallback=backup)

    reasons = Counter(item.decision.reason for item in result.lines)
    assert reasons[ReasonCode.LLM_UNAVAILABLE] == 0
    assert result.exit_code == EXIT_OK
    assert backup.calls
    assert {request.model for request in backup.calls} == {GPT}
    assert {request.provider for request in backup.calls} == {"openai"}
    served = [item for item in result.lines if item.model == GPT]
    assert served
    manifest = result.manifest
    assert manifest["fallback_engaged"] is True
    assert manifest["fallback_line_count"] == len(served)
    assert manifest["fallback_policy_resolution"] == "fallback_strictest"
    assert manifest["fallback_threshold_id"] == strictest_threshold(2).threshold_id
    assert manifest["fallback_model"] == GPT
    assert manifest["policy_resolution"] == "fallback_strictest"
    assert manifest["threshold_id"] == strictest_threshold(2).threshold_id
    assert result.policy.resolution == PolicyResolution.FALLBACK_STRICTEST
    assert GPT in manifest["served_models"]

    replay = ReplayLLM(RecordedRun.from_records(result.calls), HAIKU, ALLOWLIST, strict=True)
    replayed = await run(boq, "global", make_wrapper(replay, policy=policy), fallback=replay)
    assert render_csv(replayed) == render_csv(result)


async def test_breaker_trip_without_a_fallback_records_no_fallback() -> None:
    boq = read_boq(EN_INPUT)
    primary = fake_for(boq, "global", rule=_always_down)
    policy = WrapperPolicy(max_retries=0, breaker_threshold=3)
    result = await run(boq, "global", make_wrapper(primary, policy=policy))
    assert result.exit_code == EXIT_LLM_UNAVAILABLE
    assert result.manifest["fallback_engaged"] is False
    assert result.manifest["fallback_line_count"] == 0


async def test_a_policy_file_hit_is_an_override_with_unverified_certification() -> None:
    library = SERVICE.library("global")
    entry = PolicyEntry(policy_id="T3", certified_by="dev_selection")
    config = PolicyConfig(policies={HAIKU: {library.sha256: entry}})
    resources = dataclasses.replace(SERVICE.resources, policy=config, policy_override=True)
    boq = read_boq(EN_INPUT)
    result = await MatchService(resources).match(
        boq, "global", profile=RunProfile.B3, llm=make_wrapper(fake_for(boq, "global"))
    )
    assert result.policy.resolution == PolicyResolution.OVERRIDE
    assert result.manifest["policy_id"] == "T3"
    assert result.manifest["policy_certified_by"] is None
    assert result.manifest["policy_claimed_certified_by"] == "dev_selection"
    unchanged = as_override(resolve_policy(config, PolicyQuery(GPT, library.sha256, 2)))
    assert unchanged.resolution == PolicyResolution.FALLBACK_STRICTEST


ONE_LINE = (
    b"Item No.,Short Description,Long Description,Unit,BoQ Qty\r\n"
    b"01.01.0010.,Ready-mix concrete C30/37,,m3,10\r\n"
)


def test_the_cap_has_a_per_run_floor() -> None:
    settings = SERVICE.resources.settings
    assert budget_cap_usd(settings, 1) == MIN_RUN_BUDGET_USD
    per_line = settings.budget_usd_per_100_lines / 100
    assert budget_cap_usd(settings, 1000) == pytest.approx(per_line * 1000)


@pytest.mark.parametrize("library_id", ["global", "fr"])
async def test_a_one_line_run_fits_its_budget_floor(library_id: str) -> None:
    boq = read_boq(ONE_LINE)
    cap = budget_cap_usd(SERVICE.resources.settings, 1)
    deps = WrapperDeps(sleep=_no_sleep, now=lambda: FIXED_NOW)
    wrapper = LLMWrapper(fake_for(boq, library_id), PRICING, BudgetLedger(cap), None, deps)
    result = await run(boq, library_id, wrapper)
    assert result.lines[0].decision.reason != ReasonCode.BUDGET_CAP
    assert len(result.lines[0].raw_line_responses) == 2


GROUND_TRUTH = ROOT / "data" / "boq_dataset_matched_GT.csv"
SMALL_BOQ = ROOT / "tests" / "fixtures" / "cli" / "small_boq.csv"
B0_REVIEW_REASON = "LOW_SIGNAL:v+b+confidence"
EXERCISE_LINES = 319
EXERCISE_HEADERS = 37
STRUCTURAL_RULES = frozenset({Rule.D0, Rule.D0A, Rule.D0B})


def labelled_item_numbers() -> list[tuple[str, bool]]:
    """Return each reference row's item number and whether it carries a library label."""
    with GROUND_TRUTH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [(row["Item No."], bool(row["material_type"].strip())) for row in rows]


def silent_fake() -> FakeLLM:
    return FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour())


def test_b0_reason_is_every_signal_failed() -> None:
    assert low_signal_reason(SignalName) == B0_REVIEW_REASON


@pytest.mark.parametrize(("source", "library_id"), [(EN_INPUT, "global"), (FR_INPUT, "fr")])
async def test_b0_rules_only_on_the_exercise_inputs_makes_no_call(
    source: Path, library_id: str
) -> None:
    boq = read_boq(source)
    fake = silent_fake()
    result = await run(boq, library_id, make_wrapper(fake), RunProfile.B0)

    assert fake.calls == []
    assert result.calls == ()
    assert len(result.lines) == EXERCISE_LINES
    outcomes = Counter((item.decision.decision, item.decision.reason) for item in result.lines)
    assert outcomes == {
        (Decision.NOT_A_MATERIAL, ReasonCode.HEADER.value): EXERCISE_HEADERS,
        (Decision.NEEDS_REVIEW, B0_REVIEW_REASON): EXERCISE_LINES - EXERCISE_HEADERS,
    }
    reference = labelled_item_numbers()
    assert [line.item_no for line in boq.lines] == [item_no for item_no, _ in reference]
    for item, (_, labelled) in zip(result.lines, reference, strict=True):
        if labelled:
            assert item.decision.decision != Decision.NOT_A_MATERIAL
        if item.decision.decision == Decision.NOT_A_MATERIAL:
            assert not item.line.unit.strip()
            assert not item.line.qty.strip()
    assert result.exit_code == EXIT_OK


async def test_b0_rows_are_rule_rows_with_no_cost_and_no_prompt() -> None:
    boq = read_boq(EN_INPUT)
    result = await run(boq, "global", make_wrapper(silent_fake()), RunProfile.B0)

    for item in result.lines:
        assert item.model == RULES_MODEL
        assert item.cost_usd == 0.0
        assert item.latency_ms == 0
        assert item.call_ids == ()
        assert item.prompt_version == ""
        assert item.suggested is None
        assert item.suggested2 is None
        assert item.raw_line_responses == ()
        assert item.context == "none"
        assert item.flags == ()
    rendered = render_csv(result).decode("utf-8").splitlines()
    header = rendered[0].split(",")
    rows = list(csv.reader(rendered[1:]))
    cost, model = header.index("cost_usd"), header.index("model")
    assert {row[cost] for row in rows} == {"0.000000"}
    assert {row[model] for row in rows} == {RULES_MODEL}
    assert result.prompt_versions == ()
    assert result.system_prompts == {}


async def test_b0_manifest_records_a_rules_only_run() -> None:
    boq = read_boq(FR_INPUT)
    result = await run(boq, "fr", make_wrapper(silent_fake()), RunProfile.B0)
    manifest = result.manifest

    assert manifest["profile"] == "b0"
    assert manifest["mode"] == "rules"
    assert manifest["policy_resolution"] == "b0_rules_only"
    assert manifest["policy_id"] == "B0"
    assert manifest["passes_k"] == 0
    assert manifest["prompt_version"] == []
    assert manifest["call_count"] == 0
    assert manifest["n_routed"] == 0
    assert manifest["spend_usd"] == 0
    assert manifest["attributed_cost_usd"] == 0
    assert manifest["served_models"] == []
    assert manifest["fallback_engaged"] is False
    assert manifest["exit_code"] == EXIT_OK
    assert result.policy.resolution == PolicyResolution.B0_RULES_ONLY
    assert run_mode(make_wrapper(silent_fake()).adapter, ()) == RunMode.FAKE


async def test_b0_keeps_the_structural_gates_and_never_skips_on_the_unit() -> None:
    extra = b"Note,Voir plans,,,\r\n"
    boq = read_boq(SMALL_BOQ.read_bytes() + extra)
    full = await run(boq, "fr", make_wrapper(fake_for(boq, "fr")))
    rules = await run(boq, "fr", make_wrapper(silent_fake()), RunProfile.B0)

    reasons = {item.decision.reason for item in rules.lines}
    assert {"HEADER", "EMPTY_ROW", "HEADER_UNCONFIRMED", B0_REVIEW_REASON} <= reasons
    for b3, b0 in zip(full.lines, rules.lines, strict=True):
        if b3.decision.rule in STRUCTURAL_RULES:
            assert b0.decision == b3.decision
        else:
            assert b0.decision.rule == Rule.D10
            assert b0.decision.decision == Decision.NEEDS_REVIEW
            assert b0.decision.reason == B0_REVIEW_REASON
            assert b0.decision.signals is None
    service_line = next(item for item in rules.lines if item.line.unit == "mois")
    assert service_line.decision.decision == Decision.NEEDS_REVIEW
