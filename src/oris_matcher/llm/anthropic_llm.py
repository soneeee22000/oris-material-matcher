"""Anthropic Messages adapter behind the LLM port; never raises on API failure (DESIGN.md §11.9).

Calls ``messages.create`` (never ``parse``) with SDK retries off, cache breakpoints on the
cache-flagged system blocks, and the schema through native structured outputs
(``output_config.format``). SDK 1.11 exposes no ``temperature`` keyword, so it travels in
``extra_body``.
"""

from collections.abc import Iterable

import anthropic
import httpx2
from anthropic.types import (
    CacheControlEphemeralParam,
    Message,
    MessageParam,
    OutputConfigParam,
    TextBlock,
    TextBlockParam,
)

from oris_matcher.llm.base import (
    ANTHROPIC_RATE_LIMIT_PREFIX,
    AdapterOptions,
    LLMRequest,
    LLMResult,
    LLMStatus,
    Usage,
    classify_content,
    parse_retry_after,
    parse_should_retry,
    require_allowed_model,
    select_rate_limit_headers,
)

SDK_MAX_RETRIES = 0
REQUEST_ID_HEADER = "request-id"
HTTP_RATE_LIMITED = 429
HTTP_OVERLOADED = 529
MILLISECONDS_PER_SECOND = 1000
STATUS_BY_HTTP = {HTTP_RATE_LIMITED: LLMStatus.RATE_LIMITED, HTTP_OVERLOADED: LLMStatus.OVERLOADED}


def system_param(req: LLMRequest) -> list[TextBlockParam]:
    """Render the system blocks, with an ephemeral cache breakpoint on each flagged block.

    Args:
        req: The request.

    Returns:
        The ``system`` parameter.

    """
    blocks: list[TextBlockParam] = []
    for block in req.system_blocks:
        param: TextBlockParam = {"type": "text", "text": block.text}
        if block.cache:
            param["cache_control"] = CacheControlEphemeralParam(type="ephemeral")
        blocks.append(param)
    return blocks


def _message_text(message: Message) -> str:
    """Concatenate the text blocks of a message."""
    return "".join(block.text for block in message.content if isinstance(block, TextBlock))


def _usage(message: Message) -> Usage:
    """Map the SDK usage, including cache reads and writes."""
    usage = message.usage
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read=usage.cache_read_input_tokens or 0,
        cache_write=usage.cache_creation_input_tokens or 0,
    )


class AnthropicLLM:
    """The primary adapter: Anthropic Messages through ``AsyncAnthropic``."""

    def __init__(
        self,
        model: str,
        allowlist: Iterable[str],
        api_key: str,
        options: AdapterOptions | None = None,
    ) -> None:
        """Build the adapter, refusing a model outside the allowlist.

        Args:
            model: The configured model snapshot id.
            allowlist: Patterns from ``config/models.toml``.
            api_key: The Anthropic API key.
            options: Timeout, injected HTTP client and clock.

        Raises:
            ModelNotAllowedError: The model is not on the allowlist.

        """
        self._allowlist = tuple(allowlist)
        require_allowed_model(model, self._allowlist)
        self.model = model
        self._options = options or AdapterOptions()
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            max_retries=SDK_MAX_RETRIES,
            timeout=self._options.timeout_s,
            http_client=self._options.http_client,
        )

    async def complete(self, req: LLMRequest) -> LLMResult:
        """Run one Messages call; API failures are reported in ``status``.

        Args:
            req: The request.

        Returns:
            The result, always with the raw text.

        Raises:
            ModelNotAllowedError: The request names a model outside the allowlist.

        """
        require_allowed_model(req.model, self._allowlist)
        started = self._options.clock()
        try:
            response = await self._send(req)
        except anthropic.APIStatusError as error:
            return self._status_failure(error, started)
        except anthropic.APITimeoutError as error:
            return self._failure(LLMStatus.TIMEOUT, error, started)
        except anthropic.AnthropicError as error:
            return self._failure(LLMStatus.API_ERROR, error, started)
        return self._delivered(response, started)

    async def _send(self, req: LLMRequest) -> httpx2.Response:
        """Send the Messages request with native structured output; SDK errors propagate."""
        raw = await self._client.messages.with_raw_response.create(
            model=req.model,
            max_tokens=req.max_tokens,
            system=system_param(req),
            messages=[MessageParam(role="user", content=req.user_payload)],
            output_config=OutputConfigParam(format={"type": "json_schema", "schema": req.schema()}),
            extra_body={"temperature": req.temperature},
        )
        return raw.http_response

    def _elapsed_ms(self, started: float) -> int:
        """Return the milliseconds since ``started``."""
        return round((self._options.clock() - started) * MILLISECONDS_PER_SECOND)

    def _delivered(self, response: httpx2.Response, started: float) -> LLMResult:
        """Map a delivered HTTP response, keeping a body that is not a message as malformed."""
        headers = response.headers
        try:
            message = Message.model_validate_json(response.text)
        except ValueError as error:
            return LLMResult(
                status=LLMStatus.MALFORMED,
                raw_text=response.text,
                latency_ms=self._elapsed_ms(started),
                provider_request_id=headers.get(REQUEST_ID_HEADER),
                http_status=response.status_code,
                error_class=type(error).__name__,
                rate_limit_headers=select_rate_limit_headers(headers, ANTHROPIC_RATE_LIMIT_PREFIX),
            )
        return self._from_message(message, headers, response.status_code, started)

    def _from_message(
        self, message: Message, headers: httpx2.Headers, http_status: int, started: float
    ) -> LLMResult:
        """Map a parsed message, classifying it by stop reason and then JSON."""
        text = _message_text(message)
        finish = (message.stop_reason,) if message.stop_reason else ()
        status, parsed = classify_content(finish, text)
        return LLMResult(
            status=status,
            raw_text=text,
            parsed=parsed,
            usage=_usage(message),
            latency_ms=self._elapsed_ms(started),
            served_model=message.model,
            provider_request_id=headers.get(REQUEST_ID_HEADER),
            response_id=message.id,
            finish_reasons=finish,
            http_status=http_status,
            should_retry=parse_should_retry(headers),
            rate_limit_headers=select_rate_limit_headers(headers, ANTHROPIC_RATE_LIMIT_PREFIX),
        )

    def _status_failure(self, error: anthropic.APIStatusError, started: float) -> LLMResult:
        """Map an HTTP error status, with its retry hints and rate-limit headers."""
        headers = error.response.headers
        return LLMResult(
            status=STATUS_BY_HTTP.get(error.status_code, LLMStatus.API_ERROR),
            raw_text=error.response.text,
            latency_ms=self._elapsed_ms(started),
            provider_request_id=headers.get(REQUEST_ID_HEADER),
            http_status=error.status_code,
            error_class=type(error).__name__,
            retry_after_s=parse_retry_after(headers),
            should_retry=parse_should_retry(headers),
            rate_limit_headers=select_rate_limit_headers(headers, ANTHROPIC_RATE_LIMIT_PREFIX),
        )

    def _failure(
        self, status: LLMStatus, error: anthropic.AnthropicError, started: float
    ) -> LLMResult:
        """Map a failure where no HTTP response arrived."""
        return LLMResult(
            status=status,
            raw_text=str(error),
            latency_ms=self._elapsed_ms(started),
            error_class=type(error).__name__,
        )
