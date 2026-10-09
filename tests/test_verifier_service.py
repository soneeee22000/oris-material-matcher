"""E-08 in MatchService: flagged lines, verifier calls, decisions, audit, budget (A65.2, A61)."""

import asyncio
import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.boq import LineKind
from oris_matcher.domain.decision import (
    VERIFIER_NONE,
    DecisionProfile,
    ReasonCode,
    Rule,
)
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.io.writer import render_csv
from oris_matcher.llm.base import LLMRequest, Usage
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, Fault, FaultKind
from oris_matcher.llm.recording import MemoryCallSink
from oris_matcher.llm.replay_llm import RecordedRun, ReplayLLM
from oris_matcher.llm.routing import RoutingLLM
from oris_matcher.llm.wrapper import (
    BudgetLedger,
    LineOutcome,
    LLMWrapper,
    WrapperDeps,
    estimate_tokens,
    reservation_usd,
)
from oris_matcher.prompts.v1.verifier import (
    VerifierAnswer,
    build_verifier_request,
    is_verifier_request,
    payload_codes,
    sibling_rows,
)
from oris_matcher.prompts.v1.version import verifier_prompt_version
from oris_matcher.service import (
    DECISION_PROFILE_KEY,
    EXIT_LLM_UNAVAILABLE,
    EXIT_OK,
    MatchService,
    RunMode,
    RunOptions,
    RunProfile,
    RunResult,
    decision_profile_fields,
    recorded_decision_profile,
    run_mode,
)
from oris_matcher.settings import Settings
from oris_matcher.verification import verifier_groups
from test_service import (
    ALLOWLIST,
    CONFIG,
    EN_INPUT,
    FIXED_NOW,
    HAIKU,
    LIBRARIES,
    PRICING,
    answers_for,
)

ITEMS = 40
PARTIAL = "LLM_FAILURE:partial_signal"
VERIFIER_KEYS = {
    "verifier_flagged",
    "verifier_top1",
    "verifier_raw",
    "verifier_call_ids",
    "verifier_failure",
}


def make_settings(**overrides: Any) -> Settings:
    """Shipped config, verifier (A67) and enrichment (A68) off unless a test forces them."""
    values: dict[str, Any] = {
        "config_dir": CONFIG,
        "libraries": LIBRARIES,
        "verifier_adopted": False,
        "enrichment": Path("none"),
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


ON = MatchService.from_settings(make_settings(verifier_adopted=True))
OFF = MatchService.from_settings(make_settings())
GLOBAL = OFF.library("global")
BOQ = read_boq(EN_INPUT)
SELECT = frozenset(
    line.line_id for line in [line for line in BOQ.lines if line.kind == LineKind.ITEM][:ITEMS]
)
MAIN_ANSWERS = answers_for(BOQ, GLOBAL)


async def _no_sleep(seconds: float) -> None:
    del seconds


def _wrapper(
    adapter: Any, cap_usd: float = 100.0, sink: MemoryCallSink | None = None
) -> LLMWrapper:
    deps = WrapperDeps(sink=sink or MemoryCallSink(), sleep=_no_sleep, now=lambda: FIXED_NOW)
    return LLMWrapper(adapter, PRICING, BudgetLedger(cap_usd), None, deps)


def _fake(verifier_answers: Mapping[str, Mapping[str, Any]] | None = None, **extra: Any) -> FakeLLM:
    behaviour = FakeBehaviour(
        answers=MAIN_ANSWERS, verifier_answers=dict(verifier_answers or {}), **extra
    )
    return FakeLLM(HAIKU, ALLOWLIST, behaviour)


def _run(
    service: MatchService,
    adapter: Any,
    *,
    select: frozenset[str] = SELECT,
    profile: RunProfile = RunProfile.B3,
    cap_usd: float = 100.0,
) -> RunResult:
    options = RunOptions(select=select, run_id="e08")
    wrapper = _wrapper(adapter, cap_usd)
    return asyncio.run(service.match(BOQ, "global", profile=profile, llm=wrapper, options=options))


def _verifier_requests(fake: FakeLLM) -> list[LLMRequest]:
    return [request for request in fake.calls if is_verifier_request(request)]


def _main_requests(fake: FakeLLM) -> list[LLMRequest]:
    return [request for request in fake.calls if not is_verifier_request(request)]


def _would_match(result: RunResult) -> dict[str, str]:
    """Transport id -> top1 of every line the verifier-off run matched."""
    return {
        transport_id(item.line): item.decision.top1
        for item in result.lines
        if item.decision.rule == Rule.D9
    }


OFF_RESULT = _run(OFF, _fake())
FLAGGED = _would_match(OFF_RESULT)


def test_the_fixture_has_flagged_and_unflagged_routed_lines() -> None:
    routed = [item for item in OFF_RESULT.lines if item.line.kind == LineKind.ITEM]
    assert 5 <= len(FLAGGED) < len(routed)
    types = {GLOBAL.by_code[code].material_type for code in FLAGGED.values()}
    assert len(types) >= 2


# The setting, the profile and the manifest fields


def test_the_service_builds_its_profile_from_a_forcing_setting() -> None:
    for adopting in (False, True):
        policy = dataclasses.replace(OFF_RESULT.policy, verifier_adopted=adopting)
        assert ON.resources.decision_profile(policy) == DecisionProfile(verifier_adopted=True)
        assert OFF.resources.decision_profile(policy) == DecisionProfile()


def test_the_manifest_field_names_the_verifier_only_when_adopted() -> None:
    on = decision_profile_fields(DecisionProfile(verifier_adopted=True))
    off = decision_profile_fields(DecisionProfile())
    assert on == {DECISION_PROFILE_KEY: {"drop_conflicting_votes": False, "verifier_adopted": True}}
    assert off == {DECISION_PROFILE_KEY: {"drop_conflicting_votes": False}}
    assert recorded_decision_profile(json.loads(json.dumps(on))) == DecisionProfile(
        verifier_adopted=True
    )
    assert recorded_decision_profile(off) == DecisionProfile()
    assert recorded_decision_profile({}) == DecisionProfile()


# Flagged lines: exactly the would-be D9 lines


def test_the_verifier_is_asked_about_exactly_the_would_be_matches() -> None:
    fake = _fake()
    result = _run(ON, fake)
    asked = {line_id for request in _verifier_requests(fake) for line_id in request.line_ids}
    assert asked == set(FLAGGED)
    flagged = {
        transport_id(item.line)
        for item in result.lines
        if item.verifier is not None and item.verifier.flagged
    }
    assert flagged == set(FLAGGED)
    assert result.manifest["verifier"]["flagged_count"] == len(FLAGGED)


def test_each_request_shows_one_type_and_only_its_sibling_rows() -> None:
    fake = _fake()
    _run(ON, fake)
    requests = _verifier_requests(fake)
    assert len(requests) >= 2
    for request in requests:
        types = {GLOBAL.by_code[FLAGGED[line_id]].material_type for line_id in request.line_ids}
        assert len(types) == 1
        siblings = sibling_rows(GLOBAL, FLAGGED[request.line_ids[0]])
        assert payload_codes(request.user_payload) == tuple(row.code for row in siblings)
        assert len(request.line_ids) <= ON.resources.settings.batch_size
        assert request.model == HAIKU
        assert request.temperature == 0.0


def test_the_verifier_request_is_the_prompt_variant_rendered_for_those_lines() -> None:
    fake = _fake()
    _run(ON, fake)
    by_transport = {transport_id(line): line for line in BOQ.lines}
    requests = _verifier_requests(fake)
    assert requests
    for request in requests:
        lines = [by_transport[line_id] for line_id in request.line_ids]
        rows = sibling_rows(GLOBAL, FLAGGED[request.line_ids[0]])
        expected = build_verifier_request(lines, rows, model=HAIKU, max_tokens=4096)
        assert request.sha256() == expected.sha256()


# What the verifier's answer decides


def _verdicts(code_for: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    return {
        line_id: {"id": line_id, "evidence": "x", "code": code}
        for line_id, code in code_for.items()
    }


def _other_sibling(code: str) -> str:
    return next(row.code for row in sibling_rows(GLOBAL, code) if row.code != code)


def _foreign(code: str) -> str:
    material_type = GLOBAL.by_code[code].material_type
    return next(row.code for row in GLOBAL.rows if row.material_type != material_type)


ANSWER_KINDS = ("agree", "other", "none", "foreign", "lower", "malformed")


def _scenario() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Give each flagged line one kind of verifier answer, cycling through the kinds."""
    answers: dict[str, dict[str, Any]] = {}
    kinds: dict[str, str] = {}
    for index, (line_id, top1) in enumerate(sorted(FLAGGED.items())):
        kind = ANSWER_KINDS[index % len(ANSWER_KINDS)]
        codes = {
            "agree": top1,
            "other": _other_sibling(top1),
            "none": VERIFIER_NONE,
            "foreign": _foreign(top1),
            "lower": top1.lower(),
        }
        if kind == "malformed":
            answers[line_id] = {"id": line_id, "evidence": "x"}
        else:
            answers[line_id] = {"id": line_id, "evidence": "x", "code": codes[kind]}
        kinds[line_id] = kind
    return answers, kinds


EXPECTED = {
    "agree": (Rule.D9, "SIGNAL:T8"),
    "other": (Rule.D8A, ReasonCode.VERIFIER_DISAGREES),
    "none": (Rule.D8A, ReasonCode.VERIFIER_DISAGREES),
    "foreign": (Rule.D1B, PARTIAL),
    "lower": (Rule.D1B, PARTIAL),
    "malformed": (Rule.D1B, PARTIAL),
}


def test_the_answer_decides_each_flagged_line() -> None:
    answers, kinds = _scenario()
    assert set(kinds.values()) == set(ANSWER_KINDS)
    result = _run(ON, _fake(answers))
    for item in result.lines:
        line_id = transport_id(item.line)
        if line_id not in kinds:
            continue
        assert (item.decision.rule, item.decision.reason) == EXPECTED[kinds[line_id]]
        assert item.decision.top1 == FLAGGED[line_id]


def test_lines_that_are_not_flagged_decide_as_with_the_verifier_off() -> None:
    answers, _ = _scenario()
    result = _run(ON, _fake(answers))
    for on, off in zip(result.lines, OFF_RESULT.lines, strict=True):
        if transport_id(on.line) in FLAGGED:
            continue
        assert on.decision == off.decision
        assert on.call_ids == off.call_ids


def test_the_audit_keeps_the_verifier_raw_answer_its_check_and_its_calls() -> None:
    answers, kinds = _scenario()
    result = _run(ON, _fake(answers))
    verifier_calls = {record.call_id for record in result.calls if is_verifier_record(record)}
    for record, item in zip(result.audit, result.lines, strict=True):
        assert set(record) >= VERIFIER_KEYS
        line_id = transport_id(item.line)
        if line_id not in kinds:
            assert record["verifier_flagged"] is False
            assert record["verifier_call_ids"] == []
            continue
        assert record["verifier_flagged"] is True
        assert json.loads(record["verifier_raw"]) == answers[line_id]
        assert set(record["verifier_call_ids"]) <= verifier_calls
        assert record["verifier_call_ids"]
        assert set(record["verifier_call_ids"]) <= set(record["call_ids"])
        kind = kinds[line_id]
        expected_top1 = answers[line_id].get("code") if kind in {"agree", "other", "none"} else None
        assert record["verifier_top1"] == expected_top1
        failure = {"foreign": "invalid_code", "lower": "invalid_code", "malformed": "malformed"}
        assert record["verifier_failure"] == failure.get(kind)


def is_verifier_record(record: Any) -> bool:
    return '"candidates"' in record.user_message


def test_verifier_calls_are_recorded_attributed_and_counted() -> None:
    result = _run(ON, _fake())
    verifier = [record for record in result.calls if is_verifier_record(record)]
    assert verifier
    total = sum(record.cost_usd for record in result.calls)
    assert result.manifest["call_count"] == len(result.calls)
    assert result.manifest["attributed_cost_usd"] == pytest.approx(total)
    assert sum(item.cost_usd for item in result.lines) == pytest.approx(total)
    assert result.manifest["verifier"]["call_count"] == len(verifier)
    assert result.manifest["verifier"]["prompt_version"] == verifier_prompt_version()
    stored = set(result.system_prompts)
    assert {record.system_blocks_sha256 for record in result.calls} <= stored


# Flag off: nothing changes


def test_flag_off_main_requests_are_byte_identical_with_the_flag_on() -> None:
    on_fake, off_fake = _fake(), _fake()
    _run(ON, on_fake)
    _run(OFF, off_fake)
    on_main = sorted(request.sha256() for request in _main_requests(on_fake))
    off_main = sorted(request.sha256() for request in off_fake.calls)
    assert on_main == off_main
    assert not _verifier_requests(off_fake)


def test_flag_off_records_nothing_of_the_verifier() -> None:
    assert OFF_RESULT.manifest[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": False}
    assert "verifier" not in OFF_RESULT.manifest
    assert all(not VERIFIER_KEYS & set(record) for record in OFF_RESULT.audit)
    again = _run(OFF, _fake())
    assert render_csv(again) == render_csv(OFF_RESULT)
    assert again.audit == OFF_RESULT.audit


@pytest.mark.parametrize("profile", [RunProfile.B0, RunProfile.B2])
def test_b0_and_b2_never_run_the_verifier(profile: RunProfile) -> None:
    fake = _fake()
    result = _run(ON, fake, profile=profile)
    assert not _verifier_requests(fake)
    assert result.manifest[DECISION_PROFILE_KEY] == {"drop_conflicting_votes": False}
    assert "verifier" not in result.manifest


# Replay


def test_a_verifier_run_replays_byte_identically_at_zero() -> None:
    answers, _ = _scenario()
    recorded = _run(ON, _fake(answers))
    assert any(is_verifier_record(record) for record in recorded.calls)
    replay = ReplayLLM(RecordedRun.from_records(recorded.calls), HAIKU, ALLOWLIST, strict=True)
    replayed = _run(ON, replay)
    assert render_csv(replayed) == render_csv(recorded)
    assert replayed.audit == recorded.audit
    assert replayed.manifest["mode"] == RunMode.REPLAY
    assert replayed.manifest["spend_usd"] == 0


def test_a_replay_missing_the_verifier_records_fails_closed() -> None:
    recorded = _run(ON, _fake())
    main_only = [record for record in recorded.calls if not is_verifier_record(record)]
    replay = ReplayLLM(RecordedRun.from_records(main_only), HAIKU, ALLOWLIST, strict=False)
    replayed = _run(ON, replay)
    for item in replayed.lines:
        if transport_id(item.line) in FLAGGED:
            assert item.decision.reason == PARTIAL
            assert item.verifier is not None
            assert item.verifier.failure == "replay_miss"
    assert replayed.exit_code == EXIT_LLM_UNAVAILABLE
    assert recorded.exit_code == EXIT_OK


def test_a_routed_run_with_a_fake_verifier_is_a_fake_run() -> None:
    main = ReplayLLM(RecordedRun.from_records(()), HAIKU, ALLOWLIST)
    router = RoutingLLM(
        main=main, verifier=FakeLLM(HAIKU, ALLOWLIST), is_verifier=is_verifier_request
    )
    assert run_mode(router, ()) == RunMode.FAKE


# Budget: a verifier dispatch waits for reservations in flight, and is refused only when
# spend alone leaves no room (A61)


MAIN_USAGE = Usage(input_tokens=100, output_tokens=3000)
TINY_USAGE = Usage(input_tokens=1, output_tokens=1)


def reservation_for(request: LLMRequest) -> float:
    """Return the wrapper's reservation for a request whose prefix was never read (A61, A63)."""
    price = PRICING.lookup("anthropic", HAIKU)
    return reservation_usd(
        request, price, PRICING.per_tokens, prefix_read=False, count_tokens=estimate_tokens
    )


def _two_types() -> frozenset[str]:
    """Select one flagged line of each of two material types."""
    chosen: dict[str, str] = {}
    for line_id, top1 in sorted(FLAGGED.items()):
        material_type = GLOBAL.by_code[top1].material_type
        if material_type not in chosen.values():
            chosen[line_id] = material_type
    picked = list(chosen)[:2]
    return frozenset(line.line_id for line in BOQ.lines if transport_id(line) in picked)


def _budget_router() -> RoutingLLM:
    main = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=MAIN_ANSWERS, usage=MAIN_USAGE))
    verifier = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(usage=TINY_USAGE))
    return RoutingLLM(main=main, verifier=verifier, is_verifier=is_verifier_request)


def _sizes(select: frozenset[str]) -> tuple[list[float], list[float], float]:
    """Return the main and verifier reservations, and one main call's cost, from a free run."""
    router = _budget_router()
    _run(ON, router, select=select)
    main = [reservation_for(request) for request in router.main.calls]  # type: ignore[attr-defined]
    verifier = [reservation_for(request) for request in router.verifier.calls]  # type: ignore[attr-defined]
    price = PRICING.lookup("anthropic", HAIKU)
    usage = MAIN_USAGE.input_tokens * price.input + MAIN_USAGE.output_tokens * price.output
    return main, verifier, usage / PRICING.per_tokens


def test_a_verifier_call_spend_cannot_cover_is_refused_and_the_line_is_partial() -> None:
    select = _two_types()
    main, verifier, cost = _sizes(select)
    assert len(verifier) == 2
    cap = (len(main) - 1) * cost + max(main)
    assert len(main) * cost + min(verifier) > cap
    result = _run(ON, _budget_router(), select=select, cap_usd=cap)
    for item in result.lines:
        assert item.decision.reason == PARTIAL
        assert item.verifier is not None
        assert item.verifier.failure == ReasonCode.BUDGET_CAP.value
    declined = result.manifest["declined_attempts"]
    assert len(declined) == len(verifier)
    assert {attempt["reason"] for attempt in declined} == {ReasonCode.BUDGET_CAP.value}
    assert result.manifest["spend_usd"] == pytest.approx(len(main) * cost)


def test_verifier_calls_that_fit_spend_wait_for_each_other_and_are_all_made() -> None:
    select = _two_types()
    main, verifier, cost = _sizes(select)
    spent = len(main) * cost
    cap = max((len(main) - 1) * cost + max(main), spent + max(verifier) + min(verifier) / 2)
    assert spent + sum(verifier) > cap
    router = _budget_router()
    result = _run(ON, router, select=select, cap_usd=cap)
    assert result.manifest["declined_attempts"] == []
    assert len(router.verifier.calls) == 2  # type: ignore[attr-defined]
    for item in result.lines:
        assert item.verifier is not None
        assert item.verifier.failure is None
        assert item.verifier.top1 is not None
        assert item.decision.rule in {Rule.D9, Rule.D8A}


def test_a_verifier_reservation_has_no_cached_prefix() -> None:
    line = next(line for line in BOQ.lines if transport_id(line) in FLAGGED)
    rows = sibling_rows(GLOBAL, FLAGGED[transport_id(line)])
    request = build_verifier_request([line], rows, model=HAIKU, max_tokens=4096)
    price = PRICING.lookup("anthropic", HAIKU)
    uncached = estimate_tokens("".join(block.text for block in request.system_blocks))
    uncached += estimate_tokens(request.user_payload)
    expected = (uncached * price.input + 4096 * price.output) / PRICING.per_tokens
    assert reservation_for(request) == pytest.approx(expected)


# The verifier stage under a tripped breaker


def _timing_out(call_no: int, request: LLMRequest) -> Fault:
    del call_no, request
    return Fault(FaultKind.TIMEOUT)


def test_a_breaker_tripped_by_the_verifier_fails_the_flagged_lines_closed() -> None:
    main = _fake()
    verifier = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(rule=_timing_out))
    router = RoutingLLM(main=main, verifier=verifier, is_verifier=is_verifier_request)
    result = _run(ON, router)
    assert verifier.calls
    failures: set[str | None] = set()
    for on, off in zip(result.lines, OFF_RESULT.lines, strict=True):
        if transport_id(on.line) not in FLAGGED:
            assert on.decision == off.decision
            continue
        assert (on.decision.rule, on.decision.reason) == (Rule.D1B, PARTIAL)
        assert on.verifier is not None
        assert on.verifier.top1 is None
        failures.add(on.verifier.failure)
    unavailable = ReasonCode.LLM_UNAVAILABLE.value
    assert unavailable in failures
    assert failures <= {"timeout", unavailable}
    assert result.exit_code == EXIT_LLM_UNAVAILABLE


# Smaller guards of the stage


def test_the_manifest_counts_only_validated_verifier_answers() -> None:
    answers, kinds = _scenario()
    result = _run(ON, _fake(answers))
    validated = sum(kind in {"agree", "other", "none"} for kind in kinds.values())
    assert 0 < validated < len(kinds)
    assert result.manifest["verifier"]["answered_count"] == validated
    assert result.manifest["verifier"]["flagged_count"] == len(kinds)


def test_verifier_batches_need_a_positive_size() -> None:
    by_transport = {transport_id(line): line for line in BOQ.lines}
    flagged = [(by_transport[line_id], top1) for line_id, top1 in sorted(FLAGGED.items())]
    for size in (0, -1):
        with pytest.raises(ValueError, match="batch size must be >= 1"):
            verifier_groups(flagged, GLOBAL, size)
    assert verifier_groups(flagged, GLOBAL, 1)


def test_a_verifier_verdict_is_not_a_pass_outcome() -> None:
    verdict = VerifierAnswer(id="L3", evidence="x", code=VERIFIER_NONE)
    with pytest.raises(ValueError, match="verdict is not a pass outcome"):
        LineOutcome(line_id="L3", call_ids=("c1",), verdict=verdict).pass_outcome()


def test_a_run_with_nothing_flagged_makes_no_verifier_call() -> None:
    nothing = {
        line_id: {**answer, "kind": "no_equivalent", "top1": "", "top2": ""}
        for line_id, answer in MAIN_ANSWERS.items()
    }
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=nothing))
    result = _run(ON, fake)
    assert fake.calls
    assert not _verifier_requests(fake)
    assert not any(item.decision.rule == Rule.D9 for item in result.lines)
    counts = result.manifest["verifier"]
    assert (counts["flagged_count"], counts["answered_count"], counts["call_count"]) == (0, 0, 0)
    assert all(record["verifier_flagged"] is False for record in result.audit)
    assert result.exit_code == EXIT_OK
