"""The adapter wrapper: retry, budget, cost, recording and cache (DESIGN.md §11.3, §11.9).

This is the single place for retries, throttling, the circuit breaker, the per-line and run
budgets, bisection and id reconciliation. Response checks run in the §11.3 order: the stop
reason (in the adapter), then strict Pydantic validation here. The case-sensitive code check
against the library belongs to the domain; ``WrapperDeps.answer_hook`` sees every validated
answer for callers that want to inspect it.
"""

import asyncio
import hashlib
import json
import logging
import math
import random
import re
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from oris_matcher.domain.decision import LLMFailureKind, PassOutcome, ReasonCode
from oris_matcher.llm.base import (
    HASH_ENCODING,
    REPLAY_MISS_ERROR_CLASS,
    LLMPort,
    LLMRequest,
    LLMResult,
    LLMStatus,
    SystemBlock,
    Usage,
    canonical_json,
)
from oris_matcher.llm.recording import (
    CallRecord,
    CallSink,
    MemoryCallSink,
    ReasonForCall,
    system_blocks_sha256,
    utc_timestamp,
)
from oris_matcher.llm.replay_llm import ReplayLLM, ResponseCache, result_from_record
from oris_matcher.prompts.v1.schema import BatchAnswer, LineAnswer
from oris_matcher.settings import (
    DEFAULT_BREAKER_CONSECUTIVE_FAILURES,
    DEFAULT_LINE_BUDGET_CALLS,
    DEFAULT_LINE_BUDGET_SECONDS,
    DEFAULT_MAX_RETRIES,
    ModelPrice,
    PricingTable,
    Settings,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_BACKOFF_BASE_S = 1.0
DEFAULT_JITTER_S = 0.5
DEFAULT_RNG_SEED = 0
BACKOFF_FACTOR = 2.0
CHARS_PER_TOKEN = 4
CALL_ID_LENGTH = 16
MILLISECONDS_PER_SECOND = 1000.0
BUDGET_EPSILON_USD = 1e-12
BISECT_DIVISOR = 2
RETRYABLE_HTTP = frozenset({408, 409})
JSON_DECODER = json.JSONDecoder()
JSON_WHITESPACE = re.compile(r"[ \t\n\r]*")
JSON_SEPARATOR = ","
JSON_KEY_SEPARATOR = ":"
JSON_ARRAY_END = "]"
JSON_OBJECT_END = "}"
LINES_KEY = "lines"
HTTP_SERVER_ERROR_MIN = 500
HTTP_SERVER_ERROR_MAX = 599
CONTENT_STATUSES = frozenset(
    {LLMStatus.OK, LLMStatus.TRUNCATED, LLMStatus.MALFORMED, LLMStatus.REFUSAL}
)
BISECT_STATUSES = frozenset({LLMStatus.TRUNCATED, LLMStatus.MALFORMED})
TRANSPORT_RETRYABLE = frozenset({LLMStatus.TIMEOUT, LLMStatus.RATE_LIMITED, LLMStatus.OVERLOADED})
FAILURE_BY_STATUS = {
    LLMStatus.TIMEOUT: LLMFailureKind.TIMEOUT,
    LLMStatus.RATE_LIMITED: LLMFailureKind.RATE_LIMITED,
    LLMStatus.OVERLOADED: LLMFailureKind.OVERLOADED,
    LLMStatus.API_ERROR: LLMFailureKind.API_ERROR,
    LLMStatus.TRUNCATED: LLMFailureKind.TRUNCATED,
    LLMStatus.MALFORMED: LLMFailureKind.MALFORMED,
    LLMStatus.REFUSAL: LLMFailureKind.REFUSAL,
}

RequestBuilder = Callable[[tuple[str, ...]], LLMRequest]
AnswerHook = Callable[[LineAnswer], LineAnswer]


def _identity(answer: LineAnswer) -> LineAnswer:
    """Return the answer unchanged."""
    return answer


def estimate_tokens(text: str) -> int:
    """Estimate tokens from characters, for budget reservation only.

    Args:
        text: Any prompt text.

    Returns:
        ``ceil(len(text) / CHARS_PER_TOKEN)``.

    """
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def _default_rng() -> random.Random:
    """Return the seeded jitter generator."""
    return random.Random(DEFAULT_RNG_SEED)


@dataclass(frozen=True)
class WrapperPolicy:
    """Retry, budget and breaker limits (DESIGN.md §11.3).

    Attributes:
        max_retries: Retries after the first call on the retryable set.
        backoff_base_s: Base of the exponential backoff.
        jitter_s: Upper bound of the uniform jitter added to the backoff.
        line_max_calls: Model calls one line may take, across retries, re-asks and splits.
        line_max_inflight_s: In-flight seconds one line may take; waits are excluded.
        breaker_threshold: Consecutive non-throttling failures that trip the breaker.

    """

    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base_s: float = DEFAULT_BACKOFF_BASE_S
    jitter_s: float = DEFAULT_JITTER_S
    line_max_calls: int = DEFAULT_LINE_BUDGET_CALLS
    line_max_inflight_s: float = DEFAULT_LINE_BUDGET_SECONDS
    breaker_threshold: int = DEFAULT_BREAKER_CONSECUTIVE_FAILURES

    @classmethod
    def from_settings(cls, settings: Settings) -> "WrapperPolicy":
        """Build the policy from runtime settings.

        Args:
            settings: The effective settings.

        Returns:
            The policy.

        """
        return cls(
            max_retries=settings.max_retries,
            line_max_calls=settings.line_budget_calls,
            line_max_inflight_s=settings.line_budget_seconds,
            breaker_threshold=settings.breaker_consecutive_failures,
        )


@dataclass(frozen=True)
class WrapperDeps:
    """Injectable collaborators; the defaults suit a live run.

    Attributes:
        sink: Where every attempt's record goes.
        cache: Live response cache. The k-th first attempt of a hash in a run is served by the
            k-th cached response, so a re-ask always reaches the model; ignored in replay.
        sleep: Async sleep used for retry waits.
        rng: Jitter source, seeded so tests are deterministic.
        now: UTC clock for record timestamps.
        count_tokens: Token estimator for budget reservation.
        answer_hook: Called on every validated answer before it is returned.
        call_id_namespace: Mixed into call ids, e.g. the run id.

    """

    sink: CallSink = field(default_factory=MemoryCallSink)
    cache: ResponseCache | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    rng: random.Random = field(default_factory=_default_rng)
    now: Callable[[], datetime] = _utc_now
    count_tokens: Callable[[str], int] = estimate_tokens
    answer_hook: AnswerHook = _identity
    call_id_namespace: str = ""


def cost_of(usage: Usage, price: ModelPrice, per_tokens: int) -> float:
    """Compute the cost of one attempt from its usage.

    Args:
        usage: Reported token usage.
        price: The model's prices.
        per_tokens: Tokens the prices refer to.

    Returns:
        Cost in USD.

    """
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read * price.cache_read
        + usage.cache_write * price.cache_write
    )
    return total / per_tokens


def _split_prefix(blocks: Sequence[SystemBlock]) -> tuple[str, str]:
    """Split system text into the cached prefix (up to the last breakpoint) and the rest."""
    flagged = [index for index, block in enumerate(blocks) if block.cache]
    end = flagged[-1] + 1 if flagged else 0
    prefix = "".join(block.text for block in blocks[:end])
    rest = "".join(block.text for block in blocks[end:])
    return prefix, rest


def reservation_usd(
    req: LLMRequest,
    price: ModelPrice,
    per_tokens: int,
    *,
    prefix_read: bool,
    count_tokens: Callable[[str], int],
) -> float:
    """Reserve the worst-case cost of one dispatch (DESIGN.md §11.3).

    Args:
        req: The request.
        price: The model's prices.
        per_tokens: Tokens the prices refer to.
        prefix_read: Whether a cache read of this prefix has been observed.
        count_tokens: Token estimator.

    Returns:
        Prefix at the cache-write rate (read rate once a read is seen), the rest of the input
        at the input rate, and ``max_tokens`` at the output rate, in USD.

    """
    prefix, rest = _split_prefix(req.system_blocks)
    prefix_rate = price.cache_read if prefix_read else price.cache_write
    uncached = count_tokens(rest) + count_tokens(req.user_payload)
    total = (
        count_tokens(prefix) * prefix_rate + uncached * price.input + req.max_tokens * price.output
    )
    return total / per_tokens


class BudgetLedger:
    """Run-level spend cap with reservations; shared by every batch of a run."""

    def __init__(self, cap_usd: float) -> None:
        """Start with nothing spent or reserved.

        Args:
            cap_usd: The run's cap, e.g. $1.80 per 100 lines.

        """
        self.cap_usd = cap_usd
        self.spent_usd = 0.0
        self.reserved_usd = 0.0
        self._read_prefixes: set[str] = set()

    def try_reserve(self, amount_usd: float) -> bool:
        """Reserve an amount if spent + reserved + amount stays within the cap.

        Args:
            amount_usd: The reservation.

        Returns:
            True when reserved; False means the dispatch must not happen.

        """
        total = self.spent_usd + self.reserved_usd + amount_usd
        if total > self.cap_usd + BUDGET_EPSILON_USD:
            return False
        self.reserved_usd += amount_usd
        return True

    def settle(self, reserved_usd: float, cost_usd: float) -> None:
        """Release a reservation and book the actual cost.

        Args:
            reserved_usd: The reservation being released.
            cost_usd: The cost computed from usage (0 when the call failed before usage).

        """
        self.reserved_usd -= reserved_usd
        self.spent_usd += cost_usd

    def prefix_read(self, req: LLMRequest) -> bool:
        """Tell whether a cache read was observed for this request's system blocks."""
        return system_blocks_sha256(req.system_blocks) in self._read_prefixes

    def observe(self, req: LLMRequest, usage: Usage) -> None:
        """Remember the prefix once a cache read is reported for it.

        Args:
            req: The request.
            usage: Its reported usage.

        """
        if usage.cache_read > 0:
            self._read_prefixes.add(system_blocks_sha256(req.system_blocks))


def is_replay_miss(result: LLMResult) -> bool:
    """Tell whether a result is ReplayLLM's runtime miss."""
    return result.error_class == REPLAY_MISS_ERROR_CLASS


def is_throttling(result: LLMResult) -> bool:
    """Tell whether a result is throttling: a 429, or a 529 carrying retry-after (A39)."""
    if result.status == LLMStatus.RATE_LIMITED:
        return True
    return result.status == LLMStatus.OVERLOADED and result.retry_after_s is not None


class CircuitBreaker:
    """Trips after consecutive non-throttling API failures; throttling never counts.

    Delivered responses (ok, truncated, malformed, refusal) reset the count; a replay miss
    neither counts nor resets.
    """

    def __init__(self, threshold: int) -> None:
        """Start closed.

        Args:
            threshold: Consecutive failures that trip it.

        """
        self.threshold = threshold
        self.consecutive_failures = 0
        self.tripped = False

    def observe(self, result: LLMResult) -> None:
        """Account for one live attempt.

        Args:
            result: The adapter result.

        """
        if is_throttling(result) or is_replay_miss(result):
            return
        if result.status in CONTENT_STATUSES:
            self.consecutive_failures = 0
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold:
            self.tripped = True


def is_retryable(result: LLMResult) -> bool:
    """Classify a result for retry (DESIGN.md §11.3).

    Args:
        result: The adapter result.

    Returns:
        True for timeouts, connection errors, 408, 409, 429 and 5xx (529 included), or an
        ``x-should-retry: true``; False for delivered content, replay misses, other 4xx and
        ``x-should-retry: false``.

    """
    if result.status in CONTENT_STATUSES or is_replay_miss(result):
        return False
    if result.should_retry is not None:
        return result.should_retry
    if result.status in TRANSPORT_RETRYABLE or result.http_status is None:
        return True
    status = result.http_status
    return status in RETRYABLE_HTTP or HTTP_SERVER_ERROR_MIN <= status <= HTTP_SERVER_ERROR_MAX


def failure_kind(result: LLMResult) -> LLMFailureKind:
    """Map a non-OK result to its ``LLM_FAILURE`` kind.

    Args:
        result: A result whose status is not OK.

    Returns:
        The failure kind; a replay miss maps to ``replay_miss``.

    """
    if is_replay_miss(result):
        return LLMFailureKind.REPLAY_MISS
    return FAILURE_BY_STATUS[result.status]


@dataclass(frozen=True)
class LineOutcome:
    """One line's result from one pass: an answer, a failure kind, or a line failure.

    Attributes:
        line_id: The line id.
        call_ids: Every attempt that touched the line, in order.
        answer: The validated answer.
        failure: The ``LLM_FAILURE`` kind when no valid answer arrived.
        line_failure: ``LLM_UNAVAILABLE`` or ``BUDGET_CAP`` when the line was never answered.
        raw_line_response: The line's verbatim JSON object, cut from the accepted response.

    """

    line_id: str
    call_ids: tuple[str, ...]
    answer: LineAnswer | None = None
    failure: LLMFailureKind | None = None
    line_failure: ReasonCode | None = None
    raw_line_response: str = ""

    def __post_init__(self) -> None:
        """Reject an outcome that does not hold exactly one result.

        Raises:
            ValueError: Not exactly one of answer, failure and line_failure is set.

        """
        held = [self.answer, self.failure, self.line_failure]
        if sum(value is not None for value in held) != 1:
            raise ValueError("a line outcome holds exactly one of answer, failure, line_failure")

    def pass_outcome(self) -> PassOutcome | None:
        """Return the domain's pass outcome, or None for a line failure."""
        if self.line_failure is not None:
            return None
        return PassOutcome(call_ids=self.call_ids, answer=self.answer, failure=self.failure)


@dataclass(frozen=True)
class BatchOutcome:
    """The wrapper's output for one batch.

    Attributes:
        lines: Outcome per requested line id, in request order.
        records: Every attempt made for the batch, for attribution.

    """

    lines: dict[str, LineOutcome]
    records: tuple[CallRecord, ...]


@dataclass(frozen=True)
class _AttemptMeta:
    """Why and in what position an attempt is made."""

    parent_call_id: str | None
    attempt_no: int
    reason: ReasonForCall


@dataclass(frozen=True)
class _Timing:
    """Measured or copied timing and cost of an attempt."""

    started: datetime
    ended: datetime
    cost_usd: float
    latency_ms: int


@dataclass(frozen=True)
class _Attempt:
    """One recorded attempt."""

    result: LLMResult
    record: CallRecord


@dataclass(frozen=True)
class _Dispatch:
    """The end of a retry loop: the last attempt, or a line failure."""

    attempt: _Attempt | None = None
    line_failure: ReasonCode | None = None


@dataclass(frozen=True)
class _Task:
    """A request to make for some lines."""

    line_ids: tuple[str, ...]
    reason: ReasonForCall
    parent_call_id: str | None


@dataclass
class _LineState:
    """Per-line budget and history within one batch."""

    call_ids: list[str] = field(default_factory=list)
    calls: int = 0
    inflight_ms: int = 0
    last_failure: LLMFailureKind | None = None
    conflicts: int = 0


class LLMWrapper:
    """Wraps one adapter with retries, budgets, the breaker, recording and the cache.

    With a ``ReplayLLM`` adapter the wrapper replays: the recorded attempt for each request is
    the authority, and its record is copied (call id, timestamps, cost, latency) with
    ``cache_hit`` set and ``source_call_id`` naming the source. Waits are skipped. The breaker
    and the ledger are fed the copied values only to explain attempts the live run never made
    (``LLM_UNAVAILABLE``, ``BUDGET_CAP``); any other absence is a replay miss.
    """

    def __init__(
        self,
        adapter: LLMPort,
        pricing: PricingTable,
        ledger: BudgetLedger,
        policy: WrapperPolicy | None = None,
        deps: WrapperDeps | None = None,
    ) -> None:
        """Bind the wrapper to an adapter and the run's shared ledger.

        Args:
            adapter: The adapter behind the port; a ``ReplayLLM`` selects replay.
            pricing: The dated price table.
            ledger: The run's budget ledger.
            policy: Limits; defaults to §11.3.
            deps: Collaborators; defaults suit a live run.

        """
        self.adapter = adapter
        self.pricing = pricing
        self.ledger = ledger
        self.policy = policy or WrapperPolicy()
        self.deps = deps or WrapperDeps()
        self.breaker = CircuitBreaker(self.policy.breaker_threshold)
        self._replay = adapter if isinstance(adapter, ReplayLLM) else None
        self._issued: dict[str, int] = {}
        self._occurrences: dict[str, int] = {}

    async def run_batch(self, line_ids: Sequence[str], build: RequestBuilder) -> BatchOutcome:
        """Get one validated answer or one failure for every line of a batch.

        Args:
            line_ids: Distinct line ids, in batch order.
            build: Renders the request for any subset of the ids, in the given order.

        Returns:
            The outcome per line and every attempt's record.

        Raises:
            ValueError: Duplicate ids, or the builder returned other line ids.

        """
        ids = tuple(line_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("line ids in a batch must be distinct")
        return await _BatchRun(self, ids, build).run()

    def wait_s(self, retry_index: int, result: LLMResult) -> float:
        """Return max(retry-after, exponential backoff + jitter) before a retry.

        Args:
            retry_index: 0 before the first retry, 1 before the second.
            result: The failed attempt.

        Returns:
            Seconds to wait.

        """
        backoff = self.policy.backoff_base_s * BACKOFF_FACTOR**retry_index
        jittered = backoff + self.deps.rng.uniform(0, self.policy.jitter_s)
        return max(result.retry_after_s or 0.0, jittered)

    async def pause(self, retry_index: int, result: LLMResult) -> None:
        """Wait before a retry; a replay never waits.

        Args:
            retry_index: 0 before the first retry, 1 before the second.
            result: The failed attempt.

        """
        if self._replay is None:
            await self.deps.sleep(self.wait_s(retry_index, result))

    def blocked(self, req: LLMRequest, meta: _AttemptMeta) -> bool:
        """Tell whether the tripped breaker forbids this attempt; a recorded attempt never is."""
        return self.breaker.tripped and self._recorded(req, meta) is None

    def wants_retry(self, req: LLMRequest, attempt: _Attempt, meta: _AttemptMeta) -> bool:
        """Tell whether to retry after an attempt, or, in replay, whether the live run did.

        Args:
            req: The request.
            attempt: The attempt just made.
            meta: The attempt's position.

        Returns:
            True when retries remain and the result is retryable or a recorded retry follows.

        """
        if meta.attempt_no - 1 >= self.policy.max_retries:
            return False
        if is_retryable(attempt.result):
            return True
        return self._recorded(req, _retry_meta(attempt, meta)) is not None

    async def attempt(self, req: LLMRequest, meta: _AttemptMeta) -> _Attempt | ReasonCode | None:
        """Make one attempt, from the replay, the cache or the adapter, and record it.

        Args:
            req: The request.
            meta: Parent, attempt number and reason.

        Returns:
            The attempt; ``BUDGET_CAP`` or ``LLM_UNAVAILABLE`` when it may not be made; None in
            replay when the live run made no such retry.

        """
        if self._replay is not None:
            return await self._replayed(self._replay, req, meta)
        cached = self._cached(req, meta)
        if cached is not None:
            return self._from_cache(req, cached, meta)
        price = self.pricing.lookup(req.provider, req.model)
        reserve = self._reservation(req, price)
        if not self.ledger.try_reserve(reserve):
            return ReasonCode.BUDGET_CAP
        return await self._live(req, meta, price, reserve)

    def _reservation(self, req: LLMRequest, price: ModelPrice) -> float:
        """Return the worst-case cost of dispatching a request now."""
        return reservation_usd(
            req,
            price,
            self.pricing.per_tokens,
            prefix_read=self.ledger.prefix_read(req),
            count_tokens=self.deps.count_tokens,
        )

    def _cached(self, req: LLMRequest, meta: _AttemptMeta) -> CallRecord | None:
        """Look up the live cache for this occurrence of a first attempt's hash."""
        cache = self.deps.cache
        if cache is None or meta.attempt_no != 1:
            return None
        sha = req.sha256()
        occurrence = self._occurrences.get(sha, 0)
        self._occurrences[sha] = occurrence + 1
        return cache.get(sha, occurrence)

    def _recorded(self, req: LLMRequest, meta: _AttemptMeta) -> CallRecord | None:
        """Return the next recorded attempt when it is this attempt of this chain."""
        if self._replay is None:
            return None
        record = self._replay.peek(req)
        if record is None:
            return None
        same = (record.parent_call_id, record.attempt_no) == (meta.parent_call_id, meta.attempt_no)
        return record if same else None

    async def _replayed(
        self, replay: ReplayLLM, req: LLMRequest, meta: _AttemptMeta
    ) -> _Attempt | ReasonCode | None:
        """Serve the recorded attempt, or explain why the live run made none."""
        source = self._recorded(req, meta)
        if source is not None:
            replay.take(req)
            return self._from_replay(req, source)
        if self.breaker.tripped:
            return ReasonCode.LLM_UNAVAILABLE
        reserve = self._reservation(req, self.pricing.lookup(req.provider, req.model))
        if not self.ledger.try_reserve(reserve):
            return ReasonCode.BUDGET_CAP
        self.ledger.settle(reserve, 0.0)
        if meta.attempt_no > 1:
            return None
        result = replay.miss(req)
        moment = self.deps.now()
        return _Attempt(result, self._record(req, result, meta, _Timing(moment, moment, 0.0, 0)))

    def _from_replay(self, req: LLMRequest, source: CallRecord) -> _Attempt:
        """Copy a recorded attempt; its cost is booked as recorded, never re-measured."""
        result = result_from_record(source)
        self.ledger.settle(0.0, source.cost_usd)
        self.ledger.observe(req, result.usage)
        self.breaker.observe(result)
        origin = source.source_call_id or source.call_id
        record = replace(source, cache_hit=True, source_call_id=origin)
        self.deps.sink.write(record)
        return _Attempt(result, record)

    async def _live(
        self, req: LLMRequest, meta: _AttemptMeta, price: ModelPrice, reserve: float
    ) -> _Attempt:
        """Dispatch to the adapter, settle the ledger, feed the breaker and record."""
        started = self.deps.now()
        try:
            result = await self.adapter.complete(req)
        except BaseException:
            self.ledger.settle(reserve, 0.0)
            raise
        ended = self.deps.now()
        cost = cost_of(result.usage, price, self.pricing.per_tokens)
        self.ledger.settle(reserve, cost)
        self.ledger.observe(req, result.usage)
        self.breaker.observe(result)
        record = self._record(req, result, meta, _Timing(started, ended, cost, result.latency_ms))
        if self.deps.cache is not None:
            self.deps.cache.put(record)
        return _Attempt(result, record)

    def _from_cache(self, req: LLMRequest, cached: CallRecord, meta: _AttemptMeta) -> _Attempt:
        """Serve a cache hit, copying the source's cost and latency."""
        result = result_from_record(cached)
        moment = self.deps.now()
        timing = _Timing(moment, moment, cached.cost_usd, cached.latency_ms)
        source = cached.source_call_id or cached.call_id
        return _Attempt(result, self._record(req, result, meta, timing, source))

    def _call_id(self, req: LLMRequest, meta: _AttemptMeta, cache_hit: bool) -> str:
        """Derive a deterministic call id, suffixed when the same seed recurs."""
        seed = canonical_json(
            [
                self.deps.call_id_namespace,
                req.sha256(),
                meta.parent_call_id,
                meta.attempt_no,
                meta.reason.value,
                cache_hit,
            ]
        )
        base = hashlib.sha256(seed.encode(HASH_ENCODING)).hexdigest()[:CALL_ID_LENGTH]
        seen = self._issued.get(base, 0)
        self._issued[base] = seen + 1
        return base if seen == 0 else f"{base}-{seen}"

    def _record(
        self,
        req: LLMRequest,
        result: LLMResult,
        meta: _AttemptMeta,
        timing: _Timing,
        source_call_id: str | None = None,
    ) -> CallRecord:
        """Build and write the record of one attempt."""
        cache_hit = source_call_id is not None
        record = CallRecord(
            call_id=self._call_id(req, meta, cache_hit),
            parent_call_id=meta.parent_call_id,
            attempt_no=meta.attempt_no,
            reason_for_call=meta.reason,
            started_at=utc_timestamp(timing.started),
            ended_at=utc_timestamp(timing.ended),
            **_request_fields(req),
            **_result_fields(result),
            cost_usd=timing.cost_usd,
            latency_ms=timing.latency_ms,
            cache_hit=cache_hit,
            source_call_id=source_call_id,
        )
        self.deps.sink.write(record)
        return record


def _request_fields(req: LLMRequest) -> dict[str, Any]:
    """Return the record fields that come from the request."""
    return {
        "provider_name": req.provider,
        "request_model": req.model,
        "request_sha256": req.sha256(),
        "user_message": req.user_payload,
        "system_blocks_sha256": system_blocks_sha256(req.system_blocks),
        "line_ids": req.line_ids,
    }


def _result_fields(result: LLMResult) -> dict[str, Any]:
    """Return the record fields that come from the adapter result."""
    return {
        "response_model": result.served_model,
        "response_id": result.response_id,
        "provider_request_id": result.provider_request_id,
        "finish_reasons": result.finish_reasons,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "cache_read": result.usage.cache_read,
        "cache_creation": result.usage.cache_write,
        "http_status": result.http_status,
        "error_class": result.error_class,
        "raw_response": result.raw_text,
    }


def _retry_meta(attempt: _Attempt, meta: _AttemptMeta) -> _AttemptMeta:
    """Return the position of the retry that follows an attempt."""
    return _AttemptMeta(attempt.record.call_id, meta.attempt_no + 1, ReasonForCall.RETRY)


class _BatchRun:
    """One ``run_batch`` execution: a queue of tasks over the batch's lines."""

    def __init__(self, wrapper: LLMWrapper, line_ids: tuple[str, ...], build: RequestBuilder):
        """Start with one first-call task for every line.

        Args:
            wrapper: The owning wrapper.
            line_ids: The batch's ids.
            build: The request builder.

        """
        self.wrapper = wrapper
        self.policy = wrapper.policy
        self.line_ids = line_ids
        self.build = build
        self.states = {line_id: _LineState() for line_id in line_ids}
        self.done: dict[str, LineOutcome] = {}
        self.records: list[CallRecord] = []
        self.queue: deque[_Task] = deque()
        if line_ids:
            self.queue.append(_Task(line_ids, ReasonForCall.FIRST, None))

    async def run(self) -> BatchOutcome:
        """Work the queue until every line has an outcome."""
        while self.queue:
            await self._run_task(self.queue.popleft())
        lines = {line_id: self.done[line_id] for line_id in self.line_ids}
        return BatchOutcome(lines=lines, records=tuple(self.records))

    async def _run_task(self, task: _Task) -> None:
        """Dispatch one task, unless the breaker or the line budget forbids it."""
        ids = task.line_ids
        req = self.build(ids)
        if req.line_ids != ids:
            raise ValueError(f"builder returned line_ids {req.line_ids} for {ids}")
        if self.wrapper.blocked(req, _AttemptMeta(task.parent_call_id, 1, task.reason)):
            self._fail(ids, line_failure=ReasonCode.LLM_UNAVAILABLE)
            return
        if self._exhausted(ids):
            self._fail_exhausted(ids)
            return
        dispatch = await self._dispatch(req, task)
        self._handle(task, dispatch)

    async def _dispatch(self, req: LLMRequest, task: _Task) -> _Dispatch:
        """Run 1 call + up to ``max_retries`` retries on the retryable set."""
        meta = _AttemptMeta(task.parent_call_id, 1, task.reason)
        last: _Attempt | None = None
        while True:
            if self.wrapper.blocked(req, meta):
                return _Dispatch(line_failure=ReasonCode.LLM_UNAVAILABLE)
            if last is not None and self._exhausted(req.line_ids):
                return _Dispatch(attempt=last)
            made = await self.wrapper.attempt(req, meta)
            if isinstance(made, ReasonCode):
                return _Dispatch(line_failure=made)
            if made is None:
                return _Dispatch(attempt=last)
            self._charge(req.line_ids, made)
            last = made
            if not self.wrapper.wants_retry(req, made, meta):
                return _Dispatch(attempt=made)
            await self.wrapper.pause(meta.attempt_no - 1, made.result)
            meta = _retry_meta(made, meta)

    def _charge(self, line_ids: Iterable[str], attempt: _Attempt) -> None:
        """Count one call and its in-flight time against each line."""
        self.records.append(attempt.record)
        for line_id in line_ids:
            state = self.states[line_id]
            state.calls += 1
            state.inflight_ms += attempt.result.latency_ms
            state.call_ids.append(attempt.record.call_id)

    def _exhausted(self, line_ids: Iterable[str]) -> bool:
        """Tell whether any line has used its call or in-flight budget."""
        limit_ms = self.policy.line_max_inflight_s * MILLISECONDS_PER_SECOND
        return any(
            self.states[line_id].calls >= self.policy.line_max_calls
            or self.states[line_id].inflight_ms >= limit_ms
            for line_id in line_ids
        )

    def _handle(self, task: _Task, dispatch: _Dispatch) -> None:
        """Turn the end of a retry loop into outcomes or follow-up tasks."""
        if dispatch.attempt is None:
            self._fail(task.line_ids, line_failure=dispatch.line_failure)
            return
        result = dispatch.attempt.result
        call_id = dispatch.attempt.record.call_id
        if result.status in BISECT_STATUSES:
            self._bisect(task.line_ids, call_id, failure_kind(result))
            return
        if result.status != LLMStatus.OK:
            self._fail(task.line_ids, failure=failure_kind(result))
            return
        pairs = _validated_lines(result.raw_text)
        if pairs is None:
            self._bisect(task.line_ids, call_id, LLMFailureKind.MALFORMED)
            return
        self._reconcile(task.line_ids, pairs, call_id)

    def _bisect(self, line_ids: tuple[str, ...], call_id: str, kind: LLMFailureKind) -> None:
        """Split a failed batch in halves, or fail a single line with the kind."""
        if len(line_ids) == 1:
            self._fail(line_ids, failure=kind)
            return
        self._remember(line_ids, kind)
        middle = len(line_ids) // BISECT_DIVISOR
        for half in (line_ids[:middle], line_ids[middle:]):
            self.queue.append(_Task(half, ReasonForCall.SPLIT, call_id))

    def _reconcile(
        self, line_ids: tuple[str, ...], pairs: list[tuple[LineAnswer, str]], call_id: str
    ) -> None:
        """Accept unique answers; re-ask missing and conflicting ids; ignore unknown ids."""
        grouped = _group_by_id(pairs, frozenset(line_ids), call_id)
        missing: list[str] = []
        conflicting: list[str] = []
        for line_id in line_ids:
            entries = grouped.get(line_id, [])
            if not entries:
                missing.append(line_id)
            elif len({raw for _, raw in entries}) > 1:
                conflicting.append(line_id)
            else:
                self._accept(line_id, *entries[0])
        self._reask(missing, LLMFailureKind.MISSING_ITEM, ReasonForCall.REASK_MISSING, call_id)
        self._reask_conflicts(conflicting, call_id)

    def _reask_conflicts(self, line_ids: list[str], call_id: str) -> None:
        """Re-ask a conflicting duplicate once; a second conflict fails closed."""
        first_time: list[str] = []
        for line_id in line_ids:
            state = self.states[line_id]
            state.conflicts += 1
            if state.conflicts > 1:
                self._fail((line_id,), failure=LLMFailureKind.DUPLICATE_CONFLICT)
            else:
                first_time.append(line_id)
        kind = LLMFailureKind.DUPLICATE_CONFLICT
        self._reask(first_time, kind, ReasonForCall.REASK_CONFLICT, call_id)

    def _reask(
        self, line_ids: list[str], kind: LLMFailureKind, reason: ReasonForCall, call_id: str
    ) -> None:
        """Queue a re-ask for some lines, remembering why."""
        if not line_ids:
            return
        self._remember(line_ids, kind)
        self.queue.append(_Task(tuple(line_ids), reason, call_id))

    def _remember(self, line_ids: Iterable[str], kind: LLMFailureKind) -> None:
        """Record the failure a line ends with if its budget runs out."""
        for line_id in line_ids:
            self.states[line_id].last_failure = kind

    def _accept(self, line_id: str, answer: LineAnswer, raw: str) -> None:
        """Store a validated answer, after the answer hook."""
        checked = self.wrapper.deps.answer_hook(answer)
        call_ids = tuple(self.states[line_id].call_ids)
        self.done[line_id] = LineOutcome(line_id, call_ids, answer=checked, raw_line_response=raw)

    def _fail(
        self,
        line_ids: Iterable[str],
        *,
        failure: LLMFailureKind | None = None,
        line_failure: ReasonCode | None = None,
    ) -> None:
        """Store a failure outcome for each line."""
        for line_id in line_ids:
            call_ids = tuple(self.states[line_id].call_ids)
            self.done[line_id] = LineOutcome(
                line_id, call_ids, failure=failure, line_failure=line_failure
            )

    def _fail_exhausted(self, line_ids: Iterable[str]) -> None:
        """Fail lines whose budget ran out with the failure that consumed it."""
        for line_id in line_ids:
            kind = self.states[line_id].last_failure or LLMFailureKind.API_ERROR
            self._fail((line_id,), failure=kind)


def _validated_lines(raw_text: str) -> list[tuple[LineAnswer, str]] | None:
    """Validate a batch strictly; pair each answer with its verbatim JSON object."""
    try:
        batch = BatchAnswer.model_validate_json(raw_text)
    except ValidationError:
        return None
    return list(zip(batch.lines, line_slices(raw_text), strict=True))


def _skip_whitespace(text: str, index: int) -> int:
    """Return the first index at or after ``index`` that is not JSON whitespace."""
    match = JSON_WHITESPACE.match(text, index)
    return match.end() if match else index


def _after(text: str, index: int, separator: str) -> int:
    """Step over whitespace and one optional separator, then whitespace again."""
    index = _skip_whitespace(text, index)
    if text.startswith(separator, index):
        index = _skip_whitespace(text, index + len(separator))
    return index


def _element_slices(text: str, start: int) -> list[str]:
    """Return the source text of each element of the JSON array opening at ``start``."""
    slices: list[str] = []
    index = _skip_whitespace(text, start + 1)
    while not text.startswith(JSON_ARRAY_END, index):
        _, end = JSON_DECODER.raw_decode(text, index)
        slices.append(text[index:end])
        index = _after(text, end, JSON_SEPARATOR)
    return slices


def line_slices(raw_text: str) -> list[str]:
    """Cut the exact source text of each element of a batch's top-level ``lines`` array.

    The text must already be a valid JSON object; as in ``json.loads``, the last ``lines`` key
    wins.

    Args:
        raw_text: A response that passed strict validation.

    Returns:
        One verbatim substring per element of ``lines``, in order.

    """
    slices: list[str] = []
    index = _skip_whitespace(raw_text, _skip_whitespace(raw_text, 0) + 1)
    while not raw_text.startswith(JSON_OBJECT_END, index):
        key, end = JSON_DECODER.raw_decode(raw_text, index)
        index = _after(raw_text, end, JSON_KEY_SEPARATOR)
        _, value_end = JSON_DECODER.raw_decode(raw_text, index)
        if key == LINES_KEY:
            slices = _element_slices(raw_text, index)
        index = _after(raw_text, value_end, JSON_SEPARATOR)
    return slices


def _group_by_id(
    pairs: list[tuple[LineAnswer, str]], known: frozenset[str], call_id: str
) -> dict[str, list[tuple[LineAnswer, str]]]:
    """Group answers by id, logging and dropping ids the request did not carry."""
    grouped: dict[str, list[tuple[LineAnswer, str]]] = {}
    for answer, raw in pairs:
        if answer.id not in known:
            LOGGER.warning("ignoring unknown line id %r in call %s", answer.id, call_id)
            continue
        grouped.setdefault(answer.id, []).append((answer, raw))
    return grouped
