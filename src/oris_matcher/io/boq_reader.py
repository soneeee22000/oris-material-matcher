"""BoQ CSV reader: encodings, delimiter, headers and section paths (DESIGN.md §9.1).

Every cell stays the raw input string. Header decisions and section paths are computed over the
whole file before any chunking, so the CLI, the API and the UI see the same structure.
"""

import csv
import io
import re
import unicodedata
import warnings
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from oris_matcher.domain.boq import (
    BoqFile,
    BoqLine,
    Column,
    LineKind,
    PathMode,
    SectionHeader,
    make_line_id,
)

UTF8: Final = "utf-8"
UTF8_WITH_BOM: Final = "utf-8-sig"
CP1252: Final = "cp1252"
COMMA: Final = ","
SEMICOLON: Final = ";"
DEFAULT_HEADER_PATTERNS: Final[tuple[str, ...]] = (r"^\d{1,2}$|^\d{2}\.\d{2}\.$",)
FIELD_CAPS: Final[Mapping[Column, int]] = {
    Column.ITEM_NO: 64,
    Column.SHORT: 1000,
    Column.LONG: 4000,
}
COLUMN_NAME_NOISE_RE: Final = re.compile(r"[\s._\-]+")
CODE_SEPARATORS_RE: Final = re.compile(r"[.\-\s]+")
COLUMN_ALIASES: Final[Mapping[Column, tuple[str, ...]]] = {
    Column.ITEM_NO: (
        "Item No.",
        "Item number",
        "Item code",
        "N° article",
        "N° d'article",
        "No article",
        "Numéro d'article",
        "Numéro",
        "N°",
        "Réf",
        "Référence",
    ),
    Column.SHORT: (
        "Short Description",
        "Short desc",
        "Short",
        "Description courte",
        "Désignation",
        "Designation",
        "Libellé",
        "Libelle",
        "Intitulé",
    ),
    Column.LONG: (
        "Long Description",
        "Long desc",
        "Long",
        "Description longue",
        "Description détaillée",
        "Descriptif",
        "Description",
    ),
    Column.UNIT: ("Unit", "Units", "UoM", "Unité", "Unite", "Unités"),
    Column.QTY: (
        "BoQ Qty",
        "BoQ Quantity",
        "Qty",
        "Quantity",
        "Quantité",
        "Quantite",
        "Qté",
        "Qte",
        "Qté BoQ",
    ),
}


class BoqFormatError(ValueError):
    """The input cannot be read as a BoQ: undecodable, empty, malformed or missing a column."""


class MissingColumnsError(BoqFormatError):
    """The header lacks one or more of the five required columns.

    Attributes:
        missing: The canonical English header of each missing column, e.g. ``BoQ Qty``.

    """

    def __init__(self, message: str, missing: tuple[str, ...]) -> None:
        """Keep the message and the missing columns' display names.

        Args:
            message: The error text, as ``BoqFormatError`` carries it.
            missing: The canonical English header of each missing column.

        """
        super().__init__(message)
        self.missing = missing


class EncodingFallbackWarning(UserWarning):
    """The input was not valid UTF-8 and was decoded as cp1252."""


@dataclass(frozen=True)
class RowCells:
    """The canonical cells of one row, used to decide its structure.

    Attributes:
        item_no: Raw item number.
        short: Raw short description.
        long: Raw long description.
        unit: Raw unit.
        qty: Raw quantity.
        all_blank: True when every cell of the row, extras included, is blank.

    """

    item_no: str
    short: str
    long: str
    unit: str
    qty: str
    all_blank: bool


@dataclass(frozen=True)
class PromptFields:
    """A line's text fields capped for the prompt; the line itself is never changed.

    Attributes:
        item_no: Item number, cut to its cap.
        short: Short description, cut to its cap.
        long: Long description, cut to its cap.
        truncated: True when any field was cut, which sets the ``TRUNCATED`` audit flag.

    """

    item_no: str
    short: str
    long: str
    truncated: bool


def read_boq(
    source: Path | str | bytes,
    *,
    header_patterns: Sequence[str] = DEFAULT_HEADER_PATTERNS,
) -> BoqFile:
    """Read a BoQ CSV into raw-string lines with header decisions and section paths.

    Args:
        source: A file path, or the file's bytes.
        header_patterns: Regular expressions; a code fully matching one confirms a header.

    Returns:
        The parsed file.

    Raises:
        BoqFormatError: The input is undecodable, empty, malformed or missing a column.

    """
    text, encoding = _decode(_load_bytes(source))
    delimiter = _sniff_delimiter(text)
    records = _drop_trailing_blank_lines(_parse_records(text, delimiter))
    if not records:
        raise BoqFormatError("the BoQ input is empty: no header row")
    header = tuple(records[0])
    column_map = _map_columns(header)
    rows = [_fit_row(record, len(header), number) for number, record in enumerate(records[1:], 1)]
    lines = _build_lines(header, rows, column_map, header_patterns)
    has_path = any(line.section_path for line in lines)
    return BoqFile(
        lines=tuple(lines),
        header=header,
        encoding=encoding,
        delimiter=delimiter,
        path_mode=PathMode.DERIVED if has_path else PathMode.NONE,
        column_map=column_map,
    )


def capped_for_prompt(line: BoqLine) -> PromptFields:
    """Cap a line's item number and descriptions for the prompt (DESIGN.md §9.1, A51).

    Args:
        line: A parsed line, left unchanged.

    Returns:
        The capped fields and whether any was cut.

    """
    item_no = line.item_no[: FIELD_CAPS[Column.ITEM_NO]]
    short = line.short[: FIELD_CAPS[Column.SHORT]]
    long = line.long[: FIELD_CAPS[Column.LONG]]
    return PromptFields(
        item_no=item_no, short=short, long=long, truncated=bool(oversized_fields(line))
    )


def oversized_fields(line: BoqLine) -> tuple[Column, ...]:
    """List the capped fields of a line that exceed their cap, for the API's 422.

    Args:
        line: A parsed line.

    Returns:
        The over-long columns, in the order item number, short, long.

    """
    values = {Column.ITEM_NO: line.item_no, Column.SHORT: line.short, Column.LONG: line.long}
    return tuple(column for column, cap in FIELD_CAPS.items() if len(values[column]) > cap)


def classify_rows(
    rows: Sequence[RowCells], header_patterns: Sequence[str] = DEFAULT_HEADER_PATTERNS
) -> list[LineKind]:
    """Decide the structural kind of every row (DESIGN.md §9.1, A48).

    A row is a header when Unit and Qty are blank and its code fully matches a pattern or,
    split into segments on ``.``, ``-`` and spaces, is a strict prefix of the next non-blank
    code. Any other non-empty row with blank Unit and Qty, including every such row of a file
    with no codes, is ``HEADER_UNCONFIRMED``: it is never skipped silently (D-03).

    Args:
        rows: Every row of the input, in order.
        header_patterns: Regular expressions confirming a header code.

    Returns:
        One kind per row.

    """
    patterns = [re.compile(pattern) for pattern in header_patterns]
    next_codes = _next_codes(rows)
    return [
        _classify_coded(row, next_code, patterns)
        for row, next_code in zip(rows, next_codes, strict=True)
    ]


def derive_section_paths(
    rows: Sequence[RowCells], row_kinds: Sequence[LineKind]
) -> list[tuple[SectionHeader, ...]]:
    """Derive each row's section path from the header rows of the whole input.

    With codes, the path is the stack of the most recent headers whose code segments prefix the
    row's code. Without codes, unconfirmed rows with a title, no long description and a filled
    row after them serve as context only, their kind unchanged: a run of n of them fills the
    innermost n levels.

    Args:
        rows: Every row of the input, in order.
        row_kinds: The kinds from ``classify_rows``.

    Returns:
        One path per row, outermost header first; empty where none derives.

    """
    if any(_has_code(row) for row in rows):
        return _coded_paths(rows, row_kinds)
    return _codeless_paths(rows, row_kinds, _codeless_context_flags(rows, row_kinds))


def _load_bytes(source: Path | str | bytes) -> bytes:
    """Return the input's bytes, reading the file when given a path."""
    if isinstance(source, bytes):
        return source
    try:
        return Path(source).read_bytes()
    except OSError as error:
        raise BoqFormatError(f"{source}: cannot read the BoQ file ({error})") from error


def _decode(data: bytes) -> tuple[str, str]:
    """Decode UTF-8 (BOM tolerated) first, cp1252 only as a warned fallback."""
    try:
        return data.decode(UTF8_WITH_BOM), UTF8
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode(CP1252)
    except UnicodeDecodeError as error:
        raise BoqFormatError(f"cannot decode the BoQ input as {UTF8} or {CP1252}") from error
    warnings.warn(
        f"BoQ input is not valid {UTF8}; decoded as {CP1252}",
        EncodingFallbackWarning,
        stacklevel=3,
    )
    return text, CP1252


def _sniff_delimiter(text: str) -> str:
    """Pick ``;`` when it splits the header record into more fields than ``,``, else ``,``."""
    widths = {delimiter: len(_first_record(text, delimiter)) for delimiter in (COMMA, SEMICOLON)}
    return SEMICOLON if widths[SEMICOLON] > widths[COMMA] else COMMA


def _first_record(text: str, delimiter: str) -> list[str]:
    """Parse the header record as the csv module does, quoted line breaks included."""
    try:
        return next(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter), [])
    except csv.Error:
        return []


def _parse_records(text: str, delimiter: str) -> list[list[str]]:
    """Split the text into records with the csv module, tolerating stray quotes."""
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as error:
        raise BoqFormatError(f"malformed CSV in the BoQ input: {error}") from error


def _drop_trailing_blank_lines(records: list[list[str]]) -> list[list[str]]:
    """Drop the zero-cell records left by extra line breaks at the end of the input."""
    end = len(records)
    while end and not records[end - 1]:
        end -= 1
    return records[:end]


def _column_key(name: str) -> str:
    """Normalise a column name for alias lookup: NFKC, casefold, no spaces, dots, dashes."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return COLUMN_NAME_NOISE_RE.sub("", folded)


ALIAS_INDEX: Final[Mapping[str, Column]] = {
    _column_key(alias): column
    for column, aliases in COLUMN_ALIASES.items()
    for alias in (*aliases, column.value)
}


def _map_columns(header: Sequence[str]) -> tuple[tuple[Column, str], ...]:
    """Map each required column to exactly one header cell, in canonical order."""
    found: dict[Column, list[str]] = {column: [] for column in Column}
    for name in header:
        column = ALIAS_INDEX.get(_column_key(name))
        if column is not None:
            found[column].append(name)
    missing = [str(column) for column, names in found.items() if not names]
    if missing:
        raise MissingColumnsError(
            f"BoQ input is missing required column(s) {missing}; header is {list(header)}",
            tuple(COLUMN_ALIASES[column][0] for column, names in found.items() if not names),
        )
    ambiguous = {str(column): names for column, names in found.items() if len(names) > 1}
    if ambiguous:
        raise BoqFormatError(f"BoQ input has ambiguous column(s): {ambiguous}")
    return tuple((column, names[0]) for column, names in found.items())


def _fit_row(record: list[str], width: int, number: int) -> tuple[str, ...]:
    """Fit a record to the header's width, keeping the original cells for the writer.

    A zero-cell record (a blank physical line) stays empty. A short record is padded with empty
    cells. Blank surplus cells (trailing delimiters) are dropped; a non-blank one is an error.
    """
    if not record:
        return ()
    if any(not _is_blank(cell) for cell in record[width:]):
        raise BoqFormatError(
            f"row {number} has {len(record)} cells but the header has {width} columns"
        )
    return _padded(record[:width], width)


def _padded(cells: Sequence[str], width: int) -> tuple[str, ...]:
    """Pad cells with empty strings up to the header's width."""
    return (*cells, *([""] * (width - len(cells))))


def _build_lines(
    header: tuple[str, ...],
    rows: Sequence[tuple[str, ...]],
    column_map: tuple[tuple[Column, str], ...],
    header_patterns: Sequence[str],
) -> list[BoqLine]:
    """Assemble the lines, deciding kinds and paths over the whole input first."""
    index = {column: header.index(name) for column, name in column_map}
    extra_indices = [i for i in range(len(header)) if i not in index.values()]
    padded = [_padded(row, len(header)) for row in rows]
    sources = [
        _SourceRow(raw_row=row, extra=tuple((header[i], full[i]) for i in extra_indices))
        for row, full in zip(rows, padded, strict=True)
    ]
    cells = [_row_cells(row, index) for row in padded]
    row_kinds = classify_rows(cells, header_patterns)
    paths = derive_section_paths(cells, row_kinds)
    return [
        _make_line(position, cell, kind, path, source)
        for position, (source, cell, kind, path) in enumerate(
            zip(sources, cells, row_kinds, paths, strict=True)
        )
    ]


@dataclass(frozen=True)
class _SourceRow:
    """The cells of one row that pass through to the writer unchanged.

    Attributes:
        raw_row: The original cells; empty for a blank physical line.
        extra: Pass-through columns as (original column name, raw value).

    """

    raw_row: tuple[str, ...]
    extra: tuple[tuple[str, str], ...]


def _make_line(
    position: int,
    cell: RowCells,
    kind: LineKind,
    path: tuple[SectionHeader, ...],
    source: _SourceRow,
) -> BoqLine:
    """Build one line from its decided cells, kind, path and pass-through cells."""
    return BoqLine(
        position=position,
        line_id=make_line_id(position, cell.item_no, cell.short, cell.long),
        item_no=cell.item_no,
        short=cell.short,
        long=cell.long,
        unit=cell.unit,
        qty=cell.qty,
        kind=kind,
        section_path=path,
        extra=source.extra,
        raw_row=source.raw_row,
    )


def _row_cells(row: tuple[str, ...], index: Mapping[Column, int]) -> RowCells:
    """Pick a row's canonical cells by column index."""
    return RowCells(
        item_no=row[index[Column.ITEM_NO]],
        short=row[index[Column.SHORT]],
        long=row[index[Column.LONG]],
        unit=row[index[Column.UNIT]],
        qty=row[index[Column.QTY]],
        all_blank=all(_is_blank(cell) for cell in row),
    )


def _is_blank(value: str) -> bool:
    """Tell whether a raw cell holds nothing but whitespace; the cell is never changed."""
    return not value.strip()


def _has_code(row: RowCells) -> bool:
    """Tell whether a row carries a non-blank item number."""
    return not _is_blank(row.item_no)


def _unmeasured(row: RowCells) -> bool:
    """Tell whether a row's Unit and Qty are both blank."""
    return _is_blank(row.unit) and _is_blank(row.qty)


def _next_codes(rows: Sequence[RowCells]) -> list[str]:
    """For each row, the code of the next row below it with a non-blank code, or ''."""
    next_codes: list[str] = []
    upcoming = ""
    for row in reversed(rows):
        next_codes.append(upcoming)
        if _has_code(row):
            upcoming = row.item_no
    return next_codes[::-1]


def _classify_coded(row: RowCells, next_code: str, patterns: Sequence[re.Pattern[str]]) -> LineKind:
    """Classify one row of a file that carries codes."""
    if row.all_blank:
        return LineKind.EMPTY_ROW
    if not _unmeasured(row):
        return LineKind.ITEM
    if not _has_code(row):
        return LineKind.HEADER_UNCONFIRMED
    if any(pattern.fullmatch(row.item_no) for pattern in patterns):
        return LineKind.HEADER
    code = _code_segments(row.item_no)
    if code and _is_strict_prefix(code, _code_segments(next_code)):
        return LineKind.HEADER
    return LineKind.HEADER_UNCONFIRMED


def _codeless_context_flags(rows: Sequence[RowCells], row_kinds: Sequence[LineKind]) -> list[bool]:
    """Mark the code-less rows used as section context; their kind is never changed."""
    filled = [i for i, kind in enumerate(row_kinds) if kind is not LineKind.EMPTY_ROW]
    last_filled = filled[-1] if filled else -1
    return [
        kind is LineKind.HEADER_UNCONFIRMED and _is_context_title(row) and position < last_filled
        for position, (row, kind) in enumerate(zip(rows, row_kinds, strict=True))
    ]


def _is_context_title(row: RowCells) -> bool:
    """Tell whether an unmeasured, code-less row has a title and no long description."""
    return not _is_blank(row.short) and _is_blank(row.long)


def _code_segments(code: str) -> tuple[str, ...]:
    """Split a code on ``.``, ``-`` and spaces; numeric segments lose leading zeros."""
    parts = (part for part in CODE_SEPARATORS_RE.split(code) if part)
    return tuple(str(int(part)) if part.isdecimal() else part.casefold() for part in parts)


def _is_strict_prefix(outer: tuple[str, ...], inner: tuple[str, ...]) -> bool:
    """Tell whether one segment tuple is a strict prefix of another."""
    return len(outer) < len(inner) and inner[: len(outer)] == outer


def _coded_paths(
    rows: Sequence[RowCells], row_kinds: Sequence[LineKind]
) -> list[tuple[SectionHeader, ...]]:
    """Section paths for a file with codes, from a stack of prefixing headers."""
    stack: list[tuple[tuple[str, ...], SectionHeader]] = []
    paths: list[tuple[SectionHeader, ...]] = []
    for row, kind in zip(rows, row_kinds, strict=True):
        segments = _code_segments(row.item_no)
        enclosing = [entry for entry in stack if _is_strict_prefix(entry[0], segments)]
        paths.append(tuple(header for _, header in enclosing))
        if kind is LineKind.HEADER:
            stack = [*enclosing, (segments, SectionHeader(row.item_no, row.short))]
    return paths


def _header_runs(row_kinds: Sequence[LineKind], is_context: Sequence[bool]) -> Iterator[int]:
    """Yield, for each row, the length of the context-header run it starts, or 0.

    Empty rows are transparent: they neither break nor extend a run.
    """
    filled = [i for i, kind in enumerate(row_kinds) if kind is not LineKind.EMPTY_ROW]
    run_length = [0] * len(row_kinds)
    run_start = -1
    for previous, position in zip([-1, *filled], filled, strict=False):
        if not is_context[position]:
            run_start = -1
            continue
        starts = previous < 0 or not is_context[previous]
        run_start = position if starts else run_start
        run_length[run_start] += 1
    yield from run_length


def _codeless_paths(
    rows: Sequence[RowCells], row_kinds: Sequence[LineKind], is_context: Sequence[bool]
) -> list[tuple[SectionHeader, ...]]:
    """Section paths for a code-less file: a run of n context rows fills the innermost n levels."""
    runs = list(_header_runs(row_kinds, is_context))
    depth = max(runs, default=0)
    stack: list[SectionHeader] = []
    paths: list[tuple[SectionHeader, ...]] = []
    for row, kind, run, context in zip(rows, row_kinds, runs, is_context, strict=True):
        if run:
            stack = stack[: max(depth - run, 0)]
        paths.append(() if kind is LineKind.EMPTY_ROW else tuple(stack))
        if context:
            stack.append(SectionHeader(row.item_no, row.short))
    return paths
