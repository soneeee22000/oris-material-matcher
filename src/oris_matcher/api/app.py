"""FastAPI application factory and routes (DESIGN.md §11.4, §11.3 API rows, A13, A50, A51).

``POST /v1/match`` decides a JSON list of lines against a configured library through
``MatchService``, the same orchestrator the CLI uses. Request models forbid extra fields and
cap item_no, short and long descriptions at the reader's prompt caps (422); more than 500
lines is a 413. Section paths come from the request when given, else from header rows in the
request through the reader's ``classify_rows`` and ``derive_section_paths``. Model failures
never fail the request: it is always a 200 with every line present and a per-line reason.
When ``ORIS_API_TOKEN`` is set, ``/v1/*`` needs that Bearer token; ``/health`` and ``/ready``
stay open. Every response carries ``X-Request-ID``, the run id for a match.
"""

import hmac
import secrets
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from oris_matcher import __version__
from oris_matcher.doctor import (
    LLMKind,
    Runtime,
    WiringError,
    WrapperSetup,
    live_adapter,
    make_wrapper,
)
from oris_matcher.domain.boq import (
    BoqFile,
    BoqLine,
    Column,
    LineKind,
    PathMode,
    SectionHeader,
    make_line_id,
)
from oris_matcher.domain.decision import LLM_FAILURE_PREFIX, Decision, ReasonCode
from oris_matcher.domain.library import LibraryRow
from oris_matcher.io.boq_reader import (
    COLUMN_ALIASES,
    COMMA,
    FIELD_CAPS,
    UTF8,
    RowCells,
    classify_rows,
    derive_section_paths,
)
from oris_matcher.llm.base import LLMPort
from oris_matcher.service import (
    LineResult,
    MatchService,
    RunOptions,
    RunProfile,
    RunResult,
    budget_cap_usd,
    make_run_id,
    provider_for,
)
from oris_matcher.settings import (
    MODELS_FILE,
    PRICING_FILE,
    ConfigError,
    PricingTable,
    Settings,
    load_models_config,
    load_pricing,
    resolve_models,
)

API_PREFIX = "/v1"
HARD_LINE_LIMIT = 500
SOFT_LINE_LIMIT = 100
REQUEST_ID_HEADER = "X-Request-ID"
BEARER_PREFIX = "Bearer "
TOKEN_ENCODING = "utf-8"
REQUEST_NONCE_BYTES = 8
HTTP_UNAUTHORIZED = 401
HTTP_PAYLOAD_TOO_LARGE = 413
HTTP_UNPROCESSABLE = 422
HTTP_UNAVAILABLE = 503
HTTP_OK = 200
INPUT_HEADER: tuple[str, ...] = tuple(COLUMN_ALIASES[column][0] for column in Column)
FAILURE_REASONS = frozenset({ReasonCode.LLM_UNAVAILABLE.value, ReasonCode.BUDGET_CAP.value})

LLMFactory = Callable[[str], LLMPort]
Clock = Callable[[], datetime]


def _raw_cell(value: object) -> object:
    """Keep a JSON number's text as the raw cell string; leave everything else to validation."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return value


RawCell = Annotated[str, BeforeValidator(_raw_cell)]


class LineIn(BaseModel):
    """One request line; every cell is kept as its raw string."""

    model_config = ConfigDict(extra="forbid")

    item_no: str = Field(default="", max_length=FIELD_CAPS[Column.ITEM_NO])
    short_description: str = Field(default="", max_length=FIELD_CAPS[Column.SHORT])
    long_description: str = Field(default="", max_length=FIELD_CAPS[Column.LONG])
    unit: RawCell = ""
    qty: RawCell = ""
    section_path: list[str] | None = None


class MatchRequest(BaseModel):
    """``POST /v1/match`` body; at most 500 lines (soft limit 100)."""

    model_config = ConfigDict(extra="forbid")

    library: str
    lines: list[LineIn] = Field(min_length=1)


def _cells(line: LineIn) -> RowCells:
    """Return the canonical cells of a request line."""
    values = (line.item_no, line.short_description, line.long_description, line.unit, line.qty)
    return RowCells(*values, all_blank=not any(values))


def _boq_line(
    position: int, cells: RowCells, kind: LineKind, path: tuple[SectionHeader, ...]
) -> BoqLine:
    """Build one line exactly as the CSV reader would from the same five cells."""
    raw_row = (cells.item_no, cells.short, cells.long, cells.unit, cells.qty)
    return BoqLine(
        position=position,
        line_id=make_line_id(position, cells.item_no, cells.short, cells.long),
        item_no=cells.item_no,
        short=cells.short,
        long=cells.long,
        unit=cells.unit,
        qty=cells.qty,
        kind=kind,
        section_path=path,
        extra=(),
        raw_row=raw_row,
    )


def request_boq(lines: Sequence[LineIn]) -> BoqFile:
    """Turn request lines into a parsed BoQ, with headers and paths decided over all of them.

    Args:
        lines: The request lines, in order.

    Returns:
        The BoQ; a line's given ``section_path`` replaces the derived one.

    """
    cells = [_cells(line) for line in lines]
    kinds = classify_rows(cells)
    derived = derive_section_paths(cells, kinds)
    paths = [
        derived[index]
        if line.section_path is None
        else tuple(SectionHeader("", text) for text in line.section_path)
        for index, line in enumerate(lines)
    ]
    boq_lines = tuple(
        _boq_line(position, cell, kind, path)
        for position, (cell, kind, path) in enumerate(zip(cells, kinds, paths, strict=True))
    )
    return BoqFile(
        lines=boq_lines,
        header=INPUT_HEADER,
        encoding=UTF8,
        delimiter=COMMA,
        path_mode=PathMode.DERIVED if any(paths) else PathMode.NONE,
        column_map=tuple(zip(Column, INPUT_HEADER, strict=True)),
    )


def _suggestion(row: LibraryRow | None) -> dict[str, str] | None:
    """Return a suggested row's verbatim labels and id, or None."""
    if row is None:
        return None
    return {
        "material_type": row.material_type,
        "material_usage": row.material_usage,
        "material_subtype": row.material_subtype,
        "library_row_id": row.row_id,
    }


def decision_record(item: LineResult) -> dict[str, Any]:
    """Return one line's API decision.

    Args:
        item: One line's result.

    Returns:
        The decision; the labels are verbatim library strings on matched lines, else null.

    """
    decision = item.decision
    row = decision.row if decision.decision == Decision.MATCHED else None
    return {
        "line_index": item.line.position,
        "item_no": item.line.item_no,
        "decision": decision.decision.value,
        "material_type": row.material_type if row else None,
        "material_usage": row.material_usage if row else None,
        "material_subtype": row.material_subtype if row else None,
        "reason": decision.reason,
        "suggestions": [_suggestion(item.suggested), _suggestion(item.suggested2)],
        "model": item.model,
        "prompt_version": item.prompt_version,
        "cost_usd": item.cost_usd,
        "latency_ms": item.latency_ms,
        "call_ids": list(item.call_ids),
    }


def _is_failure(reason: str) -> bool:
    """Tell whether a line's reason is a model failure."""
    return reason.startswith(LLM_FAILURE_PREFIX) or reason in FAILURE_REASONS


def match_response(result: RunResult) -> dict[str, Any]:
    """Return the ``POST /v1/match`` response body.

    Args:
        result: The run.

    Returns:
        The run id, versions, one decision per line in input order, and the summary.

    """
    lines = result.lines
    latencies = [item.latency_ms for item in lines]
    return {
        "run_id": result.run_id,
        "versions": {
            "model": result.manifest["requested_model"],
            "prompt": list(result.prompt_versions),
            "library_sha256": result.library.sha256,
            "policy": result.policy.policy_id,
            "policy_resolution": result.policy.resolution.value,
        },
        "decisions": [decision_record(item) for item in lines],
        "summary": {
            "counts": dict(Counter(item.decision.decision.value for item in lines)),
            "cost_usd": result.attributed_cost_usd,
            "mean_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "failures": sum(_is_failure(item.decision.reason) for item in lines),
        },
    }


def _invalid(field: str, message: str) -> HTTPException:
    """Return a 422 in FastAPI's validation-error envelope."""
    detail = [{"type": "value_error", "loc": ["body", field], "msg": message}]
    return HTTPException(status_code=HTTP_UNPROCESSABLE, detail=detail)


def request_id(clock: Clock) -> str:
    """Return a fresh request id in the run-id format.

    Args:
        clock: UTC clock.

    Returns:
        The id.

    """
    return make_run_id(clock(), secrets.token_hex(REQUEST_NONCE_BYTES))


@dataclass
class ApiState:
    """What the routes share: settings, the service, prices and the adapter factory."""

    settings: Settings
    service: MatchService
    factory: LLMFactory
    key_configured: Callable[[], bool]
    runtime: Runtime

    def authorize(self, authorization: Annotated[str | None, Header()] = None) -> None:
        """Require the Bearer token on ``/v1/*`` when ``ORIS_API_TOKEN`` is set.

        Args:
            authorization: The ``Authorization`` header.

        Raises:
            HTTPException: 401 for a missing or wrong token.

        """
        token = self.settings.api_token
        if token is None:
            return
        expected = (BEARER_PREFIX + token.get_secret_value()).encode(TOKEN_ENCODING)
        given = (authorization or "").encode(TOKEN_ENCODING)
        if not hmac.compare_digest(given, expected):
            raise HTTPException(
                status_code=HTTP_UNAUTHORIZED,
                detail="missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def _adapter(self) -> LLMPort:
        """Build the run's adapter, or fail with 503 when no key is configured."""
        try:
            return self.factory(self.service.resources.requested_model)
        except WiringError as error:
            raise HTTPException(status_code=HTTP_UNAVAILABLE, detail=str(error)) from error

    def _check_limits(self, body: MatchRequest) -> None:
        """Refuse an unknown library (422) or more than the hard line limit (413)."""
        if body.library not in self.settings.libraries:
            known = ", ".join(sorted(self.settings.libraries))
            raise _invalid("library", f"unknown library {body.library!r}; use one of: {known}")
        if len(body.lines) > HARD_LINE_LIMIT:
            raise HTTPException(
                status_code=HTTP_PAYLOAD_TOO_LARGE,
                detail=f"{len(body.lines)} lines; the hard limit is {HARD_LINE_LIMIT}",
            )

    async def match(self, body: MatchRequest) -> JSONResponse:
        """Decide every request line; always a 200 when the model fails.

        Args:
            body: The request.

        Returns:
            The response, with ``X-Request-ID`` set to the run id.

        """
        self._check_limits(body)
        boq = request_boq(body.lines)
        run_id = request_id(self.runtime.clock)
        settings = self.settings
        pricing = load_pricing(settings.config_file(PRICING_FILE))
        setup = WrapperSetup(budget_cap_usd(settings, len(boq.lines)), run_id=run_id)
        wrapper = make_wrapper(self._adapter(), pricing, settings, setup, self.runtime)
        options = RunOptions(run_id=run_id, fallback=self._fallback(pricing))
        result = await self.service.match(
            boq, body.library, profile=RunProfile.B3, llm=wrapper, options=options
        )
        return JSONResponse(match_response(result), headers={REQUEST_ID_HEADER: run_id})

    def _fallback(self, pricing: PricingTable) -> LLMPort | None:
        """Build the fallback adapter when its provider's key is set, else None (§11.3, A33)."""
        resources = self.service.resources
        model = resources.fallback_model
        if model == resources.requested_model:
            return None
        if LLMKind(provider_for(model, pricing)) != LLMKind.OPENAI:
            return None
        if self.settings.openai_api_key is None:
            return None
        try:
            return self.factory(model)
        except WiringError:
            return None

    def _loaded(self, library_id: str) -> bool:
        """Tell whether a configured library loads."""
        try:
            self.service.library(library_id)
        except (OSError, ValueError, KeyError, ConfigError):
            return False
        return True

    def ready(self) -> JSONResponse:
        """Report readiness: every library loaded and a key configured; no model call.

        Returns:
            200 when ready, else 503, with the details.

        """
        libraries = {library_id: self._loaded(library_id) for library_id in self.settings.libraries}
        key = self.key_configured()
        ready = all(libraries.values()) and key
        body = {"ready": ready, "libraries": libraries, "key_configured": key}
        return JSONResponse(body, status_code=HTTP_OK if ready else HTTP_UNAVAILABLE)


def _default_factory(settings: Settings) -> tuple[LLMFactory, Callable[[], bool]]:
    """Return the live adapter factory for the requested model, and its key check."""
    models_config = load_models_config(settings.config_file(MODELS_FILE))
    pricing = load_pricing(settings.config_file(PRICING_FILE))
    primary_kind = LLMKind(provider_for(resolve_models(settings, models_config).primary, pricing))

    def factory(model: str) -> LLMPort:
        """Construct the live adapter, raising WiringError when its key is missing."""
        kind = LLMKind(provider_for(model, pricing))
        allowlist = models_config.allowlist.patterns
        return live_adapter(kind, model, settings, allowlist, Runtime())

    def key_configured() -> bool:
        """Tell whether the requested model's provider key is set."""
        if primary_kind == LLMKind.ANTHROPIC:
            return settings.anthropic_api_key is not None
        return settings.openai_api_key is not None

    return factory, key_configured


def _request_id_middleware(
    clock: Clock,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Return the middleware that gives every response an ``X-Request-ID``."""

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Add a fresh request id to a response that carries none."""
        response = await call_next(request)
        if REQUEST_ID_HEADER not in response.headers:
            response.headers[REQUEST_ID_HEADER] = request_id(clock)
        return response

    return middleware


def _routes(app: FastAPI, state: ApiState) -> None:
    """Register the probe routes and the authorised ``/v1`` router."""

    async def health() -> dict[str, str]:
        """Liveness."""
        return {"status": "ok"}

    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/ready", state.ready, methods=["GET"])
    router = APIRouter(prefix=API_PREFIX, dependencies=[Depends(state.authorize)])
    router.add_api_route("/match", state.match, methods=["POST"])
    app.include_router(router)


def create_app(
    settings: Settings | None = None,
    llm_factory: LLMFactory | None = None,
    *,
    runtime: Runtime | None = None,
) -> FastAPI:
    """Build the API.

    Args:
        settings: The effective settings; read from the environment when None.
        llm_factory: Builds the adapter for a model; tests inject FakeLLM. When None, the live
            adapter of the model's provider is built per request, and a missing key is a 503.
        runtime: Clock and retry sleep; the defaults suit a live server.

    Returns:
        The application.

    Raises:
        ConfigError: A config file is invalid, at startup.

    """
    effective = settings or Settings()
    if llm_factory is None:
        factory, key_configured = _default_factory(effective)
    else:
        factory, key_configured = llm_factory, lambda: True
    service = MatchService.from_settings(effective)
    environment = runtime or Runtime()
    state = ApiState(effective, service, factory, key_configured, environment)
    app = FastAPI(title="ORIS material matcher", version=__version__)
    app.middleware("http")(_request_id_middleware(environment.clock))
    _routes(app, state)
    return app
