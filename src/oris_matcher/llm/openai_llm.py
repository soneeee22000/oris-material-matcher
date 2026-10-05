"""OpenAI Chat Completions fallback adapter behind the LLM port (DESIGN.md §11.3).

Sends the same Pydantic-generated schema as a strict ``json_schema`` response format, with SDK
retries off, and never raises for API failures. OpenAI reports a refusal in the message rather
than in ``finish_reason``, so a refusal adds ``refusal`` to the recorded finish reasons.
"""

from collections.abc import Iterable

import httpx2
import openai
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.shared_params import ResponseFormatJSONSchema

from oris_matcher.llm.base import (
    OPENAI_RATE_LIMIT_PREFIX,
    REFUSAL_FINISH_REASON,
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
REQUEST_ID_HEADER = "x-request-id"
SCHEMA_NAME = "batch_answer"
HTTP_RATE_LIMITED = 429
HTTP_OVERLOADED = 529
MILLISECONDS_PER_SECOND = 1000
STATUS_BY_HTTP = {HTTP_RATE_LIMITED: LLMStatus.RATE_LIMITED, HTTP_OVERLOADED: LLMStatus.OVERLOADED}


def chat_messages(req: LLMRequest) -> list[ChatCompletionMessageParam]:
    """Render the system blocks as system messages, in order, then the user message.

    OpenAI caches long prompt prefixes automatically, so the cache flags need no marker.

    Args:
        req: The request.

    Returns:
        The ``messages`` parameter.

    """
    messages: list[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(role="system", content=block.text)
        for block in req.system_blocks
    ]
    messages.append(ChatCompletionUserMessageParam(role="user", content=req.user_payload))
    return messages


def response_format(req: LLMRequest) -> ResponseFormatJSONSchema:
    """Build the strict ``json_schema`` response format from the request's schema.

    Args:
        req: The request.

    Returns:
        The ``response_format`` parameter.

    """
    return {
        "type": "json_schema",
        "json_schema": {"name": SCHEMA_NAME, "schema": req.schema(), "strict": True},
    }


def _usage(completion: ChatCompletion) -> Usage:
    """Map usage; cached prompt tokens are reported apart from uncached input."""
    usage = completion.usage
    if usage is None:
        return Usage()
    details = usage.prompt_tokens_details
    cached = (details.cached_tokens or 0) if details is not None else 0
    return Usage(
        input_tokens=usage.prompt_tokens - cached,
        output_tokens=usage.completion_tokens,
        cache_read=cached,
    )


def _text_and_reasons(completion: ChatCompletion) -> tuple[str, tuple[str, ...]]:
    """Return the message text, or the refusal text, and the finish reasons."""
    if not completion.choices:
        return "", ()
    choice = completion.choices[0]
    if choice.message.refusal:
        return choice.message.refusal, (choice.finish_reason, REFUSAL_FINISH_REASON)
    return choice.message.content or "", (choice.finish_reason,)


class OpenAIChatLLM:
    """The fallback adapter: OpenAI Chat Completions through ``AsyncOpenAI``."""

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
            api_key: The OpenAI API key.
            options: Timeout, injected HTTP client and clock.

        Raises:
            ModelNotAllowedError: The model is not on the allowlist.

        """
        self._allowlist = tuple(allowlist)
        require_allowed_model(model, self._allowlist)
        self.model = model
        self._options = options or AdapterOptions()
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            max_retries=SDK_MAX_RETRIES,
            timeout=self._options.timeout_s,
            http_client=self._options.http_client,
        )

    async def complete(self, req: LLMRequest) -> LLMResult:
        """Run one Chat Completions call; API failures are reported in ``status``.

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
            raw = await self._client.chat.completions.with_raw_response.create(
                model=req.model,
                messages=chat_messages(req),
                max_completion_tokens=req.max_tokens,
                temperature=req.temperature,
                response_format=response_format(req),
            )
        except openai.APIStatusError as error:
            return self._status_failure(error, started)
        except openai.APITimeoutError as error:
            return self._failure(LLMStatus.TIMEOUT, error, started)
        except openai.OpenAIError as error:
            return self._failure(LLMStatus.API_ERROR, error, started)
        return self._delivered(raw.http_response, started)

    def _elapsed_ms(self, started: float) -> int:
        """Return the milliseconds since ``started``."""
        return round((self._options.clock() - started) * MILLISECONDS_PER_SECOND)

    def _delivered(self, response: httpx2.Response, started: float) -> LLMResult:
        """Map a delivered HTTP response, keeping an unparsable body as malformed."""
        headers = response.headers
        try:
            completion = ChatCompletion.model_validate_json(response.text)
        except ValueError as error:
            return LLMResult(
                status=LLMStatus.MALFORMED,
                raw_text=response.text,
                latency_ms=self._elapsed_ms(started),
                provider_request_id=headers.get(REQUEST_ID_HEADER),
                http_status=response.status_code,
                error_class=type(error).__name__,
            )
        text, finish = _text_and_reasons(completion)
        status, parsed = classify_content(finish, text)
        return LLMResult(
            status=status,
            raw_text=text,
            parsed=parsed,
            usage=_usage(completion),
            latency_ms=self._elapsed_ms(started),
            served_model=completion.model,
            provider_request_id=headers.get(REQUEST_ID_HEADER),
            response_id=completion.id,
            finish_reasons=finish,
            http_status=response.status_code,
            should_retry=parse_should_retry(headers),
            rate_limit_headers=select_rate_limit_headers(headers, OPENAI_RATE_LIMIT_PREFIX),
        )

    def _status_failure(self, error: openai.APIStatusError, started: float) -> LLMResult:
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
            rate_limit_headers=select_rate_limit_headers(headers, OPENAI_RATE_LIMIT_PREFIX),
        )

    def _failure(self, status: LLMStatus, error: openai.OpenAIError, started: float) -> LLMResult:
        """Map a failure where no HTTP response arrived."""
        return LLMResult(
            status=status,
            raw_text=str(error),
            latency_ms=self._elapsed_ms(started),
            error_class=type(error).__name__,
        )
