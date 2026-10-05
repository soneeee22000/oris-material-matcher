"""Decision vocabulary, the frozen reason codes and the decision-table contracts (DESIGN.md §9.5).

``DecisionInput`` is everything the pure D0-D10 table reads for one line; ``LineDecision`` is
what it emits, consumed by the writer (§9.6), ``audit.jsonl`` and the service. Score s and the
thresholds on it follow §10.6.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType

from oris_matcher.domain.attributes import Attributes, AttrResult
from oris_matcher.domain.boq import BoqLine
from oris_matcher.domain.library import LibraryRow
from oris_matcher.prompts.v1.schema import CONFIDENCE_MAX, CONFIDENCE_MIN, LineAnswer

SIGNAL_PREFIX = "SIGNAL:"
LOW_SIGNAL_PREFIX = "LOW_SIGNAL:"
ENSEMBLE_DEGRADED_PREFIX = "ENSEMBLE_DEGRADED:"
LLM_FAILURE_PREFIX = "LLM_FAILURE:"
LOW_SIGNAL_SEPARATOR = "+"
THRESHOLD_ID_RE = re.compile(r"[^\s:]+")
BUCKET_FLOOR_90 = 90
BUCKET_FLOOR_80 = 80
BUCKET_FLOOR_70 = 70


class Decision(StrEnum):
    """The three output decisions."""

    MATCHED = "matched"
    NOT_A_MATERIAL = "not_a_material"
    NEEDS_REVIEW = "needs_review"


class ReasonCode(StrEnum):
    """The fixed members of the frozen reason-code list.

    The parameterised forms are built by ``llm_failure_reason``, ``signal_reason``,
    ``low_signal_reason`` and ``ensemble_degraded``; ``is_frozen_reason`` checks any string.
    """

    HEADER = "HEADER"
    EMPTY_ROW = "EMPTY_ROW"
    HEADER_UNCONFIRMED = "HEADER_UNCONFIRMED"
    G2_SERVICE = "G2_SERVICE"
    NM_UNCONFIRMED = "NM_UNCONFIRMED"
    NO_LIBRARY_EQUIVALENT = "NO_LIBRARY_EQUIVALENT"
    INVALID_ROW_ID = "INVALID_ROW_ID"
    EVIDENCE_NOT_IN_LINE = "EVIDENCE_NOT_IN_LINE"
    NEVER_MATCH_ROW = "NEVER_MATCH_ROW"
    ATTR_CONFLICT = "ATTR_CONFLICT"
    GENERIC_PARENT = "GENERIC_PARENT"
    VERIFIER_DISAGREES = "VERIFIER_DISAGREES"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    BUDGET_CAP = "BUDGET_CAP"
    INTERNAL_INVARIANT = "INTERNAL_INVARIANT"


class LLMFailureKind(StrEnum):
    """Kinds carried by ``LLM_FAILURE:<kind>`` reasons."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"
    API_ERROR = "api_error"
    TRUNCATED = "truncated"
    MALFORMED = "malformed"
    REFUSAL = "refusal"
    MISSING_ITEM = "missing_item"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    PARTIAL_SIGNAL = "partial_signal"
    REPLAY_MISS = "replay_miss"


class SignalName(StrEnum):
    """The components of score s, in its lexicographic order (§10.6)."""

    VOTES = "v"
    ATTRIBUTES = "b"
    CONFIDENCE = "confidence"


class Rule(StrEnum):
    """The rows of the decision table, in firing order."""

    D0 = "D0"
    D0A = "D0a"
    D0B = "D0b"
    D1 = "D1"
    D1B = "D1b"
    D2 = "D2"
    D3 = "D3"
    D4 = "D4"
    D5 = "D5"
    D5A = "D5a"
    D6 = "D6"
    D7 = "D7"
    D8 = "D8"
    D8A = "D8a"
    D9 = "D9"
    D10 = "D10"


class ConfidenceBucket(IntEnum):
    """Self-reported confidence buckets, ordered from weakest to strongest."""

    BELOW_70 = 0
    FROM_70 = 1
    FROM_80 = 2
    FROM_90 = 3


NOT_A_MATERIAL_REASONS: frozenset[ReasonCode] = frozenset(
    {ReasonCode.HEADER, ReasonCode.EMPTY_ROW, ReasonCode.G2_SERVICE}
)
LINE_FAILURE_REASONS: frozenset[ReasonCode] = frozenset(
    {ReasonCode.LLM_UNAVAILABLE, ReasonCode.BUDGET_CAP}
)
RULE_DECISIONS: Mapping[Rule, Decision] = MappingProxyType(
    {
        **dict.fromkeys(Rule, Decision.NEEDS_REVIEW),
        **dict.fromkeys((Rule.D0, Rule.D0A, Rule.D2), Decision.NOT_A_MATERIAL),
        Rule.D9: Decision.MATCHED,
    }
)
BUCKET_FLOORS = (
    (BUCKET_FLOOR_90, ConfidenceBucket.FROM_90),
    (BUCKET_FLOOR_80, ConfidenceBucket.FROM_80),
    (BUCKET_FLOOR_70, ConfidenceBucket.FROM_70),
)


def llm_failure_reason(kind: LLMFailureKind) -> str:
    """Build the reason string for a model failure.

    Args:
        kind: The failure kind.

    Returns:
        ``LLM_FAILURE:<kind>``, e.g. ``LLM_FAILURE:replay_miss``.

    """
    return f"{LLM_FAILURE_PREFIX}{kind.value}"


def _check_threshold_id(threshold_id: str) -> None:
    """Raise ValueError unless the id is non-empty, without whitespace or colons."""
    if THRESHOLD_ID_RE.fullmatch(threshold_id) is None:
        raise ValueError(f"invalid threshold id {threshold_id!r}")


def signal_reason(threshold_id: str) -> str:
    """Build the reason string for a line matched at a threshold (rule D9).

    Args:
        threshold_id: The frozen threshold's id, e.g. ``T3``.

    Returns:
        ``SIGNAL:<threshold id>``.

    Raises:
        ValueError: The id is empty or contains whitespace or a colon.

    """
    _check_threshold_id(threshold_id)
    return f"{SIGNAL_PREFIX}{threshold_id}"


def low_signal_reason(failed: Iterable[SignalName]) -> str:
    """Build the reason string for a line below the threshold (rule D10).

    Args:
        failed: The signals below the threshold, in any order and possibly repeated.

    Returns:
        ``LOW_SIGNAL:`` and the distinct names in ``SignalName`` order joined by ``+``, e.g.
        ``LOW_SIGNAL:v+confidence``.

    Raises:
        ValueError: No signal is given.

    """
    distinct = set(failed)
    if not distinct:
        raise ValueError("LOW_SIGNAL needs at least one failed signal")
    names = [signal.value for signal in SignalName if signal in distinct]
    return f"{LOW_SIGNAL_PREFIX}{LOW_SIGNAL_SEPARATOR.join(names)}"


def ensemble_degraded(reason: str) -> str:
    """Mark a SIGNAL or LOW_SIGNAL reason as decided with a voter missing (E-02(d)).

    Args:
        reason: A frozen ``SIGNAL:*`` or ``LOW_SIGNAL:*`` reason.

    Returns:
        ``ENSEMBLE_DEGRADED:<reason>``.

    Raises:
        ValueError: ``reason`` is not a frozen SIGNAL or LOW_SIGNAL form.

    """
    if not _is_signal_form(reason):
        raise ValueError(f"only SIGNAL and LOW_SIGNAL reasons degrade, got {reason!r}")
    return f"{ENSEMBLE_DEGRADED_PREFIX}{reason}"


def _is_signal_form(reason: str) -> bool:
    """Tell whether a reason is a frozen ``SIGNAL:*`` or ``LOW_SIGNAL:*`` form."""
    if reason.startswith(SIGNAL_PREFIX):
        return THRESHOLD_ID_RE.fullmatch(reason.removeprefix(SIGNAL_PREFIX)) is not None
    if not reason.startswith(LOW_SIGNAL_PREFIX):
        return False
    names = reason.removeprefix(LOW_SIGNAL_PREFIX).split(LOW_SIGNAL_SEPARATOR)
    known = {signal.value for signal in SignalName}
    if not set(names) <= known:
        return False
    return reason == low_signal_reason(SignalName(name) for name in names)


def is_frozen_reason(reason: str) -> bool:
    """Tell whether a string belongs to the frozen reason-code list (§9.5).

    Args:
        reason: Any reason string.

    Returns:
        True for a fixed member or a well-formed parameterised form.

    """
    if reason in {code.value for code in ReasonCode}:
        return True
    if reason.startswith(LLM_FAILURE_PREFIX):
        kind = reason.removeprefix(LLM_FAILURE_PREFIX)
        return kind in {member.value for member in LLMFailureKind}
    if reason.startswith(ENSEMBLE_DEGRADED_PREFIX):
        return _is_signal_form(reason.removeprefix(ENSEMBLE_DEGRADED_PREFIX))
    return _is_signal_form(reason)


def confidence_bucket(confidence: int) -> ConfidenceBucket:
    """Place a self-reported confidence in its bucket.

    Args:
        confidence: An integer in ``CONFIDENCE_MIN..CONFIDENCE_MAX``.

    Returns:
        The bucket: >= 90, 80-89, 70-79 or < 70.

    Raises:
        ValueError: The confidence is out of range.

    """
    if not CONFIDENCE_MIN <= confidence <= CONFIDENCE_MAX:
        raise ValueError(f"confidence {confidence} outside {CONFIDENCE_MIN}..{CONFIDENCE_MAX}")
    for floor, bucket in BUCKET_FLOORS:
        if confidence >= floor:
            return bucket
    return ConfidenceBucket.BELOW_70


@dataclass(frozen=True)
class Signals:
    """Score s of one line: votes v, attribute signal b and the confidence bucket (§10.6).

    Attributes:
        votes: Passes whose top-1 equals the plurality top-1.
        attributes: Signal (b), ``AGREE`` or ``NO_EVIDENCE``; a conflict never gets a score.
        confidence: The self-reported confidence bucket.

    """

    votes: int
    attributes: AttrResult
    confidence: ConfidenceBucket

    def __post_init__(self) -> None:
        """Reject negative votes and a conflict signal.

        Raises:
            ValueError: The signals cannot form a score.

        """
        if self.votes < 0:
            raise ValueError(f"votes must be >= 0, got {self.votes}")
        if self.attributes == AttrResult.CONFLICT:
            raise ValueError("an attribute conflict is vetoed before scoring")

    def rank(self) -> tuple[int, int, int]:
        """Return s as a tuple that compares lexicographically."""
        return (self.votes, int(self.attributes == AttrResult.AGREE), int(self.confidence))


@dataclass(frozen=True)
class Threshold:
    """An operating point on score s: a line matches when s is at or above ``minimum``.

    Attributes:
        threshold_id: Id used in ``SIGNAL:<threshold id>``.
        minimum: The lowest score that still matches.

    """

    threshold_id: str
    minimum: Signals

    def __post_init__(self) -> None:
        """Reject an id that cannot appear in a reason string.

        Raises:
            ValueError: The id is empty or contains whitespace or a colon.

        """
        _check_threshold_id(self.threshold_id)

    def meets(self, signals: Signals) -> bool:
        """Tell whether a score is at or above this threshold, lexicographically."""
        return signals.rank() >= self.minimum.rank()

    def failed_signals(self, signals: Signals) -> tuple[SignalName, ...]:
        """Return the components below the threshold's, or () when the score meets it."""
        if self.meets(signals):
            return ()
        pairs = zip(SignalName, signals.rank(), self.minimum.rank(), strict=True)
        return tuple(name for name, actual, needed in pairs if actual < needed)


@dataclass(frozen=True)
class PassOutcome:
    """One pass's result for one line: a validated answer or a failure kind, never both.

    Attributes:
        call_ids: Every call attempt that touched the line in this pass.
        answer: The validated answer.
        failure: The failure, when no valid answer arrived.

    """

    call_ids: tuple[str, ...]
    answer: LineAnswer | None = None
    failure: LLMFailureKind | None = None

    def __post_init__(self) -> None:
        """Reject an outcome with neither or both of an answer and a failure.

        Raises:
            ValueError: Not exactly one of ``answer`` and ``failure`` is set.

        """
        if (self.answer is None) == (self.failure is None):
            raise ValueError("a pass outcome holds exactly one of answer and failure")


@dataclass(frozen=True)
class DecisionInput:
    """Everything the decision table reads for one line; built by the service.

    Attributes:
        line: The BoQ line, carrying its kind and raw unit.
        haystack: ``normalize(short + ' ' + long + ' ' + path)``, for the D5a evidence check.
        attributes: Values extracted from the line.
        has_supply_marker: Whether a supply marker occurs in the line (G2).
        is_service_unit: Whether the raw unit is in ``service_units.yaml`` (G2).
        passes: One outcome per pass, in pass order; empty for rule-decided lines.
        threshold: The frozen threshold that policy resolution selected.
        line_failure: ``LLM_UNAVAILABLE`` or ``BUDGET_CAP`` when the line was never answered.

    """

    line: BoqLine
    haystack: str
    attributes: Attributes
    has_supply_marker: bool
    is_service_unit: bool
    passes: tuple[PassOutcome, ...]
    threshold: Threshold
    line_failure: ReasonCode | None = None

    def __post_init__(self) -> None:
        """Reject a line failure other than ``LLM_UNAVAILABLE`` or ``BUDGET_CAP``.

        Raises:
            ValueError: ``line_failure`` is another reason code.

        """
        if self.line_failure is not None and self.line_failure not in LINE_FAILURE_REASONS:
            raise ValueError(f"line_failure must be one of {sorted(LINE_FAILURE_REASONS)}")


@dataclass(frozen=True)
class LineDecision:
    """The decision for one line, with what the writer and the audit record need.

    Attributes:
        rule: The table row that fired.
        decision: ``RULE_DECISIONS[rule]``.
        reason: A frozen reason string.
        row: The matched library row; set only when ``decision`` is matched.
        top1: Plurality top-1 code, as suggested; empty for rule-decided lines.
        top2: Top-2 code suggestion, or empty.
        call_ids: Every call that touched the line; empty for rule-decided lines.
        signals: Score s, when the line reached scoring.

    """

    rule: Rule
    decision: Decision
    reason: str
    row: LibraryRow | None = None
    top1: str = ""
    top2: str = ""
    call_ids: tuple[str, ...] = ()
    signals: Signals | None = None

    def __post_init__(self) -> None:
        """Enforce the table's invariants, including RQ5 provenance, by construction.

        Raises:
            ValueError: The values break an invariant of the decision table.

        """
        problem = _decision_problem(self)
        if problem:
            raise ValueError(f"inconsistent line decision: {problem}")


def _decision_problem(decision: LineDecision) -> str:
    """Return why a decision breaks the table's invariants, or an empty string."""
    if not is_frozen_reason(decision.reason):
        return f"reason {decision.reason!r} is not in the frozen list"
    if RULE_DECISIONS[decision.rule] != decision.decision:
        return f"rule {decision.rule} cannot emit {decision.decision}"
    if (decision.decision == Decision.MATCHED) != (decision.row is not None):
        return "a row is set exactly when the line is matched"
    if decision.decision == Decision.MATCHED and not _is_match_reason(decision.reason):
        return f"a match needs a SIGNAL reason, got {decision.reason!r}"
    is_skip = decision.decision == Decision.NOT_A_MATERIAL
    if is_skip and decision.reason not in NOT_A_MATERIAL_REASONS:
        return f"not_a_material cannot carry {decision.reason!r}"
    return ""


def _is_match_reason(reason: str) -> bool:
    """Tell whether a reason is ``SIGNAL:*`` or ``ENSEMBLE_DEGRADED:SIGNAL:*``."""
    return reason.removeprefix(ENSEMBLE_DEGRADED_PREFIX).startswith(SIGNAL_PREFIX)
