"""Deterministic in-process LLM for offline tests, with injectable faults (DESIGN.md §11.7).

Each call takes the next entry of the script; once the script is spent, the rule decides. A
``None`` entry, or a rule returning None, means a normal answer for every requested line id.
An E-08 verifier request, told apart by its schema, is answered with verifier objects: the
canned ``verifier_answers``, else the first candidate code the request lists. The id-level and
schema faults act on either kind of answer object.
"""

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from oris_matcher.domain.decision import VERIFIER_NONE
from oris_matcher.llm.base import (
    LLMRequest,
    LLMResult,
    LLMStatus,
    Usage,
    require_allowed_model,
)
from oris_matcher.prompts.v1.verifier import is_verifier_request, payload_codes

FAKE_LATENCY_MS = 100
FAKE_TIMEOUT_LATENCY_MS = 30_000
FAKE_CHARS_PER_TOKEN = 4
HTTP_OK = 200
HTTP_RATE_LIMITED = 429
HTTP_OVERLOADED = 529
HTTP_SERVER_ERROR = 500
DEFAULT_TOP1 = "T01.U01.S01"
DEFAULT_CONFIDENCE = 90
CONFLICT_CONFIDENCE_DELTA = 1
CONFLICT_EVIDENCE_SUFFIX = " (again)"
INVALID_VERIFIER_CODE = 0
CONFIDENCE_KEY = "confidence"
EVIDENCE_KEY = "evidence"
CODE_KEY = "code"
STOP_END_TURN = "end_turn"
STOP_MAX_TOKENS = "max_tokens"
STOP_REFUSAL = "refusal"
MALFORMED_TEXT = '{"lines": [{"id": "'
TRUNCATION_DIVISOR = 2


class FaultKind(StrEnum):
    """Faults the fake can inject into one call."""

    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"
    SERVER_ERROR = "server_error"
    MALFORMED = "malformed"
    INVALID_SCHEMA = "invalid_schema"
    TRUNCATED = "truncated"
    REFUSAL = "refusal"
    DROP_IDS = "drop_ids"
    DUPLICATE_IDS = "duplicate_ids"
    CONFLICTING_DUPLICATES = "conflicting_duplicates"
    UNKNOWN_IDS = "unknown_ids"
    SWAP_IDS = "swap_ids"


@dataclass(frozen=True)
class Fault:
    """One injected fault.

    Attributes:
        kind: What goes wrong.
        retry_after_s: Server-suggested wait, for throttling faults.
        http_status: Status for ``SERVER_ERROR`` (default 500).
        ids: Line ids a fault acts on (drop, duplicate, unknown).
        should_retry: The ``x-should-retry`` hint to report.

    """

    kind: FaultKind
    retry_after_s: float | None = None
    http_status: int | None = None
    ids: tuple[str, ...] = ()
    should_retry: bool | None = None


FaultRule = Callable[[int, LLMRequest], Fault | None]


@dataclass(frozen=True)
class FakeBehaviour:
    """What the fake answers and which faults it injects.

    Attributes:
        answers: Canned answer objects by line id; other ids get ``default_answer``.
        script: One entry per call, in call order.
        rule: Fault chooser by 1-based call number, used once the script is spent.
        usage: Fixed usage to report; by default it is derived from text lengths.
        verifier_answers: Canned verifier objects by line id, for verifier requests; other ids
            get ``default_verifier_answer``.

    """

    answers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    script: Sequence[Fault | None] = ()
    rule: FaultRule | None = None
    usage: Usage | None = None
    verifier_answers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


def default_answer(line_id: str) -> dict[str, Any]:
    """Return a schema-valid answer object for one line, in the pre-registered field order.

    Args:
        line_id: The line id.

    Returns:
        The answer object.

    """
    return {
        "id": line_id,
        "evidence": "",
        "element_or_application": "",
        "material_family": "",
        "kind": "material",
        "nm_category": "",
        "top1": DEFAULT_TOP1,
        "top2": "",
        "confidence": DEFAULT_CONFIDENCE,
        "self_reported_candidate_gap": "clear",
    }


def default_verifier_answer(line_id: str, req: LLMRequest) -> dict[str, Any]:
    """Return a schema-valid verifier object: the first candidate code the request lists.

    Args:
        line_id: The line id.
        req: The verifier request.

    Returns:
        ``{id, evidence, code}``, with ``NONE`` when the request lists no candidate.

    """
    codes = payload_codes(req.user_payload)
    return {"id": line_id, "evidence": "", "code": codes[0] if codes else VERIFIER_NONE}


class FakeLLM:
    """A scriptable, deterministic adapter behind the LLM port."""

    def __init__(
        self, model: str, allowlist: Iterable[str], behaviour: FakeBehaviour | None = None
    ) -> None:
        """Build the fake, refusing a model outside the allowlist.

        Args:
            model: The model id the fake serves.
            allowlist: Patterns from ``config/models.toml``.
            behaviour: Answers and faults; defaults to normal answers only.

        Raises:
            ModelNotAllowedError: The model is not on the allowlist.

        """
        self._allowlist = tuple(allowlist)
        require_allowed_model(model, self._allowlist)
        self.model = model
        self.behaviour = behaviour or FakeBehaviour()
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest) -> LLMResult:
        """Answer one request, applying the scheduled fault.

        Args:
            req: The request.

        Returns:
            The result; faults are reported in ``status``.

        Raises:
            ModelNotAllowedError: The request names a model outside the allowlist.

        """
        require_allowed_model(req.model, self._allowlist)
        self.calls.append(req)
        fault = self._next_fault(len(self.calls), req)
        if fault is None:
            return self._content(req, self._lines(req, req.line_ids), STOP_END_TURN)
        return self._apply(req, fault)

    def _next_fault(self, call_no: int, req: LLMRequest) -> Fault | None:
        """Pick the fault for a call: the script first, then the rule."""
        script = self.behaviour.script
        if call_no <= len(script):
            return script[call_no - 1]
        rule = self.behaviour.rule
        return rule(call_no, req) if rule is not None else None

    def _answer(self, req: LLMRequest, line_id: str) -> dict[str, Any]:
        """Return the canned or default answer object for one line, of the request's kind."""
        if is_verifier_request(req):
            verdict = self.behaviour.verifier_answers.get(line_id)
            return dict(verdict) if verdict is not None else default_verifier_answer(line_id, req)
        canned = self.behaviour.answers.get(line_id)
        return dict(canned) if canned is not None else default_answer(line_id)

    def _lines(self, req: LLMRequest, line_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Return the answer objects for the given ids, in order."""
        return [self._answer(req, line_id) for line_id in line_ids]

    def _apply(self, req: LLMRequest, fault: Fault) -> LLMResult:
        """Build the faulty result for one call."""
        transport = self._transport_fault(req, fault)
        if transport is not None:
            return transport
        if fault.kind == FaultKind.MALFORMED:
            return self._text(req, MALFORMED_TEXT, STOP_END_TURN, LLMStatus.MALFORMED)
        if fault.kind == FaultKind.TRUNCATED:
            full = _batch_text(self._lines(req, req.line_ids))
            cut = full[: len(full) // TRUNCATION_DIVISOR]
            return self._text(req, cut, STOP_MAX_TOKENS, LLMStatus.TRUNCATED)
        if fault.kind == FaultKind.REFUSAL:
            return self._text(req, "", STOP_REFUSAL, LLMStatus.REFUSAL)
        return self._content(req, self._faulty_lines(req, fault), STOP_END_TURN)

    def _transport_fault(self, req: LLMRequest, fault: Fault) -> LLMResult | None:
        """Build the result of a fault where no content arrives, or None for content faults."""
        statuses = {
            FaultKind.TIMEOUT: (LLMStatus.TIMEOUT, None, "APITimeoutError"),
            FaultKind.CONNECTION_ERROR: (LLMStatus.API_ERROR, None, "APIConnectionError"),
            FaultKind.RATE_LIMITED: (LLMStatus.RATE_LIMITED, HTTP_RATE_LIMITED, "RateLimitError"),
            FaultKind.OVERLOADED: (LLMStatus.OVERLOADED, HTTP_OVERLOADED, "OverloadedError"),
            FaultKind.SERVER_ERROR: (
                LLMStatus.API_ERROR,
                fault.http_status or HTTP_SERVER_ERROR,
                "APIStatusError",
            ),
        }
        if fault.kind not in statuses:
            return None
        status, http_status, error_class = statuses[fault.kind]
        timed_out = fault.kind == FaultKind.TIMEOUT
        return LLMResult(
            status=status,
            raw_text="" if http_status is None else json.dumps({"error": error_class}),
            latency_ms=FAKE_TIMEOUT_LATENCY_MS if timed_out else FAKE_LATENCY_MS,
            http_status=http_status,
            error_class=error_class,
            retry_after_s=fault.retry_after_s,
            should_retry=fault.should_retry,
            provider_request_id=f"fake-req-{len(self.calls)}",
        )

    def _faulty_lines(self, req: LLMRequest, fault: Fault) -> list[dict[str, Any]]:
        """Build the answer objects for an id-level or schema fault."""
        ids = list(req.line_ids)
        if fault.kind == FaultKind.DROP_IDS:
            return self._lines(req, (i for i in ids if i not in fault.ids))
        if fault.kind == FaultKind.UNKNOWN_IDS:
            return self._lines(req, ids) + self._lines(req, fault.ids)
        if fault.kind == FaultKind.SWAP_IDS:
            return _swapped(self._lines(req, ids))
        if fault.kind == FaultKind.INVALID_SCHEMA:
            return [_schema_broken(line) for line in self._lines(req, ids)]
        conflicting = fault.kind == FaultKind.CONFLICTING_DUPLICATES
        return _with_duplicates(self._lines(req, ids), fault.ids, conflicting=conflicting)

    def _content(self, req: LLMRequest, lines: list[dict[str, Any]], stop: str) -> LLMResult:
        """Build a delivered, parseable response."""
        return self._text(req, _batch_text(lines), stop, LLMStatus.OK)

    def _text(self, req: LLMRequest, text: str, stop: str, status: LLMStatus) -> LLMResult:
        """Build a delivered response with the given text, stop reason and status."""
        call_no = len(self.calls)
        parsed = json.loads(text) if status == LLMStatus.OK else None
        return LLMResult(
            status=status,
            raw_text=text,
            parsed=parsed,
            usage=self.behaviour.usage or _usage(req, text),
            latency_ms=FAKE_LATENCY_MS,
            served_model=req.model,
            provider_request_id=f"fake-req-{call_no}",
            response_id=f"fake-msg-{call_no}",
            finish_reasons=(stop,),
            http_status=HTTP_OK,
        )


def _batch_text(lines: list[dict[str, Any]]) -> str:
    """Serialise answer objects as a batch response."""
    return json.dumps({"lines": lines}, ensure_ascii=False)


def _usage(req: LLMRequest, text: str) -> Usage:
    """Derive deterministic token counts from text lengths."""
    return Usage(
        input_tokens=len(req.user_payload) // FAKE_CHARS_PER_TOKEN,
        output_tokens=len(text) // FAKE_CHARS_PER_TOKEN,
    )


def _swapped(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rotate the ids by one, so each answer body carries a neighbour's id."""
    ids = [line["id"] for line in lines]
    rotated = ids[1:] + ids[:1]
    return [{**line, "id": new_id} for line, new_id in zip(lines, rotated, strict=True)]


def _conflicting(line: dict[str, Any]) -> dict[str, Any]:
    """Return another answer object for the same id.

    A main answer gets a lower confidence; a verifier answer, which has no confidence, gets a
    longer evidence span.
    """
    if CONFIDENCE_KEY in line:
        return {**line, CONFIDENCE_KEY: line[CONFIDENCE_KEY] - CONFLICT_CONFIDENCE_DELTA}
    return {**line, EVIDENCE_KEY: line[EVIDENCE_KEY] + CONFLICT_EVIDENCE_SUFFIX}


def _schema_broken(line: dict[str, Any]) -> dict[str, Any]:
    """Break a field the answer's schema has: confidence as text, or a verifier code as a number."""
    if CONFIDENCE_KEY in line:
        return {**line, CONFIDENCE_KEY: str(line[CONFIDENCE_KEY])}
    return {**line, CODE_KEY: INVALID_VERIFIER_CODE}


def _with_duplicates(
    lines: list[dict[str, Any]], ids: tuple[str, ...], *, conflicting: bool
) -> list[dict[str, Any]]:
    """Repeat the answers of the given ids, identical or conflicting (``_conflicting``)."""
    result: list[dict[str, Any]] = []
    for line in lines:
        result.append(line)
        if line["id"] not in ids:
            continue
        result.append(_conflicting(line) if conflicting else dict(line))
    return result
