"""Tests for the LLM wrapper: retries, budgets, breaker, ids, cache, replay (§11.3)."""

import json
import logging
import random
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from oris_matcher.domain.decision import LLMFailureKind, PassOutcome, ReasonCode
from oris_matcher.llm.base import LLMPort, LLMRequest, LLMResult, LLMStatus, SystemBlock, Usage
from oris_matcher.llm.fake_llm import (
    FakeBehaviour,
    FakeLLM,
    Fault,
    FaultKind,
    FaultRule,
    default_answer,
)
from oris_matcher.llm.recording import CallRecord, MemoryCallSink, ReasonForCall, attribute_costs
from oris_matcher.llm.replay_llm import RecordedRun, ReplayLLM, ReplayMissError, ResponseCache
from oris_matcher.llm.wrapper import (
    BatchOutcome,
    BudgetLedger,
    CircuitBreaker,
    LLMWrapper,
    WrapperDeps,
    WrapperPolicy,
    cost_of,
    reservation_usd,
)
from oris_matcher.prompts.v1.schema import LineAnswer, output_json_schema
from oris_matcher.settings import PricingTable, load_models_config, load_pricing

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = load_models_config(ROOT / "config" / "models.toml").allowlist.patterns
PRICING = load_pricing(ROOT / "config" / "pricing.toml")
HAIKU = "claude-haiku-4-5-20251001"
IDS = ("L1", "L2", "L3", "L4")
TEXTS = {
    "L1": "Béton C30/37 pour voiles",
    "L2": "Acier HA B500B",
    "L3": "Coffrage bois",
    "L4": "Mortier de ciment",
}
FIXED_NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
BIG_CAP = 100.0


def make_request(line_ids: tuple[str, ...]) -> LLMRequest:
    """Build a request for the given line ids."""
    lines = [{"id": i, "short": TEXTS.get(i, i)} for i in line_ids]
    payload = json.dumps({"lines": lines}, ensure_ascii=False, sort_keys=True)
    return LLMRequest(
        provider="anthropic",
        model=HAIKU,
        system_blocks=(SystemBlock("vocabulary", cache=False), SystemBlock("library", cache=True)),
        schema_json=json.dumps(output_json_schema()),
        user_payload=f"<boq_lines>{payload}</boq_lines>",
        max_tokens=1000,
        temperature=0.0,
        line_ids=line_ids,
    )


def answers() -> dict[str, dict[str, Any]]:
    """Return a valid canned answer per line, its evidence taken from the line."""
    return {i: {**default_answer(i), "evidence": TEXTS[i]} for i in IDS}


class Harness:
    """A wrapper over FakeLLM, or another adapter, with recorded waits and records."""

    def __init__(
        self,
        behaviour: FakeBehaviour | None = None,
        *,
        cap_usd: float = BIG_CAP,
        policy: WrapperPolicy | None = None,
        cache: ResponseCache | None = None,
        adapter: LLMPort | None = None,
    ) -> None:
        """Build the wrapper; ``adapter`` replaces the fake when given."""
        self.fake = FakeLLM(HAIKU, ALLOWLIST, behaviour or FakeBehaviour(answers=answers()))
        self.sink = MemoryCallSink()
        self.waits: list[float] = []
        self.ledger = BudgetLedger(cap_usd)
        deps = WrapperDeps(
            sink=self.sink,
            cache=cache,
            sleep=self.sleep,
            rng=random.Random(7),
            now=lambda: FIXED_NOW,
            count_tokens=len,
        )
        port = adapter or self.fake
        self.wrapper = LLMWrapper(port, PRICING, self.ledger, policy, deps)

    async def sleep(self, seconds: float) -> None:
        """Record a retry wait instead of sleeping."""
        self.waits.append(seconds)

    async def run(self, line_ids: tuple[str, ...] = IDS) -> BatchOutcome:
        """Run one batch through the wrapper."""
        return await self.wrapper.run_batch(line_ids, make_request)


def replayer(
    records: list[CallRecord],
    *,
    strict: bool = True,
    cap_usd: float = BIG_CAP,
    pricing: PricingTable = PRICING,
) -> Harness:
    """Build a harness whose adapter replays the given records."""
    replay = ReplayLLM(RecordedRun.from_records(records), HAIKU, ALLOWLIST, strict=strict)
    harness = Harness(adapter=replay, cap_usd=cap_usd)
    harness.wrapper.pricing = pricing
    return harness


def scripted(*faults: Fault | None, rule: FaultRule | None = None) -> FakeBehaviour:
    """Build fake behaviour from a fault script and an optional rule."""
    return FakeBehaviour(answers=answers(), script=faults, rule=rule)


def failures(outcome: BatchOutcome) -> dict[str, LLMFailureKind | ReasonCode | None]:
    """Map each line to its failure kind or line failure, None when answered."""
    return {line_id: line.failure or line.line_failure for line_id, line in outcome.lines.items()}


def answered(outcome: BatchOutcome) -> set[str]:
    """Return the ids of the lines that got an answer."""
    return {line_id for line_id, line in outcome.lines.items() if line.answer is not None}


# ---------------------------------------------------------------- happy path and recording


async def test_happy_path_answers_every_line_with_one_recorded_call() -> None:
    """Happy path answers every line with one recorded call."""
    harness = Harness()
    outcome = await harness.run()
    assert list(outcome.lines) == list(IDS)
    assert answered(outcome) == set(IDS)
    assert len(harness.sink.records) == 1
    record = harness.sink.records[0]
    assert record.reason_for_call == ReasonForCall.FIRST
    assert record.attempt_no == 1
    assert record.parent_call_id is None
    assert record.line_ids == IDS
    assert record.request_sha256 == make_request(IDS).sha256()
    assert record.http_status == 200
    assert record.cache_hit is False
    assert record.started_at == "2026-10-05T10:00:00.000Z"
    assert record.cost_usd > 0
    assert outcome.records == tuple(harness.sink.records)
    line = outcome.lines["L1"]
    assert line.call_ids == (record.call_id,)
    assert isinstance(line.answer, LineAnswer)
    assert line.answer.evidence == TEXTS["L1"]
    assert json.loads(line.raw_line_response)["id"] == "L1"
    pass_outcome = line.pass_outcome()
    assert isinstance(pass_outcome, PassOutcome)
    assert pass_outcome.answer == line.answer


async def test_cost_is_computed_from_usage_and_the_price_table() -> None:
    """Cost is computed from usage and the price table."""
    usage = Usage(input_tokens=1000, output_tokens=200, cache_read=3000, cache_write=500)
    behaviour = FakeBehaviour(answers=answers(), usage=usage)
    harness = Harness(behaviour)
    await harness.run()
    expected = (1000 * 1.00 + 200 * 5.00 + 3000 * 0.10 + 500 * 1.25) / 1_000_000
    assert cost_of(usage, PRICING.lookup("anthropic", HAIKU), PRICING.per_tokens) == pytest.approx(
        expected
    )
    assert harness.sink.records[0].cost_usd == pytest.approx(expected)
    assert harness.ledger.spent_usd == pytest.approx(expected)
    assert harness.ledger.reserved_usd == 0


async def test_runs_are_deterministic() -> None:
    """Runs are deterministic."""
    script = (Fault(FaultKind.TIMEOUT), Fault(FaultKind.TRUNCATED), None)
    first, second = Harness(scripted(*script)), Harness(scripted(*script))
    outcome_a, outcome_b = await first.run(), await second.run()
    assert outcome_a == outcome_b
    assert first.sink.records == second.sink.records
    assert first.waits == second.waits


# ---------------------------------------------------------------- retries


async def test_timeout_is_retried_after_backoff() -> None:
    """Timeout is retried after backoff."""
    harness = Harness(scripted(Fault(FaultKind.TIMEOUT)))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    first, retry = harness.sink.records
    assert retry.reason_for_call == ReasonForCall.RETRY
    assert retry.attempt_no == 2
    assert retry.parent_call_id == first.call_id
    assert first.error_class is not None
    assert len(harness.waits) == 1
    policy = WrapperPolicy()
    assert policy.backoff_base_s <= harness.waits[0] <= policy.backoff_base_s + policy.jitter_s
    assert outcome.lines["L1"].call_ids == (first.call_id, retry.call_id)


async def test_persistent_timeout_fails_after_one_call_and_two_retries() -> None:
    """Persistent timeout fails after one call and two retries."""
    harness = Harness(scripted(rule=lambda n, r: Fault(FaultKind.TIMEOUT)))
    outcome = await harness.run()
    assert len(harness.fake.calls) == 3
    assert set(failures(outcome).values()) == {LLMFailureKind.TIMEOUT}
    assert [r.attempt_no for r in harness.sink.records] == [1, 2, 3]


async def test_rate_limit_waits_at_least_retry_after() -> None:
    """Rate limit waits at least retry after."""
    harness = Harness(scripted(Fault(FaultKind.RATE_LIMITED, retry_after_s=20.0)))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    assert harness.waits[0] >= 20.0
    assert harness.wrapper.breaker.consecutive_failures == 0


async def test_backoff_grows_exponentially_when_retry_after_is_small() -> None:
    """Backoff grows exponentially when retry after is small."""
    fault = Fault(FaultKind.RATE_LIMITED, retry_after_s=0.0)
    harness = Harness(scripted(fault, fault, fault))
    outcome = await harness.run()
    assert set(failures(outcome).values()) == {LLMFailureKind.RATE_LIMITED}
    policy = WrapperPolicy()
    assert len(harness.waits) == 2
    assert policy.backoff_base_s <= harness.waits[0] <= policy.backoff_base_s + policy.jitter_s
    low = 2 * policy.backoff_base_s
    assert low <= harness.waits[1] <= low + policy.jitter_s


@pytest.mark.parametrize("http_status", [500, 502, 503, 408, 409])
async def test_retryable_server_errors_are_retried(http_status: int) -> None:
    """Retryable server errors are retried."""
    fault = Fault(FaultKind.SERVER_ERROR, http_status=http_status)
    harness = Harness(scripted(fault, fault))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    assert len(harness.fake.calls) == 3


async def test_persistent_5xx_fails_as_api_error() -> None:
    """Persistent 5xx fails as api error."""
    harness = Harness(scripted(rule=lambda n, r: Fault(FaultKind.SERVER_ERROR)))
    outcome = await harness.run()
    assert len(harness.fake.calls) == 3
    assert set(failures(outcome).values()) == {LLMFailureKind.API_ERROR}


@pytest.mark.parametrize("http_status", [400, 401, 403, 404, 413])
async def test_client_errors_are_not_retried(http_status: int) -> None:
    """Client errors are not retried."""
    harness = Harness(scripted(Fault(FaultKind.SERVER_ERROR, http_status=http_status)))
    outcome = await harness.run()
    assert len(harness.fake.calls) == 1
    assert set(failures(outcome).values()) == {LLMFailureKind.API_ERROR}
    assert harness.waits == []


async def test_should_retry_header_overrides_the_status_code() -> None:
    """Should retry header overrides the status code."""
    no = Fault(FaultKind.SERVER_ERROR, http_status=500, should_retry=False)
    harness = Harness(scripted(no))
    await harness.run()
    assert len(harness.fake.calls) == 1
    yes = Fault(FaultKind.SERVER_ERROR, http_status=400, should_retry=True)
    harness = Harness(scripted(yes))
    outcome = await harness.run()
    assert len(harness.fake.calls) == 2
    assert answered(outcome) == set(IDS)


async def test_529_is_retried_and_reported_as_overloaded() -> None:
    """529 is retried and reported as overloaded."""
    harness = Harness(scripted(rule=lambda n, r: Fault(FaultKind.OVERLOADED)))
    outcome = await harness.run()
    assert len(harness.fake.calls) == 3
    assert set(failures(outcome).values()) == {LLMFailureKind.OVERLOADED}
    assert harness.wrapper.breaker.consecutive_failures == 3


async def test_529_with_retry_after_is_throttling() -> None:
    """529 with retry after is throttling."""
    fault = Fault(FaultKind.OVERLOADED, retry_after_s=5.0)
    harness = Harness(scripted(rule=lambda n, r: fault))
    outcome = await harness.run()
    assert len(harness.fake.calls) == 3
    assert len(harness.waits) == 2
    assert all(wait >= 5.0 for wait in harness.waits)
    assert set(failures(outcome).values()) == {LLMFailureKind.OVERLOADED}
    assert harness.wrapper.breaker.consecutive_failures == 0


async def test_refusal_is_neither_retried_nor_bisected() -> None:
    """Refusal is neither retried nor bisected."""
    harness = Harness(scripted(Fault(FaultKind.REFUSAL)))
    outcome = await harness.run()
    assert len(harness.fake.calls) == 1
    assert set(failures(outcome).values()) == {LLMFailureKind.REFUSAL}


# ---------------------------------------------------------------- bisection


async def test_truncation_bisects_down_to_answers() -> None:
    """Truncation bisects down to answers."""

    def rule(call_no: int, req: LLMRequest) -> Fault | None:
        """Choose the fault for one call."""
        return Fault(FaultKind.TRUNCATED) if len(req.line_ids) > 1 else None

    harness = Harness(scripted(rule=rule))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    sizes = [len(r.line_ids) for r in harness.sink.records]
    assert sizes == [4, 2, 2, 1, 1, 1, 1]
    root = harness.sink.records[0]
    halves = harness.sink.records[1:3]
    assert all(r.reason_for_call == ReasonForCall.SPLIT for r in harness.sink.records[1:])
    assert all(r.parent_call_id == root.call_id for r in halves)
    assert all(r.attempt_no == 1 for r in harness.sink.records)
    assert len(outcome.lines["L1"].call_ids) == 3


async def test_truncation_of_a_single_line_fails_as_truncated() -> None:
    """Truncation of a single line fails as truncated."""
    harness = Harness(scripted(rule=lambda n, r: Fault(FaultKind.TRUNCATED)))
    outcome = await harness.run(("L1", "L2"))
    assert set(failures(outcome).values()) == {LLMFailureKind.TRUNCATED}
    assert [len(r.line_ids) for r in harness.sink.records] == [2, 1, 1]


async def test_malformed_output_bisects_and_isolates_the_bad_line() -> None:
    """Malformed output bisects and isolates the bad line."""

    def rule(call_no: int, req: LLMRequest) -> Fault | None:
        """Choose the fault for one call."""
        return Fault(FaultKind.MALFORMED) if "L3" in req.line_ids else None

    harness = Harness(scripted(rule=rule))
    outcome = await harness.run()
    assert failures(outcome) == {"L1": None, "L2": None, "L3": LLMFailureKind.MALFORMED, "L4": None}


async def test_schema_invalid_output_counts_as_malformed() -> None:
    """Schema invalid line objects are malformed per line, kept verbatim, never bisected.

    The fake's INVALID_SCHEMA fault breaks every line object of the response, so since G1-T1a
    every line of that one call fails as malformed; none is re-asked.
    """
    harness = Harness(scripted(Fault(FaultKind.INVALID_SCHEMA)))
    outcome = await harness.run()
    assert set(failures(outcome).values()) == {LLMFailureKind.MALFORMED}
    assert len(harness.fake.calls) == 1
    for line_id, line in outcome.lines.items():
        assert json.loads(line.raw_line_response)["id"] == line_id


# ---------------------------------------------------------------- ids


async def test_missing_ids_are_re_asked_alone() -> None:
    """Missing ids are re asked alone."""
    harness = Harness(scripted(Fault(FaultKind.DROP_IDS, ids=("L2",))))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    first, reask = harness.sink.records
    assert reask.reason_for_call == ReasonForCall.REASK_MISSING
    assert reask.line_ids == ("L2",)
    assert reask.parent_call_id == first.call_id
    assert outcome.lines["L2"].call_ids == (first.call_id, reask.call_id)
    assert outcome.lines["L1"].call_ids == (first.call_id,)


async def test_always_missing_id_fails_within_the_six_call_budget() -> None:
    """Always missing id fails within the six call budget."""
    harness = Harness(scripted(rule=lambda n, r: Fault(FaultKind.DROP_IDS, ids=("L2",))))
    outcome = await harness.run()
    assert failures(outcome)["L2"] == LLMFailureKind.MISSING_ITEM
    assert answered(outcome) == {"L1", "L3", "L4"}
    assert len(outcome.lines["L2"].call_ids) == 6
    assert len(harness.fake.calls) == 6


async def test_byte_identical_duplicates_collapse() -> None:
    """Byte identical duplicates collapse."""
    harness = Harness(scripted(Fault(FaultKind.DUPLICATE_IDS, ids=("L1",))))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    assert len(harness.fake.calls) == 1


async def test_conflicting_duplicate_is_re_asked_once_then_accepted() -> None:
    """Conflicting duplicate is re asked once then accepted."""
    harness = Harness(scripted(Fault(FaultKind.CONFLICTING_DUPLICATES, ids=("L1",))))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    reask = harness.sink.records[1]
    assert reask.reason_for_call == ReasonForCall.REASK_CONFLICT
    assert reask.line_ids == ("L1",)


async def test_conflicting_duplicate_twice_fails_closed() -> None:
    """Conflicting duplicate twice fails closed."""
    fault = Fault(FaultKind.CONFLICTING_DUPLICATES, ids=("L1",))
    harness = Harness(scripted(rule=lambda n, r: fault))
    outcome = await harness.run()
    assert failures(outcome)["L1"] == LLMFailureKind.DUPLICATE_CONFLICT
    assert answered(outcome) == {"L2", "L3", "L4"}
    assert len(harness.fake.calls) == 2


async def test_unknown_ids_are_ignored_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Unknown ids are ignored and logged."""
    harness = Harness(scripted(Fault(FaultKind.UNKNOWN_IDS, ids=("L99",))))
    with caplog.at_level(logging.WARNING):
        outcome = await harness.run()
    assert list(outcome.lines) == list(IDS)
    assert answered(outcome) == set(IDS)
    assert "L99" in caplog.text
    assert len(harness.fake.calls) == 1


async def test_swapped_ids_are_attributed_by_id_so_evidence_exposes_them() -> None:
    """Swapped ids are attributed by id so evidence exposes them."""
    harness = Harness(scripted(Fault(FaultKind.SWAP_IDS)))
    outcome = await harness.run()
    for line_id, line in outcome.lines.items():
        assert line.answer is not None
        assert line.answer.id == line_id
        assert line.answer.evidence not in TEXTS[line_id]


# ---------------------------------------------------------------- budget per line


async def test_per_line_in_flight_budget_stops_further_calls() -> None:
    """Per line in flight budget stops further calls."""
    policy = WrapperPolicy(line_max_inflight_s=60.0)
    harness = Harness(scripted(rule=lambda n, r: Fault(FaultKind.TIMEOUT)), policy=policy)
    outcome = await harness.run()
    assert len(harness.fake.calls) == 2
    assert set(failures(outcome).values()) == {LLMFailureKind.TIMEOUT}


async def test_retry_waits_do_not_count_against_the_in_flight_budget() -> None:
    """Retry waits do not count against the in flight budget."""
    fault = Fault(FaultKind.RATE_LIMITED, retry_after_s=120.0)
    harness = Harness(scripted(fault, fault))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    assert sum(harness.waits) >= 240.0


# ---------------------------------------------------------------- circuit breaker


async def test_breaker_trips_after_ten_consecutive_non_throttling_failures() -> None:
    """Breaker trips after ten consecutive non throttling failures."""
    harness = Harness(scripted(rule=lambda n, r: Fault(FaultKind.SERVER_ERROR)))
    for line_id in IDS:
        outcome = await harness.wrapper.run_batch((line_id,), make_request)
    assert len(harness.fake.calls) == 10
    assert harness.wrapper.breaker.tripped
    assert failures(outcome)["L4"] == ReasonCode.LLM_UNAVAILABLE
    later = await harness.run(("L1", "L2"))
    assert set(failures(later).values()) == {ReasonCode.LLM_UNAVAILABLE}
    assert len(harness.fake.calls) == 10
    assert later.lines["L1"].pass_outcome() is None


async def test_throttling_never_trips_the_breaker() -> None:
    """Throttling never trips the breaker."""
    fault = Fault(FaultKind.RATE_LIMITED, retry_after_s=1.0)
    harness = Harness(scripted(rule=lambda n, r: fault))
    for _ in range(10):
        await harness.run(("L1",))
    assert len(harness.fake.calls) == 30
    assert not harness.wrapper.breaker.tripped


async def test_a_successful_call_resets_the_breaker() -> None:
    """A successful call resets the breaker."""
    breaker = CircuitBreaker(threshold=3)
    error = LLMResult(status=LLMStatus.API_ERROR, raw_text="", http_status=500)
    ok = LLMResult(status=LLMStatus.OK, raw_text="{}", http_status=200)
    for result in (error, error, ok, error, error):
        breaker.observe(result)
    assert not breaker.tripped
    breaker.observe(error)
    assert breaker.tripped


# ---------------------------------------------------------------- run budget


async def test_budget_cap_blocks_dispatch() -> None:
    """Budget cap blocks dispatch."""
    harness = Harness(cap_usd=0.0)
    outcome = await harness.run()
    assert set(failures(outcome).values()) == {ReasonCode.BUDGET_CAP}
    assert harness.fake.calls == []
    assert harness.sink.records == []
    assert outcome.lines["L1"].pass_outcome() is None


async def test_dispatch_is_allowed_when_spent_plus_reserved_equals_the_cap() -> None:
    """Dispatch is allowed when spent plus reserved equals the cap."""
    req = make_request(IDS)
    price = PRICING.lookup("anthropic", HAIKU)
    reserve = reservation_usd(req, price, PRICING.per_tokens, prefix_read=False, count_tokens=len)
    harness = Harness(cap_usd=reserve)
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)


def test_reservation_uses_the_cache_write_rate_until_a_read_is_seen() -> None:
    """Reservation uses the cache write rate until a read is seen."""
    req = make_request(("L1",))
    price = PRICING.lookup("anthropic", HAIKU)
    per = PRICING.per_tokens
    prefix = len("vocabulary") + len("library")
    user = len(req.user_payload)
    cold = reservation_usd(req, price, per, prefix_read=False, count_tokens=len)
    warm = reservation_usd(req, price, per, prefix_read=True, count_tokens=len)
    output = req.max_tokens * price.output
    assert cold == pytest.approx((prefix * price.cache_write + user * price.input + output) / per)
    assert warm == pytest.approx((prefix * price.cache_read + user * price.input + output) / per)


async def test_ledger_switches_to_the_read_rate_after_a_cache_read() -> None:
    """Ledger switches to the read rate after a cache read."""
    usage = Usage(input_tokens=10, output_tokens=10, cache_read=500, cache_write=0)
    harness = Harness(FakeBehaviour(answers=answers(), usage=usage))
    req = make_request(IDS)
    assert not harness.ledger.prefix_read(req)
    await harness.run()
    assert harness.ledger.prefix_read(req)


async def test_budget_cap_mid_batch_marks_only_undispatched_lines() -> None:
    """Budget cap mid batch marks only undispatched lines."""

    def rule(call_no: int, req: LLMRequest) -> Fault | None:
        """Choose the fault for one call."""
        return Fault(FaultKind.TRUNCATED) if len(req.line_ids) > 2 else None

    usage = Usage(input_tokens=100, output_tokens=1000)
    price = PRICING.lookup("anthropic", HAIKU)
    per_call = cost_of(usage, price, PRICING.per_tokens)
    half = make_request(("L1", "L2"))
    half_reserve = reservation_usd(
        half, price, PRICING.per_tokens, prefix_read=False, count_tokens=len
    )
    behaviour = FakeBehaviour(answers=answers(), rule=rule, usage=usage)
    harness = Harness(behaviour, cap_usd=per_call + half_reserve + 1e-9)
    outcome = await harness.run()
    assert len(harness.fake.calls) == 2
    assert failures(outcome) == {
        "L1": None,
        "L2": None,
        "L3": ReasonCode.BUDGET_CAP,
        "L4": ReasonCode.BUDGET_CAP,
    }
    assert harness.ledger.spent_usd <= harness.ledger.cap_usd


# ---------------------------------------------------------------- cache and replay


async def test_cache_hit_copies_source_cost_and_latency_and_skips_the_adapter() -> None:
    """A later run of the same batch is served from the cache at the source's cost."""
    cache = ResponseCache()
    first = Harness(cache=cache)
    await first.run()
    second = Harness(cache=cache)
    again = await second.run()
    assert answered(again) == set(IDS)
    assert second.fake.calls == []
    (source,) = first.sink.records
    (hit,) = second.sink.records
    assert hit.cache_hit is True
    assert hit.source_call_id == source.call_id
    assert hit.cost_usd == source.cost_usd
    assert hit.latency_ms == source.latency_ms
    assert hit.call_id != source.call_id
    assert second.ledger.spent_usd == 0.0


async def test_throttled_results_are_not_cached() -> None:
    """Only the delivered response of a retried call enters the cache."""
    cache = ResponseCache()
    harness = Harness(scripted(Fault(FaultKind.RATE_LIMITED, retry_after_s=1.0)), cache=cache)
    await harness.run()
    assert len(cache) == 1
    record = cache.get(make_request(IDS).sha256())
    assert record is not None
    assert record.http_status == 200


async def test_live_cache_never_answers_a_repeated_re_ask_with_its_own_miss() -> None:
    """A re-ask with the same hash as an earlier call in the run reaches the model."""
    drop = Fault(FaultKind.DROP_IDS, ids=("L1",))
    harness = Harness(scripted(drop, drop), cache=ResponseCache())
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    assert len(harness.fake.calls) == 3
    assert not any(record.cache_hit for record in harness.sink.records)


async def test_live_cache_re_asks_a_batch_whose_every_id_was_missing() -> None:
    """An empty answer is re-asked live although the re-ask hashes like the first call."""
    harness = Harness(scripted(Fault(FaultKind.DROP_IDS, ids=IDS)), cache=ResponseCache())
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    assert len(harness.fake.calls) == 2


async def test_cache_serves_each_occurrence_of_a_hash_in_recording_order() -> None:
    """A later run gets the k-th cached response for the k-th issue of a hash."""
    drop = Fault(FaultKind.DROP_IDS, ids=("L1",))
    cache = ResponseCache()
    live = await Harness(scripted(drop, drop), cache=cache).run()
    second = Harness(scripted(rule=lambda n, r: Fault(FaultKind.SERVER_ERROR)), cache=cache)
    cached = await second.run()
    assert second.fake.calls == []
    assert {k: v.answer for k, v in cached.lines.items()} == {
        k: v.answer for k, v in live.lines.items()
    }
    assert all(record.cache_hit for record in second.sink.records)


SCHEDULES = {
    "missing_reask_twice": (
        Fault(FaultKind.DROP_IDS, ids=("L1",)),
        Fault(FaultKind.DROP_IDS, ids=("L1",)),
    ),
    "everything_missing": (Fault(FaultKind.DROP_IDS, ids=IDS),),
    "500_then_429": (
        Fault(FaultKind.SERVER_ERROR),
        Fault(FaultKind.RATE_LIMITED, retry_after_s=3.0),
    ),
    "timeout_then_truncated": (Fault(FaultKind.TIMEOUT), Fault(FaultKind.TRUNCATED)),
    "conflict": (Fault(FaultKind.CONFLICTING_DUPLICATES, ids=("L2",)),),
    "should_not_retry": (Fault(FaultKind.SERVER_ERROR, should_retry=False),),
    "should_retry_a_400": (Fault(FaultKind.SERVER_ERROR, http_status=400, should_retry=True),),
}


@pytest.mark.parametrize("schedule", list(SCHEDULES))
async def test_replay_reproduces_the_live_outcome_attempt_by_attempt(schedule: str) -> None:
    """Replay walks every recorded attempt, failures included, and copies the source."""
    live = Harness(scripted(*SCHEDULES[schedule]))
    live_outcome = await live.run()
    replay = replayer(live.sink.records)
    replayed = await replay.run()
    assert replayed.lines == live_outcome.lines
    assert attribute_costs(replay.sink.records) == attribute_costs(live.sink.records)
    assert [r.call_id for r in replay.sink.records] == [r.call_id for r in live.sink.records]
    assert replay.ledger.spent_usd == live.ledger.spent_usd
    assert replay.waits == []
    assert all(r.cache_hit for r in replay.sink.records)
    assert [r.source_call_id for r in replay.sink.records] == [r.call_id for r in live.sink.records]


async def test_replay_reproduces_a_mid_batch_budget_cap() -> None:
    """Lines the live run never dispatched replay as BUDGET_CAP, not as replay misses."""

    def rule(call_no: int, req: LLMRequest) -> Fault | None:
        """Truncate batches of more than two lines."""
        return Fault(FaultKind.TRUNCATED) if len(req.line_ids) > 2 else None

    usage = Usage(input_tokens=100, output_tokens=1000)
    price = PRICING.lookup("anthropic", HAIKU)
    half = make_request(("L1", "L2"))
    reserve = reservation_usd(half, price, PRICING.per_tokens, prefix_read=False, count_tokens=len)
    cap = cost_of(usage, price, PRICING.per_tokens) + reserve + 1e-9
    live = Harness(FakeBehaviour(answers=answers(), rule=rule, usage=usage), cap_usd=cap)
    live_outcome = await live.run()
    replayed = await replayer(live.sink.records, cap_usd=cap).run()
    assert replayed.lines == live_outcome.lines
    assert failures(replayed)["L3"] == ReasonCode.BUDGET_CAP


async def test_replay_reproduces_a_breaker_trip() -> None:
    """Batches the live run skipped after the breaker tripped replay as LLM_UNAVAILABLE."""
    live = Harness(scripted(rule=lambda n, r: Fault(FaultKind.SERVER_ERROR)))
    live_outcomes = [await live.run((line_id,)) for line_id in IDS]
    replay = replayer(live.sink.records, strict=False)
    replay_outcomes = [await replay.run((line_id,)) for line_id in IDS]
    assert [o.lines for o in replay_outcomes] == [o.lines for o in live_outcomes]
    assert failures(replay_outcomes[-1])["L4"] == ReasonCode.LLM_UNAVAILABLE


async def test_replay_under_a_changed_price_table_copies_the_source_values() -> None:
    """A replay never re-measures cost, latency or timestamps."""
    live = Harness(scripted(Fault(FaultKind.SERVER_ERROR)))
    await live.run()
    price = PRICING.lookup("anthropic", HAIKU)
    dearer = price.model_copy(update={"output": price.output * 2, "input": price.input * 2})
    pricing = PRICING.model_copy(update={"providers": {"anthropic": {HAIKU: dearer}}})
    replay = replayer(live.sink.records, pricing=pricing)
    await replay.run()
    for source, copy in zip(live.sink.records, replay.sink.records, strict=True):
        assert (copy.cost_usd, copy.latency_ms) == (source.cost_usd, source.latency_ms)
        assert (copy.started_at, copy.ended_at) == (source.started_at, source.ended_at)
    assert replay.ledger.spent_usd == live.ledger.spent_usd


async def test_replay_miss_raises_in_strict_mode_through_the_wrapper() -> None:
    """A strict replay never turns a missing record into a result."""
    with pytest.raises(ReplayMissError):
        await replayer([]).run()


async def test_replay_miss_never_reaches_a_live_call() -> None:
    """A runtime miss fails the lines as replay_miss, at no cost, without the breaker."""
    replay = replayer([], strict=False)
    outcome = await replay.run()
    assert set(failures(outcome).values()) == {LLMFailureKind.REPLAY_MISS}
    assert len(replay.sink.records) == 1
    assert replay.sink.records[0].cost_usd == 0.0
    assert replay.wrapper.breaker.consecutive_failures == 0
    assert replay.fake.calls == []


# ---------------------------------------------------------------- verbatim line responses


class FixedText:
    """An adapter that always delivers the same raw text."""

    def __init__(self, text: str) -> None:
        """Keep the text to deliver."""
        self.text = text

    async def complete(self, req: LLMRequest) -> LLMResult:
        """Deliver the text as a 200 end_turn response."""
        return LLMResult(
            status=LLMStatus.OK,
            raw_text=self.text,
            parsed=json.loads(self.text),
            finish_reasons=("end_turn",),
            http_status=200,
        )


def spaced(line_id: str) -> str:
    """Serialise one answer with ASCII escapes and odd spacing, as a model might."""
    return json.dumps(answers()[line_id], ensure_ascii=True, separators=(" ,", " :  "))


async def test_raw_line_response_is_the_exact_source_slice() -> None:
    """The audit keeps each line's bytes as the model sent them, not a re-serialisation."""
    elements = [spaced(line_id) for line_id in IDS]
    text = '{ "lines" :[\n  ' + " ,\n  ".join(elements) + "\n] }"
    outcome = await Harness(adapter=FixedText(text)).run()
    assert answered(outcome) == set(IDS)
    for line_id, element in zip(IDS, elements, strict=True):
        assert outcome.lines[line_id].raw_line_response == element
    assert "\\u00e9" in outcome.lines["L1"].raw_line_response


async def test_duplicates_that_differ_only_in_spacing_are_not_byte_identical() -> None:
    """Only byte-identical duplicates collapse; any other difference is a conflict."""
    canonical = json.dumps(answers()["L1"], ensure_ascii=False)
    others = [json.dumps(answers()[line_id], ensure_ascii=False) for line_id in IDS[1:]]
    text = json.dumps({"lines": []})[:-2] + ", ".join([canonical, spaced("L1"), *others]) + "]}"
    harness = Harness(adapter=FixedText(text))
    outcome = await harness.run()
    assert failures(outcome)["L1"] == LLMFailureKind.DUPLICATE_CONFLICT
    assert harness.sink.records[1].reason_for_call == ReasonForCall.REASK_CONFLICT


async def test_byte_identical_duplicates_in_raw_text_collapse() -> None:
    """Two identical slices for one id count as one answer."""
    elements = [spaced(line_id) for line_id in IDS]
    text = '{"lines": [' + ", ".join([elements[0], *elements]) + "]}"
    harness = Harness(adapter=FixedText(text))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    assert len(harness.sink.records) == 1
    assert outcome.lines["L1"].raw_line_response == elements[0]


# ---------------------------------------------------------------- hook and builder contract


async def test_answer_hook_sees_every_validated_answer() -> None:
    """Answer hook sees every validated answer."""
    seen: list[str] = []

    def hook(answer: LineAnswer) -> LineAnswer:
        """Note the answer's id and pass it on."""
        seen.append(answer.id)
        return answer

    harness = Harness()
    harness.wrapper = LLMWrapper(
        harness.fake,
        PRICING,
        harness.ledger,
        None,
        replace(harness.wrapper.deps, answer_hook=hook),
    )
    await harness.run()
    assert seen == list(IDS)


async def test_builder_must_return_a_request_for_the_asked_lines() -> None:
    """Builder must return a request for the asked lines."""
    harness = Harness()

    def wrong(ids: tuple[str, ...]) -> LLMRequest:
        """Return a request for other lines than asked."""
        return make_request(("L1",))

    with pytest.raises(ValueError, match="line_ids"):
        await harness.wrapper.run_batch(IDS, wrong)


async def test_empty_batch_makes_no_call() -> None:
    """Empty batch makes no call."""
    harness = Harness()
    outcome = await harness.run(())
    assert outcome.lines == {}
    assert harness.fake.calls == []


# ---------------------------------------------------------------- per-line validation (G1-T1a)

TEN_IDS = tuple(f"L{number}" for number in range(1, 11))
LONG_EVIDENCE = " ".join(["mot"] * 14)
BROKEN_ENVELOPE = '{"lines": [{"id": "L1"'


class ByRequest:
    """An adapter that delivers, as a 200 end_turn response, the text a function picks."""

    def __init__(self, pick: Callable[[LLMRequest], str]) -> None:
        """Keep the function from request to raw text."""
        self.pick = pick
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest) -> LLMResult:
        """Deliver the picked text, unparsed, as an OK result."""
        self.calls.append(req)
        return LLMResult(
            status=LLMStatus.OK,
            raw_text=self.pick(req),
            finish_reasons=("end_turn",),
            http_status=200,
        )


def element(line_id: str, **changes: Any) -> str:
    """Serialise one answer object for a line, with some fields changed."""
    return json.dumps({**default_answer(line_id), **changes}, ensure_ascii=False)


def envelope(elements: Iterable[str]) -> str:
    """Wrap verbatim element texts in a batch envelope."""
    return '{"lines": [' + ", ".join(elements) + "]}"


def valid_text(req: LLMRequest) -> str:
    """Return a fully valid batch for the request's lines."""
    return envelope(element(line_id) for line_id in req.line_ids)


def with_bad(line_id: str, bad: str) -> Callable[[LLMRequest], str]:
    """Return a picker that puts ``bad`` in place of one line's object on every call."""

    def pick(req: LLMRequest) -> str:
        """Serialise the batch with the one bad object."""
        return envelope(bad if i == line_id else element(i) for i in req.line_ids)

    return pick


async def test_one_invalid_line_fails_alone_without_bisection() -> None:
    """1 of 10 lines breaks the evidence cap: 9 accepted, 1 malformed with raw kept, 1 call."""
    bad = element("L7", evidence=LONG_EVIDENCE)
    adapter = ByRequest(with_bad("L7", bad))
    harness = Harness(adapter=adapter)
    outcome = await harness.run(TEN_IDS)
    assert answered(outcome) == set(TEN_IDS) - {"L7"}
    assert failures(outcome)["L7"] == LLMFailureKind.MALFORMED
    assert outcome.lines["L7"].raw_line_response == bad
    assert outcome.lines["L7"].answer is None
    assert len(adapter.calls) == 1
    assert [r.reason_for_call for r in harness.sink.records] == [ReasonForCall.FIRST]
    assert outcome.lines["L7"].call_ids == (harness.sink.records[0].call_id,)


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": 101},
        {"confidence": "80"},
        {"kind": "mineral"},
        {"extra_field": 1},
    ],
    ids=["confidence_range", "confidence_type", "bad_enum", "extra_field"],
)
async def test_each_line_level_violation_fails_only_its_line(changes: dict[str, Any]) -> None:
    """Any strict-validation failure of one line object stays with that line."""
    bad = element("L2", **changes)
    adapter = ByRequest(with_bad("L2", bad))
    outcome = await Harness(adapter=adapter).run()
    assert failures(outcome) == {"L1": None, "L2": LLMFailureKind.MALFORMED, "L3": None, "L4": None}
    assert outcome.lines["L2"].raw_line_response == bad
    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    "broken",
    [
        BROKEN_ENVELOPE,
        "not json",
        '{"lines": [], "note": "extra"}',
        '{"lines": {"id": "L1"}}',
        '["lines"]',
        valid_text(make_request(IDS)) + " trailing",
    ],
    ids=["truncated_json", "not_json", "extra_key", "lines_not_a_list", "not_an_object", "trail"],
)
async def test_a_broken_envelope_still_bisects(broken: str) -> None:
    """When the envelope itself is broken, §11.3 bisection applies as before."""
    adapter = ByRequest(lambda req: broken if len(req.line_ids) > 1 else valid_text(req))
    harness = Harness(adapter=adapter)
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    assert [len(r.line_ids) for r in harness.sink.records] == [4, 2, 2, 1, 1, 1, 1]
    assert all(r.reason_for_call == ReasonForCall.SPLIT for r in harness.sink.records[1:])


async def test_a_broken_envelope_on_a_single_line_fails_as_malformed() -> None:
    """A single line whose envelope is broken fails as malformed, as before."""
    adapter = ByRequest(lambda req: BROKEN_ENVELOPE)
    outcome = await Harness(adapter=adapter).run(("L1",))
    assert failures(outcome) == {"L1": LLMFailureKind.MALFORMED}
    assert outcome.lines["L1"].raw_line_response == ""
    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    "orphan",
    [
        json.dumps({**default_answer("L2"), "id": None, "confidence": 500}),
        json.dumps({key: value for key, value in default_answer("L2").items() if key != "id"}),
        json.dumps({**default_answer("L2"), "id": 2}),
        "42",
        '"L2"',
        "[]",
    ],
    ids=["null_id", "no_id", "int_id", "number", "string", "array"],
)
async def test_an_invalid_line_without_a_usable_id_is_missing_and_re_asked(
    orphan: str, caplog: pytest.LogCaptureFixture
) -> None:
    """An invalid object without a usable id is ignored and its line re-asked as missing."""
    first_call = with_bad("L2", orphan)
    adapter = ByRequest(lambda req: valid_text(req) if len(req.line_ids) == 1 else first_call(req))
    harness = Harness(adapter=adapter)
    with caplog.at_level(logging.WARNING):
        outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    first, reask = harness.sink.records
    assert reask.reason_for_call == ReasonForCall.REASK_MISSING
    assert reask.line_ids == ("L2",)
    assert outcome.lines["L2"].call_ids == (first.call_id, reask.call_id)
    assert "ignoring" in caplog.text


async def test_an_always_orphaned_line_fails_as_missing_within_the_budget() -> None:
    """The missing-id re-ask keeps its per-line budget when the id never comes back usable."""
    adapter = ByRequest(with_bad("L2", json.dumps({**default_answer("L2"), "id": None})))
    outcome = await Harness(adapter=adapter).run()
    assert failures(outcome)["L2"] == LLMFailureKind.MISSING_ITEM
    assert answered(outcome) == {"L1", "L3", "L4"}
    assert len(adapter.calls) == 6


async def test_an_invalid_line_with_an_unknown_id_is_ignored_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An invalid object naming an id outside the batch is dropped like any unknown id."""
    stray = element("L99", confidence=-1)
    adapter = ByRequest(lambda req: envelope([*(element(i) for i in req.line_ids), stray]))
    with caplog.at_level(logging.WARNING):
        outcome = await Harness(adapter=adapter).run()
    assert answered(outcome) == set(IDS)
    assert "L99" in caplog.text
    assert len(adapter.calls) == 1


async def test_a_valid_and_an_invalid_object_for_one_id_conflict() -> None:
    """Two different objects for one id conflict, whether or not both are valid."""
    bad = element("L1", confidence=101)

    def pick(req: LLMRequest) -> str:
        """Add an invalid duplicate of L1 on the first call only."""
        extra = [bad] if len(req.line_ids) > 1 else []
        return envelope([*(element(i) for i in req.line_ids), *extra])

    harness = Harness(adapter=ByRequest(pick))
    outcome = await harness.run()
    assert answered(outcome) == set(IDS)
    assert harness.sink.records[1].reason_for_call == ReasonForCall.REASK_CONFLICT
    assert harness.sink.records[1].line_ids == ("L1",)


async def test_byte_identical_invalid_duplicates_fail_once_as_malformed() -> None:
    """Identical invalid objects for one id collapse into one malformed outcome."""
    bad = element("L1", confidence=101)
    adapter = ByRequest(lambda req: envelope([bad, bad, *(element(i) for i in IDS[1:])]))
    outcome = await Harness(adapter=adapter).run()
    assert failures(outcome)["L1"] == LLMFailureKind.MALFORMED
    assert outcome.lines["L1"].raw_line_response == bad
    assert len(adapter.calls) == 1


async def test_the_answer_hook_never_sees_an_invalid_line() -> None:
    """Only validated answers reach the answer hook."""
    seen: list[str] = []

    def hook(answer: LineAnswer) -> LineAnswer:
        """Note the answer's id and pass it on."""
        seen.append(answer.id)
        return answer

    adapter = ByRequest(with_bad("L3", element("L3", evidence=LONG_EVIDENCE)))
    harness = Harness(adapter=adapter)
    deps = replace(harness.wrapper.deps, answer_hook=hook)
    harness.wrapper = LLMWrapper(adapter, PRICING, harness.ledger, None, deps)
    await harness.run()
    assert seen == ["L1", "L2", "L4"]


async def test_replay_reproduces_a_per_line_malformed_outcome() -> None:
    """Replaying a run with one invalid line gives the same outcome from the one record."""
    adapter = ByRequest(with_bad("L4", element("L4", evidence=LONG_EVIDENCE)))
    live = Harness(adapter=adapter)
    live_outcome = await live.run()
    replay = replayer(live.sink.records)
    replayed = await replay.run()
    assert replayed.lines == live_outcome.lines
    assert [r.source_call_id for r in replay.sink.records] == [r.call_id for r in live.sink.records]


async def test_an_invalid_line_on_a_missing_re_ask_fails_without_another_re_ask() -> None:
    """A known id that comes back invalid on the re-ask fails as malformed with both call ids."""
    bad = element("L2", evidence=LONG_EVIDENCE)

    def pick(req: LLMRequest) -> str:
        """Omit L2 on the first call, then return it over the evidence cap."""
        if len(req.line_ids) > 1:
            return envelope(element(i) for i in req.line_ids if i != "L2")
        return envelope([bad])

    adapter = ByRequest(pick)
    harness = Harness(adapter=adapter)
    outcome = await harness.run()
    first, reask = harness.sink.records
    assert reask.reason_for_call == ReasonForCall.REASK_MISSING
    assert answered(outcome) == {"L1", "L3", "L4"}
    assert failures(outcome)["L2"] == LLMFailureKind.MALFORMED
    assert outcome.lines["L2"].raw_line_response == bad
    assert outcome.lines["L2"].call_ids == (first.call_id, reask.call_id)
    assert len(adapter.calls) == 2
