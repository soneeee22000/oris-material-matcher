"""Candidate provider that offers the whole library to every line (DESIGN.md §9.2).

This is the default provider (D-01): every row is a candidate for every batch, so candidate
inclusion is 1.0 by construction and no shortlist can cap accuracy before the model is called.
"""

from collections.abc import Sequence

from oris_matcher.domain.boq import BoqLine
from oris_matcher.domain.library import Library, LibraryRow

WHOLE_LIBRARY_NAME = "whole_library"
FULL_INCLUSION = 1.0


class WholeLibrary:
    """Offers every library row, in display-code order, to every batch."""

    name = WHOLE_LIBRARY_NAME

    def __init__(self, library: Library) -> None:
        """Bind the provider to a loaded library.

        Args:
            library: The loaded library.

        """
        self.library = library

    def candidates(self, lines: Sequence[BoqLine]) -> tuple[LibraryRow, ...]:
        """Return the library rows offered to the model for one batch.

        Args:
            lines: The batch's lines, in batch order; every batch gets the same rows.

        Returns:
            Every library row, in display-code order.

        """
        del lines
        return self.library.rows

    def inclusion(self, lines: Sequence[BoqLine]) -> float:
        """Return the share of a batch's correct rows that can be in its candidate set.

        Args:
            lines: The batch's lines.

        Returns:
            1.0: the whole library always includes the correct row when one exists.

        """
        del lines
        return FULL_INCLUSION
