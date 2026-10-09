"""Upload reader: a BoQ as ``.csv`` or ``.xlsx`` bytes, parsed exactly as the CSV reader does.

An ``.xlsx`` is opened with openpyxl in read-only, values-only mode (defusedxml, from the
``[xlsx]`` extra, parses the workbook XML safely): the first visible sheet is read, formulas
and macros never are, and each cell becomes the string Excel displays for it, a numeric item
code through its ``number_format`` so ``09.01`` keeps its zero. The rows are then written as
CSV and handed to ``read_boq``, so header decisions, section paths and line ids are those of
the same file saved as CSV (docs/ui-spec.md §4, U1). ``.xlsm`` and ``.xls`` are refused.

Before openpyxl reads anything, the archive's declared sizes are checked: a workbook that would
inflate past 50 MB, or a large member that inflates more than 100-fold, is refused, so a small
upload cannot expand into gigabytes of XML (a zip bomb). openpyxl falls back to the standard
library's XML parser when defusedxml is missing, so a workbook is refused unless openpyxl reports
that it parses with defusedxml.
"""

import csv
import io
import re
import warnings
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import PurePath
from typing import Any, Final

from oris_matcher.domain.boq import BoqFile
from oris_matcher.io.boq_reader import BoqFormatError, EncodingFallbackWarning, read_boq

CSV_SUFFIX: Final = ".csv"
XLSX_SUFFIX: Final = ".xlsx"
SUPPORTED_SUFFIXES: Final = (CSV_SUFFIX, XLSX_SUFFIX)
ROW_ENCODING: Final = "utf-8"
ROW_TERMINATOR: Final = "\r\n"
MAX_COLUMNS: Final = 64
MAX_SCANNED_ROWS: Final = 100_000
MAX_XLSX_UNCOMPRESSED_BYTES: Final = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO: Final = 100
RATIO_CHECK_MIN_BYTES: Final = 1024 * 1024
DEFUSEDXML_NEEDED: Final = "reading .xlsx needs defusedxml: uv sync --extra xlsx"
VISIBLE: Final = "visible"
GENERAL_FORMATS: Final = frozenset({"general", "@", ""})
TRUE_TEXT: Final = "TRUE"
FALSE_TEXT: Final = "FALSE"
DECIMAL_POINT: Final = "."
ZERO: Final = "0"
NUMBER_FORMAT_RE: Final = re.compile(r"^(?P<whole>[#0,]*0)(?:\.(?P<fraction>[0#]+))?$")


class UnsupportedTypeError(BoqFormatError):
    """The upload is neither a ``.csv`` nor an ``.xlsx`` file."""


class EmptyBoqError(BoqFormatError):
    """The upload holds no header row, or a header and no data row."""


class TooManyRowsError(BoqFormatError):
    """The upload holds more data rows than the limit."""


class WorkbookTooLargeError(BoqFormatError):
    """The workbook would inflate past the size cap, or one member inflates suspiciously far."""


@dataclass(frozen=True)
class UploadedBoq:
    """A parsed upload.

    Attributes:
        boq: The parsed BoQ.
        warnings: What the reader warned about, e.g. a cp1252 fallback.

    """

    boq: BoqFile
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _NumberFormat:
    """A plain ``0``/``#`` number format: integer digits, grouping and decimals."""

    min_whole: int
    grouped: bool
    min_fraction: int
    max_fraction: int


def read_upload(filename: str, data: bytes, *, max_rows: int | None = None) -> UploadedBoq:
    """Parse an uploaded BoQ by its extension.

    Args:
        filename: The uploaded file's name; only its extension is used.
        data: The file's bytes.
        max_rows: Refuse more data rows than this; None for no limit.

    Returns:
        The parsed BoQ and the reader's warnings.

    Raises:
        UnsupportedTypeError: The extension is not ``.csv`` or ``.xlsx``.
        EmptyBoqError: No header row, or no data row.
        TooManyRowsError: More data rows than ``max_rows``.
        BoqFormatError: The file cannot be read as a BoQ.

    """
    suffix = PurePath(filename).suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedTypeError(
            f"unsupported file type {suffix or '(none)'!r}: upload a .csv or .xlsx file"
        )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", EncodingFallbackWarning)
        boq = _read_csv(data) if suffix == CSV_SUFFIX else read_xlsx(data, max_rows=max_rows)
    _check_rows(boq, max_rows)
    notes = tuple(
        str(warning.message)
        for warning in caught
        if issubclass(warning.category, EncodingFallbackWarning)
    )
    return UploadedBoq(boq, notes)


def read_xlsx(data: bytes, *, max_rows: int | None = None) -> BoqFile:
    """Read the first visible sheet of an ``.xlsx`` as a BoQ.

    Args:
        data: The workbook's bytes.
        max_rows: Stop and refuse once more data rows than this are found; None for no limit.

    Returns:
        The BoQ ``read_boq`` gives for the same cells saved as CSV.

    Raises:
        EmptyBoqError: The sheet holds no row.
        TooManyRowsError: More data rows than ``max_rows``, or a sheet too long to scan.
        BoqFormatError: The bytes are not a readable workbook, or the extra is not installed.

    """
    workbook = _open_workbook(data)
    try:
        records = _records(_first_visible_sheet(workbook), max_rows)
    finally:
        workbook.close()
    if not records:
        raise EmptyBoqError("the BoQ input is empty: no header row")
    return read_boq(_csv_bytes(records))


def render_cell(value: object, number_format: str | None) -> str:
    """Render one cell value as Excel displays it, for the cells a BoQ holds.

    Args:
        value: The cell's stored value.
        number_format: The cell's number format, e.g. ``General`` or ``00.00``.

    Returns:
        The text: numbers through a plain ``0``/``#`` format (else General), dates in ISO
        form, booleans as ``TRUE``/``FALSE``, nothing as an empty string.

    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return TRUE_TEXT if value else FALSE_TEXT
    if isinstance(value, datetime | date | time):
        return _render_temporal(value)
    if isinstance(value, int | float):
        return _render_number(value, number_format or "")
    return str(value)


def _read_csv(data: bytes) -> BoqFile:
    """Read CSV bytes, refusing a blank file as empty rather than as malformed."""
    if not data.strip():
        raise EmptyBoqError("the BoQ input is empty: no header row")
    return read_boq(data)


def _check_rows(boq: BoqFile, max_rows: int | None) -> None:
    """Refuse a BoQ with no data row, or with more than ``max_rows``."""
    if not boq.lines:
        raise EmptyBoqError("the BoQ input has a header row and no data row")
    if max_rows is not None and len(boq.lines) > max_rows:
        raise TooManyRowsError(f"{len(boq.lines)} data rows; the limit is {max_rows}")


def check_inflation(data: bytes) -> None:
    """Refuse a workbook whose members would inflate past the caps, before any is read.

    Args:
        data: The workbook's bytes.

    Raises:
        WorkbookTooLargeError: The declared uncompressed total is over
            ``MAX_XLSX_UNCOMPRESSED_BYTES``, or a member over ``RATIO_CHECK_MIN_BYTES`` inflates
            more than ``MAX_COMPRESSION_RATIO``-fold.
        BoqFormatError: The bytes are not a zip archive.

    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        raise BoqFormatError(f"cannot read the workbook: {error}") from error
    total = sum(member.file_size for member in members)
    if total > MAX_XLSX_UNCOMPRESSED_BYTES:
        raise WorkbookTooLargeError(
            f"the workbook inflates to {total} bytes; the limit is {MAX_XLSX_UNCOMPRESSED_BYTES}"
        )
    for member in members:
        if member.file_size > RATIO_CHECK_MIN_BYTES and member.file_size > (
            MAX_COMPRESSION_RATIO * max(member.compress_size, 1)
        ):
            raise WorkbookTooLargeError(
                f"{member.filename} inflates more than {MAX_COMPRESSION_RATIO}-fold"
            )


def _openpyxl() -> tuple[Any, type[Exception]]:
    """Import openpyxl, refusing when it is missing or parses without defusedxml."""
    try:
        import openpyxl  # type: ignore[import-untyped]  # noqa: PLC0415
        from openpyxl import xml as openpyxl_xml  # noqa: PLC0415
        from openpyxl.utils.exceptions import (  # type: ignore[import-untyped]  # noqa: PLC0415
            InvalidFileException,
        )
    except ImportError as error:
        raise UnsupportedTypeError(
            "reading .xlsx needs the [xlsx] extra: uv sync --extra xlsx"
        ) from error
    if not getattr(openpyxl_xml, "DEFUSEDXML", False):
        raise UnsupportedTypeError(DEFUSEDXML_NEEDED)
    return openpyxl, InvalidFileException


def _open_workbook(data: bytes) -> Any:
    """Open workbook bytes read-only and values-only, after the inflation check."""
    openpyxl, invalid_file = _openpyxl()
    check_inflation(data)
    unreadable = (zipfile.BadZipFile, invalid_file, KeyError, OSError, ValueError)
    try:
        return openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except unreadable as error:
        raise BoqFormatError(f"cannot read the workbook: {error}") from error


def _first_visible_sheet(workbook: Any) -> Any:
    """Return the first worksheet whose state is visible."""
    for sheet in workbook.worksheets:
        if getattr(sheet, "sheet_state", VISIBLE) == VISIBLE:
            return sheet
    raise BoqFormatError("the workbook has no visible sheet")


def _records(sheet: Any, max_rows: int | None) -> list[list[str]]:
    """Read the sheet's rows as CSV records, dropping trailing blank rows.

    A blank row in the middle is kept, as a blank CSV line is; blank rows are only counted until
    a filled row follows, so a sheet formatted far below its data costs no memory.
    """
    records: list[list[str]] = []
    pending_blank = 0
    for scanned, record in enumerate(_rendered_rows(sheet), 1):
        if scanned > MAX_SCANNED_ROWS:
            raise TooManyRowsError(f"the sheet has more than {MAX_SCANNED_ROWS} rows")
        if not record:
            pending_blank += 1
            continue
        records += [[] for _ in range(pending_blank)] + [record]
        pending_blank = 0
        if max_rows is not None and len(records) - 1 > max_rows:
            raise TooManyRowsError(f"more than {max_rows} data rows; the limit is {max_rows}")
    return records


def _rendered_rows(sheet: Any) -> Iterator[list[str]]:
    """Yield each row's rendered cells, without trailing empty cells."""
    for row in sheet.iter_rows(max_col=MAX_COLUMNS):
        cells = [render_cell(cell.value, getattr(cell, "number_format", None)) for cell in row]
        while cells and not cells[-1]:
            cells.pop()
        yield cells


def _csv_bytes(records: Iterable[list[str]]) -> bytes:
    """Write records as UTF-8 CSV, the form ``read_boq`` reads."""
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator=ROW_TERMINATOR).writerows(records)
    return buffer.getvalue().encode(ROW_ENCODING)


def _render_temporal(value: datetime | date | time) -> str:
    """Render a date as ``YYYY-MM-DD`` and a date-time with its time only when it has one."""
    if isinstance(value, datetime) and value.time() == time():
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value.isoformat()


def _render_number(value: float, number_format: str) -> str:
    """Render a number through a plain ``0``/``#`` format, else as Excel's General does."""
    parsed = _parse_format(number_format)
    if parsed is None:
        return _general(value)
    grouping = "," if parsed.grouped else ""
    text = f"{abs(value):{grouping}.{parsed.max_fraction}f}"
    whole, _, fraction = text.partition(DECIMAL_POINT)
    whole = whole.rjust(parsed.min_whole, ZERO)
    fraction = fraction.rstrip(ZERO).ljust(parsed.min_fraction, ZERO)
    sign = "-" if value < 0 else ""
    return f"{sign}{whole}{DECIMAL_POINT}{fraction}" if fraction else f"{sign}{whole}"


def _parse_format(number_format: str) -> _NumberFormat | None:
    """Parse the first section of a plain numeric format; None for General or anything else."""
    section = number_format.split(";", 1)[0].strip()
    if section.casefold() in GENERAL_FORMATS:
        return None
    match = NUMBER_FORMAT_RE.fullmatch(section)
    if match is None:
        return None
    whole, fraction = match["whole"], match["fraction"] or ""
    return _NumberFormat(
        min_whole=whole.count(ZERO),
        grouped="," in whole,
        min_fraction=fraction.count(ZERO),
        max_fraction=len(fraction),
    )


def _general(value: float) -> str:
    """Render a number as General does for BoQ values: integral floats lose their ``.0``."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
