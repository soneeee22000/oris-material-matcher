"""Grouping of routed BoQ lines into model batches with transport ids (DESIGN.md §9.1, §9.4).

``plan_batches`` is pure and deterministic, so the CLI, the API and the UI batch identically
(§5.2, A50). A batch holds up to ``size`` lines that are contiguous in the input and share the
same section path, so it never crosses a header boundary.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from oris_matcher.domain.boq import BoqLine, LineKind

DEFAULT_BATCH_SIZE = 10
TRANSPORT_ID_PREFIX = "L"
ROUTED_KIND = LineKind.ITEM


def transport_id(line: BoqLine) -> str:
    """Return the in-batch transport id of a line.

    Args:
        line: A BoQ line.

    Returns:
        ``L<position>``, e.g. ``L118``.

    """
    return f"{TRANSPORT_ID_PREFIX}{line.position}"


@dataclass(frozen=True)
class Batch:
    """One model batch: routed lines in input order, with their transport ids.

    Attributes:
        lines: The routed lines, contiguous and under one section path.
        transport_ids: ``transport_id`` of each line, in the same order.

    """

    lines: tuple[BoqLine, ...]
    transport_ids: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        """Derive the transport ids and reject an empty or non-routed batch.

        Raises:
            ValueError: The batch is empty or holds a line that is not routed to the model.

        """
        if not self.lines:
            raise ValueError("a batch cannot be empty")
        if any(line.kind != ROUTED_KIND for line in self.lines):
            raise ValueError("a batch holds only routed (item) lines")
        object.__setattr__(self, "transport_ids", tuple(map(transport_id, self.lines)))

    def line_for(self, transport: str) -> BoqLine | None:
        """Return the line carrying a transport id, compared exactly.

        Args:
            transport: A transport id from a model answer.

        Returns:
            The line, or None for an id this batch does not hold.

        """
        for line, line_transport in zip(self.lines, self.transport_ids, strict=True):
            if line_transport == transport:
                return line
        return None


def _continues(current: list[BoqLine], line: BoqLine, size: int) -> bool:
    """Tell whether a routed line may join the batch being built."""
    if not current or len(current) >= size:
        return False
    previous = current[-1]
    adjacent = line.position == previous.position + 1
    return adjacent and line.section_path == previous.section_path


def plan_batches(lines: Iterable[BoqLine], size: int = DEFAULT_BATCH_SIZE) -> list[Batch]:
    """Group the routed lines into batches, in input order.

    Only item lines are routed. A batch ends at ``size`` lines, at any change of section path,
    and wherever the routed lines are not adjacent in the input (a header, an empty row or an
    unconfirmed header sits between them, or the caller left lines out).

    Args:
        lines: BoQ lines in input order; rule-decided lines may be included and are skipped.
        size: Maximum lines per batch.

    Returns:
        The batches; every routed line is in exactly one, in input order.

    Raises:
        ValueError: ``size`` is not positive.

    """
    if size < 1:
        raise ValueError(f"batch size must be >= 1, got {size}")
    batches: list[Batch] = []
    current: list[BoqLine] = []
    for line in lines:
        if line.kind != ROUTED_KIND:
            continue
        if current and not _continues(current, line, size):
            batches.append(Batch(lines=tuple(current)))
            current = []
        current.append(line)
    if current:
        batches.append(Batch(lines=tuple(current)))
    return batches
