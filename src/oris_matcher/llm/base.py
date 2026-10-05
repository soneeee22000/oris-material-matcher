"""The LLM port: request and result contracts every adapter implements (DESIGN.md §11.9).

Adapters never raise for API failures; they report them through ``LLMResult.status`` and
always return the raw response text.
"""

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

HASH_ENCODING = "utf-8"
ANTHROPIC_RATE_LIMIT_PREFIX = "anthropic-ratelimit-"
OPENAI_RATE_LIMIT_PREFIX = "x-ratelimit-"


class LLMStatus(StrEnum):
    """Outcome of one adapter call."""

    OK = "ok"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"
    API_ERROR = "api_error"
    TRUNCATED = "truncated"
    REFUSAL = "refusal"
    MALFORMED = "malformed"


def canonical_json(value: Any) -> str:
    """Serialise a JSON value canonically: sorted keys, no spaces, non-ASCII kept.

    Args:
        value: Any JSON-serialisable value.

    Returns:
        The canonical JSON text.

    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def select_rate_limit_headers(
    headers: Mapping[str, str], prefix: str
) -> tuple[tuple[str, str], ...]:
    """Pick the rate-limit headers an adapter records on ``LLMResult`` (DESIGN.md §9.6, §11.3).

    Args:
        headers: Response headers; names are compared case-insensitively.
        prefix: Lower-case name prefix, e.g. ``ANTHROPIC_RATE_LIMIT_PREFIX``.

    Returns:
        (lower-case name, value) pairs whose name starts with ``prefix``, sorted by name.

    """
    selected = ((name.lower(), value) for name, value in headers.items())
    return tuple(sorted(pair for pair in selected if pair[0].startswith(prefix)))


@dataclass(frozen=True)
class SystemBlock:
    """One system prompt block.

    Attributes:
        text: The block's text.
        cache: Whether a cache breakpoint follows this block.

    """

    text: str
    cache: bool


@dataclass(frozen=True)
class LLMRequest:
    """One model call.

    Attributes:
        provider: Provider name, e.g. ``anthropic``.
        model: Requested model snapshot id.
        system_blocks: System blocks in cache-friendly order.
        schema_json: Output JSON schema as JSON text (canonicalised when hashed).
        user_payload: The full user message.
        max_tokens: Output token cap.
        temperature: Sampling temperature.
        line_ids: Line ids carried by the batch, for attribution.

    """

    provider: str
    model: str
    system_blocks: tuple[SystemBlock, ...]
    schema_json: str
    user_payload: str
    max_tokens: int
    temperature: float
    line_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject a ``schema_json`` that is not a JSON object, where the request is built.

        Raises:
            ValueError: ``schema_json`` is not valid JSON, or not an object.

        """
        try:
            schema = json.loads(self.schema_json)
        except json.JSONDecodeError as error:
            raise ValueError(f"schema_json is not valid JSON: {error}") from error
        if not isinstance(schema, dict):
            raise ValueError("schema_json must encode a JSON object")

    def schema(self) -> dict[str, Any]:
        """Return the output schema as a new dict."""
        parsed: dict[str, Any] = json.loads(self.schema_json)
        return parsed

    def sha256(self) -> str:
        """Return the canonical request hash, which is the response-cache key (DESIGN.md §11.5).

        Covers model, system blocks, schema, temperature (as a float), max_tokens and user
        payload. ``provider`` and ``line_ids`` are excluded, so batches with the same content
        share a key.

        Returns:
            The SHA-256 hex digest.

        """
        canonical = canonical_json(
            {
                "model": self.model,
                "system_blocks": [
                    {"text": block.text, "cache": block.cache} for block in self.system_blocks
                ],
                "schema": self.schema(),
                "temperature": float(self.temperature),
                "max_tokens": self.max_tokens,
                "user_payload": self.user_payload,
            }
        )
        return hashlib.sha256(canonical.encode(HASH_ENCODING)).hexdigest()


@dataclass(frozen=True)
class Usage:
    """Token usage reported by the provider.

    Attributes:
        input_tokens: Uncached input tokens.
        output_tokens: Output tokens.
        cache_read: Input tokens read from the prompt cache.
        cache_write: Input tokens written to the prompt cache.

    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass(frozen=True)
class LLMResult:
    """Outcome of one adapter call; never an exception for API failures.

    The result is hashable. ``parsed`` is excluded from hashing and equality, since it derives
    from ``raw_text``; it is deep-copied and exposed read-only at the top level.

    Attributes:
        status: Call outcome.
        raw_text: The raw response text, kept even when malformed.
        parsed: The parsed JSON object, or None.
        usage: Token usage.
        latency_ms: Wall time of the call.
        served_model: Model id reported by the provider.
        provider_request_id: Provider's request id header.
        response_id: Provider's response id.
        finish_reasons: Stop or finish reasons.
        http_status: HTTP status code, when a response arrived.
        error_class: Exception class name for transport or API errors.
        retry_after_s: Server-suggested wait before retrying.
        should_retry: The provider's ``x-should-retry`` hint, when present.
        rate_limit_headers: Rate-limit response headers, from ``select_rate_limit_headers``.

    """

    status: LLMStatus
    raw_text: str
    parsed: Mapping[str, Any] | None = field(default=None, hash=False, compare=False)
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    served_model: str | None = None
    provider_request_id: str | None = None
    response_id: str | None = None
    finish_reasons: tuple[str, ...] = ()
    http_status: int | None = None
    error_class: str | None = None
    retry_after_s: float | None = None
    should_retry: bool | None = None
    rate_limit_headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Detach ``parsed`` from the caller's object and make its top level read-only."""
        if self.parsed is not None:
            frozen = MappingProxyType(copy.deepcopy(dict(self.parsed)))
            object.__setattr__(self, "parsed", frozen)


class LLMPort(Protocol):
    """The port every LLM adapter implements."""

    async def complete(self, req: LLMRequest) -> LLMResult:
        """Run one model call.

        Args:
            req: The request.

        Returns:
            The result; API failures are reported in ``status``, never raised.

        """
        ...
