"""Decision vocabulary, the frozen reason codes and the decision table (DESIGN.md §9.5).

``DecisionInput`` is everything the pure D0-D10 table reads for one line; ``LineDecision`` is
what it emits, consumed by the writer (§9.6), ``audit.jsonl`` and the service. Score s and the
thresholds on it follow §10.6. ``decide`` is the B3 table, first rule wins; ``decide_b2`` is
the B2 rung of the baseline ladder (§10.5).
"""

import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType

from oris_matcher.domain.attributes import Attributes, AttrResult, compare, has_hard_attribute
from oris_matcher.domain.boq import BoqLine, LineKind
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.domain.normalize import normalize
from oris_matcher.domain.validator import is_valid_code
from oris_matcher.prompts.v1.schema import CONFIDENCE_MAX, CONFIDENCE_MIN, LineAnswer

SIGNAL_PREFIX = "SIGNAL:"
LOW_SIGNAL_PREFIX = "LOW_SIGNAL:"
ENSEMBLE_DEGRADED_PREFIX = "ENSEMBLE_DEGRADED:"
LLM_FAILURE_PREFIX = "LLM_FAILURE:"
LOW_SIGNAL_SEPARATOR = "+"
THRESHOLD_ID_RE = re.compile(r"[^\s:]+")
EVIDENCE_WORD_RE = re.compile(r"\w+")
BUCKET_FLOOR_90 = 90
BUCKET_FLOOR_80 = 80
BUCKET_FLOOR_70 = 70
THRESHOLD_ID_PREFIX = "T"
PATH_SEPARATOR = " > "
HEADER_PART_SEPARATOR = " "


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


def candidate_thresholds(passes: int) -> tuple[Threshold, ...]:
    """Return the pre-registered operating points on score s, strictest first (§10.6).

    Every candidate needs ``v = k``; they step through signal (b), ``agree`` then
    ``no_evidence``, and within each through the confidence buckets from >= 90 down to < 70.
    Each is a superset of the one before, so the order is fixed before any call.

    Args:
        passes: k, the number of passes per line.

    Returns:
        The 8 thresholds, with ids ``T1`` (strictest) to ``T8`` (loosest).

    Raises:
        ValueError: ``passes`` is below 1.

    """
    if passes < 1:
        raise ValueError(f"the number of passes must be >= 1, got {passes}")
    minima = (
        Signals(passes, attributes, bucket)
        for attributes in (AttrResult.AGREE, AttrResult.NO_EVIDENCE)
        for bucket in sorted(ConfidenceBucket, reverse=True)
    )
    return tuple(
        Threshold(f"{THRESHOLD_ID_PREFIX}{index}", minimum)
        for index, minimum in enumerate(minima, start=1)
    )


def strictest_threshold(passes: int) -> Threshold:
    """Return the strictest threshold, (v = k, agree, >= 90), used by policy fallback (§10.6).

    Args:
        passes: k, the number of passes per line.

    Returns:
        The first of ``candidate_thresholds(passes)``.

    """
    return candidate_thresholds(passes)[0]


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
        verifier_top1: The sibling verifier's top-1 code; read by D8a only when E-08 is adopted.

    """

    line: BoqLine
    haystack: str
    attributes: Attributes
    has_supply_marker: bool
    is_service_unit: bool
    passes: tuple[PassOutcome, ...]
    threshold: Threshold
    line_failure: ReasonCode | None = None
    verifier_top1: str | None = None

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
        line_id: The decided line's ``line_id``, so a decision can be tied to its line.

    """

    rule: Rule
    decision: Decision
    reason: str
    row: LibraryRow | None = None
    top1: str = ""
    top2: str = ""
    call_ids: tuple[str, ...] = ()
    signals: Signals | None = None
    line_id: str = ""

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


HAYSTACK_SEPARATOR = " "
NON_MATERIAL = "non_material"
NO_EQUIVALENT = "no_equivalent"
FAIL_CLOSED_KINDS = (LLMFailureKind.REPLAY_MISS, LLMFailureKind.DUPLICATE_CONFLICT)


@dataclass(frozen=True)
class DecisionProfile:
    """Flags for the gated rows of the table, all off until an experiment is adopted.

    Attributes:
        verifier_adopted: E-08 is adopted, so D8a runs and a missing verifier is a D1b
            ``partial_signal`` (A20).

    """

    verifier_adopted: bool = False


DEFAULT_PROFILE = DecisionProfile()


def section_path_text(line: BoqLine) -> str:
    """Render a line's section path as the model is shown it, outermost header first.

    Args:
        line: The BoQ line.

    Returns:
        Each header's raw item number and text joined by a space, the headers joined by
        `` > ``; empty when the line has no section context.

    """
    headers = (
        HEADER_PART_SEPARATOR.join(part for part in (header.item_no, header.text) if part)
        for header in line.section_path
    )
    return PATH_SEPARATOR.join(headers)


def make_haystack(line: BoqLine) -> str:
    """Build the text the D5a evidence check searches.

    Args:
        line: The BoQ line.

    Returns:
        ``normalize(short + ' ' + long + ' ' + path)``, where path is ``section_path_text``,
        so evidence copied from the path the model was shown is found.

    """
    parts = (line.short, line.long, section_path_text(line))
    return normalize(HAYSTACK_SEPARATOR.join(parts))


def is_service_unit(raw_unit: str, service_units: Collection[str]) -> bool:
    """Tell whether a raw unit is a configured service unit (gate G2).

    Args:
        raw_unit: The line's raw unit.
        service_units: Units from ``service_units.yaml``.

    Returns:
        True when the unit, with surrounding whitespace removed, equals one exactly; case is
        significant, so ``Ft`` (forfait) never captures ``ft`` (feet).

    """
    return raw_unit.strip() in service_units


@dataclass(frozen=True)
class _Tally:
    """The plurality vote over the valid answers of one line (§10.6).

    Attributes:
        answers: Valid answers, in pass order.
        leader: The first answer, in pass order, whose top1 has the most votes.
        votes: v, the number of passes whose top1 equals the leader's.
        is_tie: Another top1 has as many votes as the leader's.
        supporters: The answers whose top1 equals the leader's.
        top2: The leader's top2 when it is a library code, else empty.

    """

    answers: tuple[LineAnswer, ...]
    leader: LineAnswer
    votes: int
    is_tie: bool
    supporters: tuple[LineAnswer, ...]
    top2: str


@dataclass(frozen=True)
class _Context:
    """What every rule of one evaluation reads.

    Attributes:
        decision_input: The line's input.
        library: The loaded library.
        call_ids: Every call that touched the line, de-duplicated, in pass order.

    """

    decision_input: DecisionInput
    library: Library
    call_ids: tuple[str, ...]

    def decided(
        self,
        rule: Rule,
        reason: str,
        tally: _Tally | None = None,
        signals: Signals | None = None,
    ) -> LineDecision:
        """Build the unmatched decision of a rule for this line, with any suggestion."""
        return LineDecision(
            rule=rule,
            decision=RULE_DECISIONS[rule],
            reason=reason,
            top1=tally.leader.top1 if tally else "",
            top2=tally.top2 if tally else "",
            call_ids=self.call_ids,
            signals=signals,
            line_id=self.decision_input.line.line_id,
        )


def _tally(answers: tuple[LineAnswer, ...], library: Library) -> _Tally:
    """Count the top-1 votes of a non-empty tuple of answers."""
    counts = Counter(answer.top1 for answer in answers)
    votes = max(counts.values())
    leaders = [answer for answer in answers if counts[answer.top1] == votes]
    leader = leaders[0]
    supporters = tuple(answer for answer in answers if answer.top1 == leader.top1)
    is_tie = len({answer.top1 for answer in leaders}) > 1
    top2 = leader.top2 if is_valid_code(leader.top2, library) else ""
    return _Tally(answers, leader, votes, is_tie, supporters, top2)


def _rule_decided(line: BoqLine, rule: Rule, reason: ReasonCode) -> LineDecision:
    """Build the decision of a structural rule, which no model call touched."""
    return LineDecision(
        rule=rule, decision=RULE_DECISIONS[rule], reason=reason, line_id=line.line_id
    )


def _matched(context: _Context, tally: _Tally, signals: Signals) -> LineDecision:
    """Build the D9 decision, carrying the library's own row for top1."""
    return LineDecision(
        rule=Rule.D9,
        decision=Decision.MATCHED,
        reason=signal_reason(context.decision_input.threshold.threshold_id),
        row=context.library.by_code[tally.leader.top1],
        top1=tally.leader.top1,
        top2=tally.top2,
        call_ids=context.call_ids,
        signals=signals,
        line_id=context.decision_input.line.line_id,
    )


def _is_blank(cell: str) -> bool:
    """Tell whether a raw cell is empty or whitespace only."""
    return not cell.strip()


def _is_fully_empty(line: BoqLine) -> bool:
    """Tell whether every cell of a line is blank."""
    cells = (line.item_no, line.short, line.long, line.unit, line.qty, *line.raw_row)
    return all(map(_is_blank, cells))


def _structural_gate(line: BoqLine) -> LineDecision | None:
    """Apply D0, D0a and D0b, which need empty Unit and Qty and come before any answer."""
    if not (_is_blank(line.unit) and _is_blank(line.qty)):
        return None
    if line.kind == LineKind.HEADER:
        return _rule_decided(line, Rule.D0, ReasonCode.HEADER)
    if line.kind == LineKind.EMPTY_ROW and _is_fully_empty(line):
        return _rule_decided(line, Rule.D0A, ReasonCode.EMPTY_ROW)
    return _rule_decided(line, Rule.D0B, ReasonCode.HEADER_UNCONFIRMED)


def _failure_gate(
    context: _Context, profile: DecisionProfile, required_passes: int
) -> LineDecision | _Tally:
    """Apply D1 and D1b; return the vote tally when the line has every answer it needs.

    Fewer pass outcomes than ``required_passes`` means a pass the threshold needs is missing,
    which is a D1b ``partial_signal`` and never a ``LOW_SIGNAL``.
    """
    decision_input = context.decision_input
    if decision_input.line_failure is not None:
        return context.decided(Rule.D1, decision_input.line_failure)
    answers = tuple(outcome.answer for outcome in decision_input.passes if outcome.answer)
    tally = _tally(answers, context.library) if answers else None
    failures = [outcome.failure for outcome in decision_input.passes if outcome.failure]
    for kind in FAIL_CLOSED_KINDS:
        if kind in failures:
            return context.decided(Rule.D1, llm_failure_reason(kind), tally)
    if tally is None:
        reason = llm_failure_reason(failures[0]) if failures else ReasonCode.INTERNAL_INVARIANT
        return context.decided(Rule.D1, reason)
    verifier_missing = profile.verifier_adopted and decision_input.verifier_top1 is None
    pass_missing = len(decision_input.passes) < required_passes
    if failures or verifier_missing or pass_missing:
        partial = llm_failure_reason(LLMFailureKind.PARTIAL_SIGNAL)
        return context.decided(Rule.D1B, partial, tally)
    return tally


def _g2_holds(decision_input: DecisionInput) -> bool:
    """Tell whether gate G2 admits a skip: service unit, no hard attribute, no supply marker."""
    return (
        decision_input.is_service_unit
        and not has_hard_attribute(decision_input.attributes)
        and not decision_input.has_supply_marker
    )


def _kind_gate(context: _Context, tally: _Tally) -> LineDecision | None:
    """Apply D2, D3 and D4; any pass that is not ``material`` sends the line to review."""
    kinds = {answer.kind for answer in tally.answers}
    if kinds == {NON_MATERIAL} and _g2_holds(context.decision_input):
        return context.decided(Rule.D2, ReasonCode.G2_SERVICE, tally)
    if NON_MATERIAL in kinds:
        return context.decided(Rule.D3, ReasonCode.NM_UNCONFIRMED, tally)
    if NO_EQUIVALENT in kinds:
        return context.decided(Rule.D4, ReasonCode.NO_LIBRARY_EQUIVALENT, tally)
    return None


def _evidence_in_line(evidence: str, haystack: str) -> bool:
    r"""Tell whether the evidence has a word and every word of it is a word of the haystack.

    Words are whole ``\w+`` runs of the normalised texts, in any order (A62): the model
    stitches its quote from the short and long descriptions, and a word from outside the line
    still fails.
    """
    words = set(EVIDENCE_WORD_RE.findall(normalize(evidence)))
    return bool(words) and words <= set(EVIDENCE_WORD_RE.findall(normalize(haystack)))


def _is_generic_parent(code: str, attributes: Attributes, library: Library) -> bool:
    """Tell whether a code is a mixed parent's blank leaf with a sibling that agrees."""
    siblings = library.mixed_parents.get(code, ())
    return any(
        compare(attributes, library.by_code[sibling].attributes) == AttrResult.AGREE
        for sibling in siblings
    )


def _code_veto(context: _Context, tally: _Tally) -> Rule | None:
    """Return the first of D5, D5a and D6 that vetoes the plurality top1, or None."""
    code = tally.leader.top1
    if not is_valid_code(code, context.library):
        return Rule.D5
    haystack = context.decision_input.haystack
    if not all(_evidence_in_line(answer.evidence, haystack) for answer in tally.supporters):
        return Rule.D5A
    if context.library.is_never_match(code):
        return Rule.D6
    return None


def _attribute_veto(context: _Context, tally: _Tally, profile: DecisionProfile) -> Rule | None:
    """Return the first of D7, D8 and D8a that vetoes a valid top1, or None."""
    decision_input, library = context.decision_input, context.library
    code = tally.leader.top1
    if compare(decision_input.attributes, library.by_code[code].attributes) == AttrResult.CONFLICT:
        return Rule.D7
    if _is_generic_parent(code, decision_input.attributes, library):
        return Rule.D8
    if profile.verifier_adopted and decision_input.verifier_top1 != code:
        return Rule.D8A
    return None


def _row_veto(context: _Context, tally: _Tally, profile: DecisionProfile) -> Rule | None:
    """Return the first of D5 to D8a that vetoes the plurality top1, or None."""
    return _code_veto(context, tally) or _attribute_veto(context, tally, profile)


VETO_REASONS: Mapping[Rule, ReasonCode] = MappingProxyType(
    {
        Rule.D5: ReasonCode.INVALID_ROW_ID,
        Rule.D5A: ReasonCode.EVIDENCE_NOT_IN_LINE,
        Rule.D6: ReasonCode.NEVER_MATCH_ROW,
        Rule.D7: ReasonCode.ATTR_CONFLICT,
        Rule.D8: ReasonCode.GENERIC_PARENT,
        Rule.D8A: ReasonCode.VERIFIER_DISAGREES,
    }
)


B2_PASSES = 1
B2_ABSTENTIONS: Mapping[str, Rule] = MappingProxyType(
    {NON_MATERIAL: Rule.D3, NO_EQUIVALENT: Rule.D4}
)
B2_REASONS: Mapping[Rule, ReasonCode] = MappingProxyType(
    {
        Rule.D3: ReasonCode.NM_UNCONFIRMED,
        Rule.D4: ReasonCode.NO_LIBRARY_EQUIVALENT,
        Rule.D5: ReasonCode.INVALID_ROW_ID,
    }
)


def _signals(tally: _Tally, attributes: AttrResult) -> Signals:
    """Build score s; the confidence is the weakest among the passes that voted for top1."""
    weakest = min(answer.confidence for answer in tally.supporters)
    return Signals(tally.votes, attributes, confidence_bucket(weakest))


def _score(context: _Context, tally: _Tally) -> LineDecision:
    """Apply D9 and D10; a vote tie never matches, whatever the threshold."""
    decision_input = context.decision_input
    row = context.library.by_code[tally.leader.top1]
    signals = _signals(tally, compare(decision_input.attributes, row.attributes))
    threshold = decision_input.threshold
    if threshold.meets(signals) and not tally.is_tie:
        return _matched(context, tally, signals)
    failed = set(threshold.failed_signals(signals))
    if tally.is_tie:
        failed.add(SignalName.VOTES)
    return context.decided(Rule.D10, low_signal_reason(failed), tally, signals)


def _context(decision_input: DecisionInput, library: Library) -> _Context:
    """Bundle one line's input with the library and its de-duplicated call ids."""
    call_ids = (call_id for outcome in decision_input.passes for call_id in outcome.call_ids)
    return _Context(decision_input, library, tuple(dict.fromkeys(call_ids)))


def decide(
    decision_input: DecisionInput, library: Library, profile: DecisionProfile = DEFAULT_PROFILE
) -> LineDecision:
    """Decide one line with the B3 table: the first rule that fires wins (§9.5).

    Order: D0, D0a, D0b, D1, D1b, D2, D3, D4, D5, D5a, D6, D7, D8, D8a (only when
    ``profile.verifier_adopted``), D9, D10. top1 is the plurality top-1 across the passes; on a
    tie the vetoes run on the earliest pass's choice and the line ends at D10. Only D0, D0a and
    D2 emit ``not_a_material``; D0 and D0a need empty Unit and Qty, and D2 a service unit, so a
    measured unit never reaches it. ``ENSEMBLE_DEGRADED`` forms are not emitted, because
    E-02(d) is not adopted.

    Args:
        decision_input: Everything the table reads for the line.
        library: The loaded library; a match is always its own row.
        profile: Flags for gated rows.

    Returns:
        The line's decision.

    """
    gated = _structural_gate(decision_input.line)
    if gated is not None:
        return gated
    context = _context(decision_input, library)
    tally = _failure_gate(context, profile, decision_input.threshold.minimum.votes)
    if isinstance(tally, LineDecision):
        return tally
    by_kind = _kind_gate(context, tally)
    if by_kind is not None:
        return by_kind
    veto = _row_veto(context, tally, profile)
    if veto is not None:
        return context.decided(veto, VETO_REASONS[veto], tally)
    return _score(context, tally)


def decide_b2(decision_input: DecisionInput, library: Library) -> LineDecision:
    """Decide one line as rung B2 of the baseline ladder: every valid answer matched (§10.5).

    B2 is the raw single-pass model with no extractors and no glossary, so more than one pass
    outcome is a service bug and gives D1 ``INTERNAL_INVARIANT``. It keeps the gates that no
    rung may drop: D0, D0a and D0b (structure), D1 and D1b (no answer), and D5 (closed world:
    only a library code can match). An answer without a library top1 that abstains as
    ``non_material`` or ``no_equivalent`` is reported as D3 or D4 rather than D5. Otherwise it
    skips D2, D3 and D4 (the model's kind), D5a (evidence), D6 (never-match rows), D7 and D8
    (attribute vetoes), D8a, and the threshold of D9 and D10: any valid top1 is matched as
    ``SIGNAL:<threshold id>``, with signal (b) recorded as ``no_evidence``. B2 emits
    ``not_a_material`` only from D0 and D0a.

    Args:
        decision_input: Everything the table reads for the line; its threshold names the run.
        library: The loaded library; a match is always its own row.

    Returns:
        The line's decision.

    """
    gated = _structural_gate(decision_input.line)
    if gated is not None:
        return gated
    context = _context(decision_input, library)
    if len(decision_input.passes) > B2_PASSES:
        return context.decided(Rule.D1, ReasonCode.INTERNAL_INVARIANT)
    tally = _failure_gate(context, DEFAULT_PROFILE, B2_PASSES)
    if isinstance(tally, LineDecision):
        return tally
    if is_valid_code(tally.leader.top1, library):
        return _matched(context, tally, _signals(tally, AttrResult.NO_EVIDENCE))
    abstention = B2_ABSTENTIONS.get(tally.leader.kind, Rule.D5)
    return context.decided(abstention, B2_REASONS[abstention], tally)
