"""Per-attempt call records with the ``calls.jsonl`` fields, and their sinks (DESIGN.md §9.6).

The serialised form uses the OTel GenAI attribute names (``gen_ai.*``) as flat keys.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from oris_matcher.llm.base import HASH_ENCODING, SystemBlock, canonical_json

JSONL_ENCODING = "utf-8"
JSONL_NEWLINE = "\n"
UTC_SUFFIX = "Z"
UTC_OFFSET = "+00:00"
TIMESTAMP_PRECISION = "milliseconds"

SERIALISED_NAMES = {
    "provider_name": "gen_ai.provider.name",
    "request_model": "gen_ai.request.model",
    "response_model": "gen_ai.response.model",
    "response_id": "gen_ai.response.id",
    "finish_reasons": "gen_ai.response.finish_reasons",
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "cache_read": "gen_ai.usage.cache_read",
    "cache_creation": "gen_ai.usage.cache_creation",
}
TUPLE_FIELDS = frozenset({"finish_reasons", "line_ids"})


class ReasonForCall(StrEnum):
    """Why an attempt was made."""

    FIRST = "first"
    RETRY = "retry"
    REASK_MISSING = "reask_missing"
    REASK_CONFLICT = "reask_conflict"
    SPLIT = "split"


@dataclass(frozen=True)
class CallRecord:
    """One attempt, exactly the ``calls.jsonl`` fields of §9.6.

    Attributes:
        call_id: This attempt's id.
        parent_call_id: The attempt that caused this one, or None for a first call.
        attempt_no: 1 for a first call, split or re-ask; +1 per retry.
        reason_for_call: Why the attempt was made.
        started_at: UTC start, ISO 8601 with a ``Z`` suffix.
        ended_at: UTC end, ISO 8601 with a ``Z`` suffix.
        provider_name: ``gen_ai.provider.name``.
        request_model: ``gen_ai.request.model``.
        response_model: ``gen_ai.response.model``, the served model.
        response_id: ``gen_ai.response.id``.
        provider_request_id: The provider's request id header.
        finish_reasons: ``gen_ai.response.finish_reasons``.
        input_tokens: ``gen_ai.usage.input_tokens``.
        output_tokens: ``gen_ai.usage.output_tokens``.
        cache_read: ``gen_ai.usage.cache_read``.
        cache_creation: ``gen_ai.usage.cache_creation``.
        request_sha256: ``LLMRequest.sha256()``, the cache key.
        user_message: The full user message.
        system_blocks_sha256: Hash of the system blocks, stored once under ``prompts/``.
        http_status: HTTP status, when a response arrived.
        error_class: Transport or API error class.
        raw_response: The raw response text.
        cost_usd: Cost of the attempt; a cache hit copies its source's cost.
        latency_ms: Latency of the attempt; a cache hit copies its source's latency.
        line_ids: Line ids in the request.
        cache_hit: Whether the response came from the cache.
        source_call_id: The original attempt a cache hit copies.

    """

    call_id: str
    parent_call_id: str | None
    attempt_no: int
    reason_for_call: ReasonForCall
    started_at: str
    ended_at: str
    provider_name: str
    request_model: str
    response_model: str | None
    response_id: str | None
    provider_request_id: str | None
    finish_reasons: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_creation: int
    request_sha256: str
    user_message: str
    system_blocks_sha256: str
    http_status: int | None
    error_class: str | None
    raw_response: str
    cost_usd: float
    latency_ms: int
    line_ids: tuple[str, ...]
    cache_hit: bool
    source_call_id: str | None

    def to_json_dict(self) -> dict[str, Any]:
        """Return the serialised form, with ``gen_ai.*`` names and lists for tuples."""
        serialised: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name in TUPLE_FIELDS:
                value = list(value)
            serialised[SERIALISED_NAMES.get(item.name, item.name)] = value
        serialised["reason_for_call"] = self.reason_for_call.value
        return serialised

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "CallRecord":
        """Rebuild a record from its serialised form.

        Args:
            data: One parsed ``calls.jsonl`` object.

        Returns:
            The record.

        Raises:
            KeyError: A field is missing.

        """
        values: dict[str, Any] = {}
        for item in fields(cls):
            value = data[SERIALISED_NAMES.get(item.name, item.name)]
            values[item.name] = tuple(value) if item.name in TUPLE_FIELDS else value
        values["reason_for_call"] = ReasonForCall(values["reason_for_call"])
        return cls(**values)


CALL_RECORD_KEYS: tuple[str, ...] = tuple(
    SERIALISED_NAMES.get(item.name, item.name) for item in fields(CallRecord)
)


class CallSink(Protocol):
    """Where call records go."""

    def write(self, record: CallRecord) -> None:
        """Store one record.

        Args:
            record: The attempt's record.

        """
        ...


class MemoryCallSink:
    """Keeps records in memory, in write order."""

    def __init__(self) -> None:
        """Start empty."""
        self.records: list[CallRecord] = []

    def write(self, record: CallRecord) -> None:
        """Append one record.

        Args:
            record: The attempt's record.

        """
        self.records.append(record)


def record_line(record: CallRecord) -> str:
    """Serialise one record as a ``calls.jsonl`` line, without the newline.

    Args:
        record: The record.

    Returns:
        JSON with sorted keys and non-ASCII text kept.

    """
    return json.dumps(record.to_json_dict(), sort_keys=True, ensure_ascii=False)


class JsonlCallSink:
    """Appends records to a ``calls.jsonl`` file: UTF-8, one JSON object per LF-terminated line."""

    def __init__(self, path: Path) -> None:
        """Bind the sink to a file; it is created on the first write.

        Args:
            path: The ``calls.jsonl`` path.

        """
        self.path = path

    def write(self, record: CallRecord) -> None:
        """Append one record.

        Args:
            record: The attempt's record.

        """
        with open(self.path, "a", encoding=JSONL_ENCODING, newline=JSONL_NEWLINE) as handle:
            handle.write(record_line(record) + JSONL_NEWLINE)


def read_calls_jsonl(path: Path) -> list[CallRecord]:
    """Read every record of a ``calls.jsonl`` file, skipping blank lines.

    Args:
        path: The file.

    Returns:
        The records in file order.

    """
    with open(path, encoding=JSONL_ENCODING) as handle:
        return [CallRecord.from_json_dict(json.loads(line)) for line in handle if line.strip()]


def utc_timestamp(moment: datetime) -> str:
    """Format a timezone-aware moment as ISO 8601 UTC with a ``Z`` suffix.

    Args:
        moment: An aware datetime.

    Returns:
        E.g. ``2026-10-05T10:00:00.000Z``.

    """
    text = moment.isoformat(timespec=TIMESTAMP_PRECISION)
    return text.removesuffix(UTC_OFFSET) + UTC_SUFFIX


def system_blocks_sha256(blocks: Iterable[SystemBlock]) -> str:
    """Hash system blocks canonically, including each block's cache flag.

    Args:
        blocks: The request's system blocks.

    Returns:
        The SHA-256 hex digest.

    """
    canonical = canonical_json([{"text": block.text, "cache": block.cache} for block in blocks])
    return hashlib.sha256(canonical.encode(HASH_ENCODING)).hexdigest()


@dataclass(frozen=True)
class LineAttribution:
    """Cost and latency attributed to one line (§9.6).

    Attributes:
        cost_usd: Sum over attempts a of cost(a) / n_a.
        latency_ms: round(sum over attempts a of elapsed(a) / n_a).
        call_ids: Attempts that touched the line, in record order.

    """

    cost_usd: float
    latency_ms: int
    call_ids: tuple[str, ...]


def attribute_costs(records: Sequence[CallRecord]) -> dict[str, LineAttribution]:
    """Split each attempt's cost and latency evenly over the lines it carried.

    Args:
        records: Every attempt of a run, cache hits included.

    Returns:
        Attribution per line id, in first-seen order.

    """
    costs: dict[str, float] = {}
    latencies: dict[str, float] = {}
    call_ids: dict[str, list[str]] = {}
    for record in records:
        share = len(record.line_ids)
        for line_id in record.line_ids:
            costs[line_id] = costs.get(line_id, 0.0) + record.cost_usd / share
            latencies[line_id] = latencies.get(line_id, 0.0) + record.latency_ms / share
            call_ids.setdefault(line_id, []).append(record.call_id)
    return {
        line_id: LineAttribution(costs[line_id], round(latencies[line_id]), tuple(ids))
        for line_id, ids in call_ids.items()
    }
