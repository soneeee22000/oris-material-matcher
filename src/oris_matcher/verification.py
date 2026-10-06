"""E-08 sibling verifier stage: which lines are asked, in which batches, what an answer means.

After the main passes, every line the table would match (``flagged_top1``, A65.2) is sent to
the verifier. Flagged lines are grouped by the material type of their top1, in input order of
each type's first line, and each group is cut into batches of at most ``batch_size`` lines in
input order. One request therefore carries one type, and its candidate rows (every row of that
type) are rendered once for all its lines. An answer counts only when it is schema-valid and
its code is one of the request's codes or ``NONE``, compared case-sensitively; anything else
leaves the line without a verifier answer, which the decision table reads as D1b.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.boq import BoqLine
from oris_matcher.domain.decision import LLMFailureKind, ReasonCode
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.llm.wrapper import LineOutcome
from oris_matcher.prompts.v1.verifier import sibling_rows, validated_code

INVALID_CODE = "invalid_code"
BATCHING = "per_material_type"
UNAVAILABLE_FAILURES = frozenset(
    {ReasonCode.LLM_UNAVAILABLE.value, LLMFailureKind.REPLAY_MISS.value}
)


@dataclass(frozen=True)
class VerifierLine:
    """What E-08 recorded for one line of a run with the verifier adopted.

    Attributes:
        flagged: Whether the line would match without the verifier, so it was asked.
        top1: The validated answer, a sibling code or ``NONE``; None when there is none.
        raw: The line's verbatim answer object, valid or not; empty when none arrived.
        call_ids: Every verifier attempt that touched the line.
        failure: Why there is no validated answer: an ``LLM_FAILURE`` kind, ``BUDGET_CAP``,
            ``LLM_UNAVAILABLE`` or ``invalid_code``; None when there is one or none was asked.

    """

    flagged: bool
    top1: str | None = None
    raw: str = ""
    call_ids: tuple[str, ...] = ()
    failure: str | None = None

    @property
    def is_unavailable(self) -> bool:
        """Whether the verifier could not be reached: the breaker, or a replay miss (§11.3)."""
        return self.failure in UNAVAILABLE_FAILURES


NOT_FLAGGED = VerifierLine(flagged=False)


@dataclass(frozen=True)
class VerifierGroup:
    """One verifier request's lines and the candidate rows they share.

    Attributes:
        lines: Flagged lines whose top1 has the same material type, in input order.
        rows: Every row of that type, in display-code order.

    """

    lines: tuple[BoqLine, ...]
    rows: tuple[LibraryRow, ...]

    @property
    def transport_ids(self) -> tuple[str, ...]:
        """The lines' transport ids, in order."""
        return tuple(transport_id(line) for line in self.lines)

    @property
    def codes(self) -> frozenset[str]:
        """The candidate codes a valid answer may name, besides ``NONE``."""
        return frozenset(row.code for row in self.rows)


def verifier_groups(
    flagged: Sequence[tuple[BoqLine, str]], library: Library, size: int
) -> tuple[VerifierGroup, ...]:
    """Batch the flagged lines by the material type of their top1.

    Args:
        flagged: (line, top1) for each flagged line, in input order.
        library: The loaded library.
        size: The most lines one request carries.

    Returns:
        The groups: types in the input order of their first line, lines in input order,
        each type cut into chunks of at most ``size`` lines.

    Raises:
        ValueError: ``size`` is not positive.

    """
    if size < 1:
        raise ValueError(f"batch size must be >= 1, got {size}")
    by_type: dict[str, list[BoqLine]] = {}
    rows_of: dict[str, tuple[LibraryRow, ...]] = {}
    for line, top1 in flagged:
        material_type = library.by_code[top1].material_type
        by_type.setdefault(material_type, []).append(line)
        rows_of.setdefault(material_type, sibling_rows(library, top1))
    return tuple(
        VerifierGroup(tuple(lines[start : start + size]), rows_of[material_type])
        for material_type, lines in by_type.items()
        for start in range(0, len(lines), size)
    )


def verifier_line(outcome: LineOutcome, codes: frozenset[str]) -> VerifierLine:
    """Turn the wrapper's outcome for one flagged line into what the table and the audit read.

    Args:
        outcome: The line's outcome from a ``VERIFIER_ANSWERS`` batch.
        codes: The request's candidate codes.

    Returns:
        The validated answer, or the failure that left the line without one.

    """
    raw, call_ids = outcome.raw_line_response, outcome.call_ids
    if outcome.verdict is not None:
        top1 = validated_code(outcome.verdict.code, codes)
        failure = None if top1 is not None else INVALID_CODE
        return VerifierLine(True, top1, raw, call_ids, failure)
    kind = outcome.failure or outcome.line_failure
    failure = kind.value if kind is not None else None
    return VerifierLine(True, None, raw, call_ids, failure)
