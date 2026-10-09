"""Output CSV writer: input columns unchanged, then decision and audit columns (DESIGN.md §9.6).

Written with the ``csv`` module: UTF-8 without a BOM (``excel_bom`` adds one, and the caller
records it), CRLF line endings, QUOTE_MINIMAL. The input cells are written as the raw
strings the reader kept, in input order. Canonical labels are filled on matched rows only;
suggestions sit in their own columns (D-15), followed by the A56 second suggestion.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from pathlib import Path

from oris_matcher.domain.decision import Decision
from oris_matcher.domain.library import LibraryRow
from oris_matcher.service import LineResult, RunResult

OUTPUT_ENCODING = "utf-8"
EXCEL_BOM = "﻿"
LINE_TERMINATOR = "\r\n"
CALL_ID_SEPARATOR = ";"
COST_FORMAT = "{:.6f}"
OUTPUT_COLUMNS: tuple[str, ...] = (
    "decision",
    "material_type",
    "material_usage",
    "material_subtype",
    "reason",
    "model",
    "prompt_version",
    "latency_ms",
    "cost_usd",
    "suggested_type",
    "suggested_usage",
    "suggested_subtype",
    "library_row_id",
    "call_ids",
    "suggested2_type",
    "suggested2_usage",
    "suggested2_subtype",
)
BLANK_TRIPLE = ("", "", "")


def output_header(input_header: Sequence[str]) -> tuple[str, ...]:
    """Return the output header: the input's columns unchanged, then ``OUTPUT_COLUMNS``.

    Args:
        input_header: The input's column names, in order.

    Returns:
        The output column names.

    """
    return (*input_header, *OUTPUT_COLUMNS)


def _triple(row: LibraryRow | None) -> tuple[str, str, str]:
    """Return a row's three library strings verbatim, or blanks."""
    if row is None:
        return BLANK_TRIPLE
    return row.material_type, row.material_usage, row.material_subtype


def output_row(item: LineResult) -> tuple[str, ...]:
    """Return one output row: the raw input cells, then the §9.6 and A56 columns.

    Args:
        item: One line's result.

    Returns:
        The row's cells.

    """
    decision = item.decision
    labels = _triple(decision.row) if decision.decision == Decision.MATCHED else BLANK_TRIPLE
    return (
        *item.line.raw_row,
        decision.decision.value,
        *labels,
        decision.reason,
        item.model,
        item.prompt_version,
        str(item.latency_ms),
        COST_FORMAT.format(item.cost_usd),
        *_triple(item.suggested),
        item.suggested.row_id if item.suggested else "",
        CALL_ID_SEPARATOR.join(item.call_ids),
        *_triple(item.suggested2),
    )


def render_rows(header: Sequence[str], rows: Iterable[Sequence[str]], *, excel_bom: bool) -> bytes:
    """Serialise a header and rows as the output CSV bytes.

    Args:
        header: The output column names.
        rows: The rows' cells.
        excel_bom: Prefix a UTF-8 BOM for Excel.

    Returns:
        The encoded CSV.

    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator=LINE_TERMINATOR, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    writer.writerows(rows)
    prefix = EXCEL_BOM if excel_bom else ""
    return (prefix + buffer.getvalue()).encode(OUTPUT_ENCODING)


def render_csv(result: RunResult, *, excel_bom: bool = False) -> bytes:
    """Serialise a run's output, one row per output line in input order.

    Args:
        result: The run.
        excel_bom: Prefix a UTF-8 BOM for Excel; off by default.

    Returns:
        The encoded CSV.

    """
    rows = (output_row(item) for item in result.lines)
    return render_rows(output_header(result.input_header), rows, excel_bom=excel_bom)


def write_csv(path: Path, result: RunResult, *, excel_bom: bool = False) -> None:
    """Write a run's output CSV.

    Args:
        path: The output file.
        result: The run.
        excel_bom: Prefix a UTF-8 BOM for Excel; off by default.

    """
    path.write_bytes(render_csv(result, excel_bom=excel_bom))
