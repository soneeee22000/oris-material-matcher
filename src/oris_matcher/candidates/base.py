"""The CandidateProvider port (DESIGN.md §11.1): which library rows a batch is shown against."""

from collections.abc import Sequence
from typing import Protocol

from oris_matcher.domain.boq import BoqLine
from oris_matcher.domain.library import LibraryRow


class CandidateProvider(Protocol):
    """The port every candidate provider implements (whole library, or retrieval)."""

    def candidates(self, lines: Sequence[BoqLine]) -> tuple[LibraryRow, ...]:
        """Return the library rows offered to the model for one batch.

        Args:
            lines: The batch's lines, in batch order.

        Returns:
            The candidate rows in display-code order; the prompt renders them as a coded tree.

        """
        ...
