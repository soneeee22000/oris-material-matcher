"""Replay of recorded attempts, and the live response cache (DESIGN.md §9.6, §11.5, §11.9).

``RecordedRun`` holds every attempt of a run, failures included, per request hash in recording
order. ``ReplayLLM`` serves the k-th request with a hash from the k-th recorded attempt with
that hash, so a replay walks the same attempts as the live run. It holds no live adapter, so a
miss can never fall through to a paid call: it raises ``ReplayMissError`` in strict (test) mode
and returns a non-retryable result marked ``REPLAY_MISS_ERROR_CLASS`` at runtime, which the
wrapper reports as ``LLM_FAILURE:replay_miss``. It also holds the attempts the recorded run
declined (``DeclinedAttempt``), so the wrapper reads a breaker or budget refusal back instead of
recomputing it in replay order; ``declined=None`` marks a run recorded before declines were
kept.

``ResponseCache`` is the live ``--cache`` store. It keeps content responses only, per hash in
recording order, and serves them by occurrence.
"""

from collections.abc import Iterable
from pathlib import Path

from oris_matcher.llm.base import (
    REPLAY_MISS_ERROR_CLASS,
    LLMRequest,
    LLMResult,
    LLMStatus,
    Usage,
    classify_content,
    require_allowed_model,
)
from oris_matcher.llm.recording import CallRecord, DeclinedAttempt, read_calls_jsonl

HTTP_OK = 200
HTTP_RATE_LIMITED = 429
HTTP_OVERLOADED = 529
TIMEOUT_ERROR_CLASSES = frozenset({"APITimeoutError"})
STATUS_BY_HTTP = {HTTP_RATE_LIMITED: LLMStatus.RATE_LIMITED, HTTP_OVERLOADED: LLMStatus.OVERLOADED}


class ReplayMissError(LookupError):
    """A request hash has no recorded response."""


ReplayMiss = ReplayMissError


def is_cacheable(record: CallRecord) -> bool:
    """Tell whether a record holds delivered content.

    Args:
        record: One attempt.

    Returns:
        True for an HTTP 200 response without an error class.

    """
    return record.http_status == HTTP_OK and record.error_class is None


def failed_status(record: CallRecord) -> LLMStatus:
    """Re-derive the status of an attempt that delivered no content.

    Args:
        record: An attempt that is not cacheable.

    Returns:
        ``MALFORMED`` for an unreadable 200 body, ``TIMEOUT`` or ``API_ERROR`` when no response
        arrived, otherwise the status of the HTTP code.

    """
    if record.http_status == HTTP_OK:
        return LLMStatus.MALFORMED
    if record.http_status is None:
        timed_out = record.error_class in TIMEOUT_ERROR_CLASSES
        return LLMStatus.TIMEOUT if timed_out else LLMStatus.API_ERROR
    return STATUS_BY_HTTP.get(record.http_status, LLMStatus.API_ERROR)


def _usage(record: CallRecord) -> Usage:
    """Rebuild the token usage of a recorded attempt."""
    return Usage(
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cache_read=record.cache_read,
        cache_write=record.cache_creation,
    )


def result_from_record(record: CallRecord) -> LLMResult:
    """Rebuild the adapter result of a recorded attempt.

    ``retry_after_s`` and ``should_retry`` are not ``calls.jsonl`` fields, so they come back
    as None.

    Args:
        record: Any recorded attempt.

    Returns:
        The result; content is re-classified from its finish reasons and raw text.

    """
    if is_cacheable(record):
        status, parsed = classify_content(record.finish_reasons, record.raw_response)
    else:
        status, parsed = failed_status(record), None
    return LLMResult(
        status=status,
        raw_text=record.raw_response,
        parsed=parsed,
        usage=_usage(record),
        latency_ms=record.latency_ms,
        served_model=record.response_model,
        provider_request_id=record.provider_request_id,
        response_id=record.response_id,
        finish_reasons=record.finish_reasons,
        http_status=record.http_status,
        error_class=record.error_class,
    )


def _by_hash(records: Iterable[CallRecord]) -> dict[str, list[CallRecord]]:
    """Group records by request hash, keeping recording order."""
    grouped: dict[str, list[CallRecord]] = {}
    for record in records:
        grouped.setdefault(record.request_sha256, []).append(record)
    return grouped


class RecordedRun:
    """Every attempt of a recorded run, per request hash in recording order."""

    def __init__(self, records: Iterable[CallRecord] = ()) -> None:
        """Index the records.

        Args:
            records: Attempts in recording order, failed ones included.

        """
        self._attempts = _by_hash(records)

    @classmethod
    def from_records(cls, records: Iterable[CallRecord]) -> "RecordedRun":
        """Build a recorded run from records.

        Args:
            records: Attempts in recording order.

        Returns:
            The recorded run.

        """
        return cls(records)

    @classmethod
    def from_calls_jsonl(cls, path: Path) -> "RecordedRun":
        """Build a recorded run from a run's ``calls.jsonl``.

        Args:
            path: The file.

        Returns:
            The recorded run.

        """
        return cls(read_calls_jsonl(path))

    def attempts(self, request_sha256: str) -> tuple[CallRecord, ...]:
        """Return the recorded attempts for a request hash.

        Args:
            request_sha256: ``LLMRequest.sha256()``.

        Returns:
            The attempts in recording order; empty when none were recorded.

        """
        return tuple(self._attempts.get(request_sha256, ()))

    def __len__(self) -> int:
        """Return the number of recorded attempts."""
        return sum(len(records) for records in self._attempts.values())


class ResponseCache:
    """Content records per ``request_sha256``, in recording order, for the live cache."""

    def __init__(self) -> None:
        """Start empty."""
        self._records: dict[str, list[CallRecord]] = {}

    @classmethod
    def from_records(cls, records: Iterable[CallRecord]) -> "ResponseCache":
        """Build a cache from records, keeping only cacheable ones.

        Args:
            records: Attempts in recording order.

        Returns:
            The cache.

        """
        cache = cls()
        for record in records:
            cache.put(record)
        return cache

    @classmethod
    def from_calls_jsonl(cls, path: Path) -> "ResponseCache":
        """Build a cache from a run's ``calls.jsonl``.

        Args:
            path: The file.

        Returns:
            The cache.

        """
        return cls.from_records(read_calls_jsonl(path))

    def put(self, record: CallRecord) -> None:
        """Append a cacheable record to its hash's list; drop any other record.

        Args:
            record: One attempt.

        """
        if is_cacheable(record):
            self._records.setdefault(record.request_sha256, []).append(record)

    def get(self, request_sha256: str, occurrence: int = 0) -> CallRecord | None:
        """Return the content record for one occurrence of a request hash.

        Args:
            request_sha256: ``LLMRequest.sha256()``.
            occurrence: 0 for the first issue of the hash in a run, 1 for the second.

        Returns:
            The record, or None on a miss.

        """
        records = self._records.get(request_sha256, [])
        return records[occurrence] if occurrence < len(records) else None

    def __len__(self) -> int:
        """Return the number of cached request hashes."""
        return len(self._records)


class ReplayLLM:
    """Serves recorded attempts behind the LLM port; never makes a live call."""

    def __init__(
        self,
        run: RecordedRun,
        model: str,
        allowlist: Iterable[str],
        *,
        strict: bool = True,
        declined: Iterable[DeclinedAttempt] | None = None,
    ) -> None:
        """Build the replay adapter, refusing a model outside the allowlist.

        Args:
            run: The recorded attempts.
            model: The model id of the replayed run.
            allowlist: Patterns from ``config/models.toml``.
            strict: Raise on a miss (tests) instead of returning a replay-miss result.
            declined: The attempts the recorded run declined; None for a run recorded before
                they were kept, whose refusals the wrapper then re-derives.

        Raises:
            ModelNotAllowedError: The model is not on the allowlist.

        """
        self._allowlist = tuple(allowlist)
        require_allowed_model(model, self._allowlist)
        self.model = model
        self.run = run
        self.strict = strict
        self._served: dict[str, int] = {}
        self.declined: dict[tuple[str, str | None, int], DeclinedAttempt] | None = None
        if declined is not None:
            self.declined = {}
            for attempt in declined:
                self.declined.setdefault(attempt.key, attempt)

    def declined_reason(
        self, req: LLMRequest, parent_call_id: str | None, attempt_no: int
    ) -> str | None:
        """Return why the recorded run declined this attempt, or None when it did not.

        Args:
            req: The request.
            parent_call_id: The attempt's parent.
            attempt_no: Its position in the retry chain.

        Returns:
            ``LLM_UNAVAILABLE`` or ``BUDGET_CAP``; None when not declined or not recorded.

        """
        if self.declined is None:
            return None
        found = self.declined.get((req.sha256(), parent_call_id, attempt_no))
        return None if found is None else found.reason

    def peek(self, req: LLMRequest) -> CallRecord | None:
        """Return the next recorded attempt for a request without consuming it.

        Args:
            req: The request.

        Returns:
            The attempt, or None when every recorded attempt for its hash is used.

        """
        sha = req.sha256()
        attempts = self.run.attempts(sha)
        index = self._served.get(sha, 0)
        return attempts[index] if index < len(attempts) else None

    def take(self, req: LLMRequest) -> CallRecord | None:
        """Return and consume the next recorded attempt for a request.

        Args:
            req: The request.

        Returns:
            The attempt, or None when every recorded attempt for its hash is used.

        """
        record = self.peek(req)
        if record is not None:
            self._served[record.request_sha256] = self._served.get(record.request_sha256, 0) + 1
        return record

    def miss(self, req: LLMRequest) -> LLMResult:
        """Report a request that has no recorded attempt.

        Args:
            req: The request.

        Returns:
            A non-retryable replay-miss result, at runtime.

        Raises:
            ReplayMissError: Strict mode.

        """
        if self.strict:
            raise ReplayMissError(f"no recorded response for request {req.sha256()}")
        return LLMResult(
            status=LLMStatus.API_ERROR,
            raw_text="",
            error_class=REPLAY_MISS_ERROR_CLASS,
            should_retry=False,
        )

    async def complete(self, req: LLMRequest) -> LLMResult:
        """Serve the next recorded attempt for a request.

        Args:
            req: The request.

        Returns:
            The recorded result, or a non-retryable replay-miss result at runtime.

        Raises:
            ReplayMissError: Strict mode and no recorded attempt left.
            ModelNotAllowedError: The request names a model outside the allowlist.

        """
        require_allowed_model(req.model, self._allowlist)
        record = self.take(req)
        return self.miss(req) if record is None else result_from_record(record)
