"""BoQ line and file contracts shared by the reader, the service and the writer (DESIGN.md §9.1).

Every cell is kept as the raw input string: no stripping, no NaN conversion.
"""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

LINE_ID_LENGTH = 16
ID_HASH_ENCODING = "utf-8"


class LineKind(StrEnum):
    """Structural class of a BoQ row, decided before any model call."""

    HEADER = "header"
    EMPTY_ROW = "empty_row"
    HEADER_UNCONFIRMED = "header_unconfirmed"
    ITEM = "item"


class Column(StrEnum):
    """Canonical names of the required input columns."""

    ITEM_NO = "item_no"
    SHORT = "short"
    LONG = "long"
    UNIT = "unit"
    QTY = "qty"


class PathMode(StrEnum):
    """Whether section paths could be derived from header rows in the file."""

    DERIVED = "derived"
    NONE = "none"


@dataclass(frozen=True)
class SectionHeader:
    """One header row on a line's section path.

    Attributes:
        item_no: The header's raw item number.
        text: The header's raw description.

    """

    item_no: str
    text: str


@dataclass(frozen=True)
class BoqLine:
    """One input row, with every cell as its raw string.

    Attributes:
        position: 0-based row index in the input, excluding the header row.
        line_id: ``make_line_id(position, item_no, short, long)``.
        item_no: Raw item number.
        short: Raw short description.
        long: Raw long description.
        unit: Raw unit.
        qty: Raw quantity.
        kind: Structural class of the row.
        section_path: Enclosing header rows, outermost first; empty when none derive.
        extra: Pass-through columns as (original column name, raw value), in input order.
        raw_row: All original cells in input column order, for the writer.

    """

    position: int
    line_id: str
    item_no: str
    short: str
    long: str
    unit: str
    qty: str
    kind: LineKind
    section_path: tuple[SectionHeader, ...]
    extra: tuple[tuple[str, str], ...]
    raw_row: tuple[str, ...]

    @property
    def extra_map(self) -> dict[str, str]:
        """The pass-through columns as a new dict keyed by original column name."""
        return dict(self.extra)


@dataclass(frozen=True)
class BoqFile:
    """A parsed BoQ input.

    Attributes:
        lines: Every input row, in input order.
        header: Original column names, in input order.
        encoding: Encoding the file was decoded with, e.g. ``utf-8`` or ``cp1252``.
        delimiter: Field delimiter, ``,`` or ``;``.
        path_mode: Whether section paths were derived.
        column_map: Pairs of (canonical column, original column name).

    """

    lines: tuple[BoqLine, ...]
    header: tuple[str, ...]
    encoding: str
    delimiter: str
    path_mode: PathMode
    column_map: tuple[tuple[Column, str], ...]

    @property
    def column_dict(self) -> dict[str, str]:
        """The column map as a new dict, canonical name -> original column name."""
        return {str(canonical): original for canonical, original in self.column_map}

    def column(self, canonical: Column) -> str:
        """Return the original name of one canonical column.

        Args:
            canonical: The canonical column.

        Returns:
            The column's name in the input header.

        Raises:
            KeyError: The column was not mapped.

        """
        return self.column_dict[canonical]


def make_line_id(position: int, item_no: str, short: str, long: str) -> str:
    """Hash a row's position, item number and text into its stable line id.

    Duplicate texts at different positions get different ids (DESIGN.md §9.1, A8).

    Args:
        position: 0-based row index.
        item_no: Raw item number.
        short: Raw short description.
        long: Raw long description.

    Returns:
        The first ``LINE_ID_LENGTH`` hex characters of a SHA-256.

    """
    payload = json.dumps(
        [position, item_no, short, long], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode(ID_HASH_ENCODING)).hexdigest()[:LINE_ID_LENGTH]
