r"""Render ``eval/experiments.md`` from ``eval/experiments.jsonl`` (DESIGN.md §7.2).

The JSONL ledger is the record; the Markdown table is a view of it, rewritten whole from the
ledger so it never drifts. One table row per ledger row, in file order. The output is
byte-stable: UTF-8, LF line ends, and nothing taken from the clock or the host.

Usage::

    python eval/render_ledger.py [--ledger eval/experiments.jsonl] [--output eval/experiments.md]

Exit codes: 0 ok, 2 the ledger cannot be read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "eval" / "experiments.jsonl"
DEFAULT_OUTPUT = ROOT / "eval" / "experiments.md"
ENCODING = "utf-8"
NEWLINE = "\n"
EXIT_OK = 0
EXIT_ERROR = 2
DASH = "—"
NOT_AVAILABLE = "n/a"
TEMP_SUFFIX = ".tmp"
CELL_SEPARATOR = " | "
PIPE = "|"
ESCAPED_PIPE = "\\|"
DIGITS = 3
LINES_PER_COST_UNIT = 100
DATE_FORMAT = "%Y-%m-%d %H:%M UTC"
KEPT_LABELS = {True: "kept", False: "reverted"}
P_VALUE_FORMAT = ".3g"
HEADER = (
    "# Experiment ledger",
    "",
    "This ledger is append-only, and every attempt gets one row, whether it was kept or "
    "reverted. The keep rule is pre-registered in `DESIGN.md` §7.2. All experiments run on "
    "**dev only**.",
    "",
    "| id | date | hypothesis | cause targeted | change "
    "| dev before (P EN/FR · C₂₅₂ · F_NM · $/100) | dev after | kept / reverted "
    "| prompt_version |",
    "|---|---|---|---|---|---|---|---|---|",
)


def escape(value: object) -> str:
    """Return a table cell: ``—`` for null or empty, newlines folded, ``|`` escaped."""
    if value is None:
        return DASH
    text = " ".join(str(value).split())
    return text.replace(PIPE, ESCAPED_PIPE) if text else DASH


def _number(value: object) -> str:
    """Format a proportion or a dollar amount, ``n/a`` when absent or not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return NOT_AVAILABLE
    return f"{value:.{DIGITS}f}"


def _nested_value(metrics: Mapping[str, Any], key: str) -> Any:
    """Return ``metrics[key]["value"]``, or None when either level is not a mapping."""
    nested = metrics.get(key)
    return nested.get("value") if isinstance(nested, Mapping) else None


def metrics_cell(lang: str, metrics: object) -> str:
    """Return ``P <LANG> · C · F_NM · $/100`` from one row's metrics, ``—`` when absent.

    Args:
        lang: The row's language.
        metrics: The row's ``metrics`` object.

    Returns:
        The cell text, before escaping.

    """
    if not isinstance(metrics, Mapping) or not metrics:
        return DASH
    mean_cost = metrics.get("mean_cost_usd")
    per_100 = None
    if isinstance(mean_cost, int | float) and not isinstance(mean_cost, bool):
        per_100 = mean_cost * LINES_PER_COST_UNIT
    parts = (
        f"P {lang.upper()} {_number(_nested_value(metrics, 'precision'))}",
        f"C {_number(_nested_value(metrics, 'coverage_labelled'))}",
        f"F_NM {metrics.get('false_not_a_material', NOT_AVAILABLE)}",
        f"${_number(per_100)}/100",
    )
    return " · ".join(parts)


def date_cell(value: object) -> str:
    """Render an ISO timestamp as ``YYYY-MM-DD HH:MM UTC``; other values are kept as they are."""
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return escape(value)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.strftime(DATE_FORMAT)


def kept_cell(value: object) -> str:
    """Render the keep verdict: ``kept``, ``reverted``, or the label as written (``baseline``)."""
    if isinstance(value, bool):
        return KEPT_LABELS[value]
    return escape(value)


def verdict_cell(row: Mapping[str, Any]) -> str:
    """Render the verdict with d and the one-sided sign-test p when the row was compared (§7.2)."""
    verdict = kept_cell(row.get("kept"))
    discordant = row.get("d")
    if discordant is None:
        return verdict
    return f"{verdict} (d={discordant}, p={row.get('sign_test_p'):{P_VALUE_FORMAT}})"


def prompt_cell(value: object) -> str:
    """Render the prompt version(s) a row ran, comma-separated."""
    if isinstance(value, list):
        return escape(", ".join(str(item) for item in value))
    return escape(value)


def before_metrics(row: Mapping[str, Any], earlier: Sequence[Mapping[str, Any]]) -> str:
    """Return the dev-before cell: the metrics of the baseline run this row compared against.

    Args:
        row: The ledger row.
        earlier: The ledger rows above it, oldest first.

    Returns:
        The cell text; ``—`` when the row has no baseline or its run is not in the ledger.

    """
    before = row.get("before")
    if not isinstance(before, Mapping):
        return DASH
    found = [other for other in earlier if other.get("run_id") == before.get("run_id")]
    if not found:
        return DASH
    baseline = found[-1]
    return metrics_cell(str(baseline.get("lang", "")), baseline.get("metrics"))


def table_row(row: Mapping[str, Any], earlier: Sequence[Mapping[str, Any]]) -> str:
    """Return one Markdown table row for one ledger row.

    Args:
        row: The ledger row.
        earlier: The ledger rows above it, oldest first, for the baseline's metrics.

    Returns:
        The row, without its line end.

    """
    lang = str(row.get("lang", ""))
    cells = (
        escape(row.get("id")),
        date_cell(row.get("date")),
        escape(row.get("hypothesis")),
        escape(row.get("cause_targeted")),
        escape(row.get("change")),
        escape(before_metrics(row, earlier)),
        escape(metrics_cell(lang, row.get("metrics"))),
        verdict_cell(row),
        prompt_cell(row.get("prompt_version")),
    )
    return f"| {CELL_SEPARATOR.join(cells)} |"


def render(rows: Sequence[Mapping[str, Any]]) -> str:
    """Render the whole Markdown ledger from the JSONL rows, in file order.

    Args:
        rows: The ledger rows, oldest first.

    Returns:
        The file's text, LF line ends, ending with one line end.

    """
    lines = [*HEADER, *(table_row(row, rows[:index]) for index, row in enumerate(rows))]
    return NEWLINE.join(lines) + NEWLINE


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Read the JSONL ledger's rows, oldest first; an absent ledger has none.

    Args:
        path: The JSONL ledger.

    Returns:
        The rows.

    Raises:
        ValueError: A line is not valid JSON or not a JSON object.

    """
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding=ENCODING).splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path.name} line {number}: not a JSON object")
        rows.append(row)
    return rows


def write(ledger: Path, output: Path) -> None:
    """Render the ledger and write the Markdown file as UTF-8 bytes with LF line ends.

    The text goes to a sibling temp file first, then replaces the output in one step, so a
    crash never leaves a truncated table.

    Args:
        ledger: The JSONL ledger.
        output: The Markdown file, rewritten whole.

    """
    data = render(read_rows(ledger)).encode(ENCODING)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + TEMP_SUFFIX)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Render the ledger; return the exit code.

    Args:
        argv: Arguments; ``sys.argv[1:]`` when None.

    Returns:
        0 ok, 2 the ledger cannot be read or parsed.

    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        write(args.ledger, args.output)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(f"rendered {args.output} from {args.ledger}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
