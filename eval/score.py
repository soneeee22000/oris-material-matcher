"""Score a matcher output CSV against any reference CSV that has the three label columns.

Standard library only, and nothing is imported from ``src/`` (DESIGN.md §10.1; evaluation
protocol §1, §3 and §8). The headline compares exact raw strings; a lenient line
(NFC + trim + whitespace collapse) is always printed next to it and never changes it.

Usage::

    python eval/score.py --output OUT.csv --reference REF.csv [--label en] [--key COL]
        [--strict] [--join key|row-order] [--split eval/split_v1.json --side dev|lockbox|all]
        [--classes eval/annotations/blank_line_classes.csv] [--json report.json]

Repeat the ``--output``/``--label`` pair to score several outputs (EN and FR) side by side.
Exit code 0 on success and 2 on input that cannot be scored (or any ``--strict`` violation).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENCODING = "utf-8-sig"
EXIT_OK = 0
EXIT_ERROR = 2
CONFIDENCE_ALPHA = 0.05
BISECTION_STEPS = 100
WILSON_Z = 1.96
DIGITS = 3
COST_DIGITS = 6
LATENCY_DIGITS = 1
LIST_LIMIT = 10

MATCHED = "matched"
NOT_A_MATERIAL = "not_a_material"
NEEDS_REVIEW = "needs_review"
DECISIONS = (MATCHED, NOT_A_MATERIAL, NEEDS_REVIEW)
MISSING = "missing"
INVALID = "invalid"

JOIN_KEY = "key"
JOIN_ROW_ORDER = "row-order"
SIDE_ALL = "all"
SPLIT_FIELDS = {"dev": "item_ids_dev", "lockbox": "item_ids_lockbox"}

NAN_LITERAL = re.compile(r"^(nan|NaN|None|NULL|<NA>|N/A)$")
ITEM_KEY = re.compile(r"^\d{2}\.\d{2}\.\d{4}\.$")
WHITESPACE = re.compile(r"\s+")
HEADER_NOISE = re.compile(r"[\s_\-]+")

KEY_ALIASES = ("itemno.", "itemno", "n°article", "noarticle")
TYPE_ALIASES = ("materialtype", "type")
USAGE_ALIASES = ("materialusage", "usage")
SUBTYPE_ALIASES = ("materialsubtype", "subtype")
SUGGESTED_ALIASES = (("suggestedtype",), ("suggestedusage",), ("suggestedsubtype",))
SUGGESTED_2_ALIASES = (("suggested2type",), ("suggested2usage",), ("suggested2subtype",))
DECISION_ALIASES = ("decision",)
UNIT_ALIASES = ("unit", "unité", "unite")
COST_ALIASES = ("costusd",)
LATENCY_ALIASES = ("latencyms",)
CLASS_ALIASES = ("class",)

CLASS_HEADER = "H"
CLASS_LABELLED = "L"
CLASS_NO_EQUIVALENT = "E"
CLASS_BLANK = "blank"
CLASS_UNCLASSED = "U"
NON_ITEM_CLASSES = (CLASS_HEADER, CLASS_UNCLASSED)
F_NM_LABELLED = "labelled"
F_NM_MATERIAL = "labelled+no_equivalent"
HIT_2_NOTE = "no second-suggestion columns in the §9.6 shape"
CLASS_CODES = {"material_no_equivalent": "E", "service": "S", "ambiguous": "A"}

Triple = tuple[str, str, str]
BLANK: Triple = ("", "", "")
LEVELS = ("type", "type_usage", "triple")


class ScoreError(Exception):
    """Input that cannot be scored, or a ``--strict`` violation."""


def normalize_header(name: str) -> str:
    """Return a column name folded for alias lookup (case, spaces, ``_`` and ``-`` ignored)."""
    return HEADER_NOISE.sub("", unicodedata.normalize("NFC", name).casefold())


@dataclass(frozen=True)
class Table:
    """A CSV file read as raw strings.

    Attributes:
        path: Where the file was read from.
        header: The header row, verbatim.
        rows: The data rows, every cell a raw string.

    """

    path: Path
    header: list[str]
    rows: list[list[str]]

    def column(self, aliases: Sequence[str]) -> int | None:
        """Return the index of the first column whose folded name is in ``aliases``."""
        folded = [normalize_header(name) for name in self.header]
        for alias in aliases:
            if alias in folded:
                return folded.index(alias)
        return None


def cell(row: Sequence[str], index: int | None) -> str:
    """Return the raw cell at ``index``, or ``''`` when the column or cell is absent."""
    if index is None or index >= len(row):
        return ""
    return row[index]


def read_table(path: Path) -> Table:
    """Read a CSV with the ``csv`` module, with no NA inference and no stripping."""
    try:
        with path.open(encoding=ENCODING, newline="") as handle:
            records = list(csv.reader(handle))
    except (OSError, UnicodeDecodeError) as error:
        raise ScoreError(f"cannot read {path}: {error}") from error
    if not records:
        raise ScoreError(f"{path} is empty")
    return Table(path=path, header=records[0], rows=records[1:])


def resolve_key(table: Table, override: str | None) -> int | None:
    """Find the key column: ``--key`` when the file has it, else the aliases."""
    if override is not None:
        index = table.column((normalize_header(override),))
        if index is not None:
            return index
    return table.column(KEY_ALIASES)


def resolve_triple(
    table: Table, aliases: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]
) -> tuple[int, int, int] | None:
    """Return the indices of a type/usage/subtype column triple, or None if any is absent."""
    found = [table.column(names) for names in aliases]
    if any(index is None for index in found):
        return None
    return (int(found[0] or 0), int(found[1] or 0), int(found[2] or 0))


def triple_at(row: Sequence[str], indices: tuple[int, int, int]) -> Triple:
    """Return the three raw cells at ``indices``."""
    return (cell(row, indices[0]), cell(row, indices[1]), cell(row, indices[2]))


# --- reference and output rows ---------------------------------------------------------


@dataclass(frozen=True)
class RefRow:
    """One reference row.

    Attributes:
        key: Raw key cell, or None when the reference has no key column.
        labels: The three labels, with ``nan``-like literals blanked.
        unit: Raw unit cell, or None when the reference has no unit column.

    """

    key: str | None
    labels: Triple
    unit: str | None = None


@dataclass
class Reference:
    """The parsed reference file.

    Attributes:
        rows: Reference rows in file order.
        nan_literals: Label cells that matched the ``nan``-like pattern and were blanked.
        partial: Rows with some but not all labels filled.
        has_key: Whether a key column was found.
        has_unit: Whether a unit column was found.

    """

    rows: list[RefRow]
    nan_literals: int
    partial: int
    has_key: bool
    has_unit: bool = False


def blank_nan(labels: Triple) -> tuple[Triple, int]:
    """Blank ``nan``-like literal label cells; return the labels and how many were blanked."""
    cleaned = tuple("" if NAN_LITERAL.match(value) else value for value in labels)
    blanked = sum(1 for before, after in zip(labels, cleaned, strict=True) if before != after)
    return (cleaned[0], cleaned[1], cleaned[2]), blanked


def parse_reference(table: Table, key_override: str | None, need_key: bool) -> Reference:
    """Parse the reference file; raise when its label columns (or a needed key) are absent."""
    labels_at = resolve_triple(table, (TYPE_ALIASES, USAGE_ALIASES, SUBTYPE_ALIASES))
    if labels_at is None:
        raise ScoreError(f"{table.path}: the three label columns were not found")
    key_at = resolve_key(table, key_override)
    if key_at is None and need_key:
        raise ScoreError(
            f"{table.path}: no key column (tried {', '.join(KEY_ALIASES)}); "
            "pass --key COL or --join row-order"
        )
    unit_at = table.column(UNIT_ALIASES)
    rows: list[RefRow] = []
    nan_total = 0
    partial = 0
    for raw in table.rows:
        labels, blanked = blank_nan(triple_at(raw, labels_at))
        nan_total += blanked
        partial += int(labels != BLANK and "" in labels[:2])
        key = None if key_at is None else cell(raw, key_at)
        unit = None if unit_at is None else cell(raw, unit_at)
        rows.append(RefRow(key=key, labels=labels, unit=unit))
    return Reference(
        rows=rows,
        nan_literals=nan_total,
        partial=partial,
        has_key=key_at is not None,
        has_unit=unit_at is not None,
    )


@dataclass(frozen=True)
class OutRow:
    """One output row.

    Attributes:
        key: Raw key cell, or None when the output has no key column.
        decision: Raw decision cell.
        labels: The three label cells, raw.
        suggested: The top-1 suggestion, or None when the columns are absent.
        suggested_2: The top-2 suggestion, or None when the columns are absent.
        unit: Raw unit cell, or None when the column is absent.
        cost: Parsed ``cost_usd``, or None.
        latency: Parsed ``latency_ms``, or None.

    """

    key: str | None
    decision: str
    labels: Triple
    suggested: Triple | None
    suggested_2: Triple | None
    unit: str | None
    cost: float | None
    latency: float | None


@dataclass(frozen=True)
class Output:
    """The parsed output file and which optional columns it has.

    Attributes:
        rows: Output rows in file order.
        has_key: Whether a key column was found.
        has_suggested: Whether the ``suggested_*`` columns exist.
        has_suggested_2: Whether the ``suggested_2_*`` columns exist.
        has_cost: Whether ``cost_usd`` exists.
        has_latency: Whether ``latency_ms`` exists.
        nan_literals: Label and suggestion cells matching the ``nan``-like pattern, blanked.
        has_unit: Whether a unit column exists (read only for the hostile cross-check).

    """

    rows: list[OutRow]
    has_key: bool
    has_suggested: bool
    has_suggested_2: bool
    has_cost: bool
    has_latency: bool
    nan_literals: int
    has_unit: bool = False


def parse_number(text: str) -> float | None:
    """Parse a numeric cell; blank or malformed cells give None."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


@dataclass(frozen=True)
class OutputColumns:
    """Column indices of an output file (None when absent).

    Attributes:
        key: Key column.
        decision: Decision column.
        labels: The three label columns.
        suggested: The three ``suggested_*`` columns.
        suggested_2: The three ``suggested_2_*`` columns.
        unit: Unit column.
        cost: ``cost_usd`` column.
        latency: ``latency_ms`` column.

    """

    key: int | None
    decision: int
    labels: tuple[int, int, int]
    suggested: tuple[int, int, int] | None
    suggested_2: tuple[int, int, int] | None
    unit: int | None
    cost: int | None
    latency: int | None


def output_columns(table: Table, key_override: str | None, need_key: bool) -> OutputColumns:
    """Resolve the output's columns; raise when a required one is absent."""
    decision = table.column(DECISION_ALIASES)
    labels = resolve_triple(table, (TYPE_ALIASES, USAGE_ALIASES, SUBTYPE_ALIASES))
    if decision is None or labels is None:
        raise ScoreError(f"{table.path}: needs a decision column and the three label columns")
    key = resolve_key(table, key_override)
    if key is None and need_key:
        raise ScoreError(f"{table.path}: no key column; pass --key COL or --join row-order")
    return OutputColumns(
        key=key,
        decision=decision,
        labels=labels,
        suggested=resolve_triple(table, SUGGESTED_ALIASES),
        suggested_2=resolve_triple(table, SUGGESTED_2_ALIASES),
        unit=table.column(UNIT_ALIASES),
        cost=table.column(COST_ALIASES),
        latency=table.column(LATENCY_ALIASES),
    )


def optional_triple(
    raw: Sequence[str], indices: tuple[int, int, int] | None
) -> tuple[Triple | None, int]:
    """Return the blanked triple at ``indices`` (None when absent) and its blanked-cell count."""
    if indices is None:
        return None, 0
    return blank_nan(triple_at(raw, indices))


def output_row(raw: Sequence[str], columns: OutputColumns) -> tuple[OutRow, int]:
    """Build one ``OutRow`` from raw cells; return it with its count of blanked ``nan`` cells."""
    labels, blanked = blank_nan(triple_at(raw, columns.labels))
    suggested, blanked_1 = optional_triple(raw, columns.suggested)
    suggested_2, blanked_2 = optional_triple(raw, columns.suggested_2)
    row = OutRow(
        key=None if columns.key is None else cell(raw, columns.key),
        decision=cell(raw, columns.decision),
        labels=labels,
        suggested=suggested,
        suggested_2=suggested_2,
        unit=None if columns.unit is None else cell(raw, columns.unit),
        cost=None if columns.cost is None else parse_number(cell(raw, columns.cost)),
        latency=None if columns.latency is None else parse_number(cell(raw, columns.latency)),
    )
    return row, blanked + blanked_1 + blanked_2


def parse_output(table: Table, key_override: str | None, need_key: bool) -> Output:
    """Parse an output file; ``nan``-like label cells read as blank, as in the reference."""
    columns = output_columns(table, key_override, need_key)
    parsed = [output_row(raw, columns) for raw in table.rows]
    rows = [row for row, _ in parsed]
    nan_cells = sum(blanked for _, blanked in parsed)
    return Output(
        rows=rows,
        has_key=columns.key is not None,
        has_suggested=columns.suggested is not None,
        has_suggested_2=columns.suggested_2 is not None,
        has_cost=columns.cost is not None,
        has_latency=columns.latency is not None,
        nan_literals=nan_cells,
        has_unit=columns.unit is not None,
    )


# --- join ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pair:
    """A reference row and the output row joined to it (None when missing).

    Attributes:
        ref: The reference row.
        out: The joined output row, or None.

    """

    ref: RefRow
    out: OutRow | None

    @property
    def key(self) -> str | None:
        """The row's key: the reference key, else the output key."""
        if self.ref.key is not None:
            return self.ref.key
        return None if self.out is None else self.out.key


@dataclass
class Joined:
    """The join result.

    Attributes:
        pairs: One pair per reference row, in reference order.
        unscored: Labels (keys or row numbers) of output rows with no reference row.

    """

    pairs: list[Pair]
    unscored: list[str]


def occurrences(keys: Sequence[str]) -> list[tuple[str, int]]:
    """Pair each key with its 0-based occurrence index."""
    seen: Counter[str] = Counter()
    result: list[tuple[str, int]] = []
    for key in keys:
        result.append((key, seen[key]))
        seen[key] += 1
    return result


def join_by_key(reference: Reference, output: Output) -> Joined:
    """Join on (key, occurrence index); leftover output rows are unscored."""
    out_slots = occurrences([row.key or "" for row in output.rows])
    by_slot = dict(zip(out_slots, output.rows, strict=True))
    ref_slots = occurrences([row.key or "" for row in reference.rows])
    pairs = [
        Pair(ref=row, out=by_slot.pop(slot, None))
        for row, slot in zip(reference.rows, ref_slots, strict=True)
    ]
    unscored = [slot[0] for slot in out_slots if slot in by_slot]
    return Joined(pairs=pairs, unscored=unscored)


def join_by_row_order(reference: Reference, output: Output) -> Joined:
    """Join row i of the reference to row i of the output."""
    pairs = [
        Pair(ref=row, out=output.rows[index] if index < len(output.rows) else None)
        for index, row in enumerate(reference.rows)
    ]
    extra = output.rows[len(reference.rows) :]
    first_extra = len(reference.rows)
    unscored = [row.key or f"row {first_extra + offset + 1}" for offset, row in enumerate(extra)]
    return Joined(pairs=pairs, unscored=unscored)


def key_warnings(name: str, keys: Sequence[str]) -> list[str]:
    """Warn about duplicate and blank keys, with counts."""
    counts = Counter(keys)
    duplicates = sum(count - 1 for key, count in counts.items() if key and count > 1)
    warnings: list[str] = []
    if duplicates:
        warnings.append(f"{name}: {duplicates} duplicate key row(s); joined on occurrence index")
    if counts.get("", 0):
        warnings.append(f"{name}: {counts['']} blank key(s)")
    return warnings


# --- strict mode -----------------------------------------------------------------------


def strict_keys(
    reference: Reference, output: Output, joined: Joined, side_ids: set[str] | None
) -> list[str]:
    """Return the ``--strict`` key violations: duplicates, and missing (in side) or extra keys."""
    problems = key_warnings("reference", [row.key or "" for row in reference.rows])
    problems += key_warnings("output", [row.key or "" for row in output.rows])
    missing = sum(
        1
        for pair in joined.pairs
        if pair.out is None and (side_ids is None or (pair.key or "") in side_ids)
    )
    if missing:
        problems.append(f"{missing} reference row(s) missing from the output")
    if joined.unscored:
        problems.append(f"{len(joined.unscored)} output row(s) not in the reference")
    return problems


def strict_rows(output: Output) -> list[str]:
    """Return the ``--strict`` row violations of an output file."""
    counts = row_problem_counts(output)
    labels = {
        INVALID: "decision outside the enum",
        "matched_blank": "matched with an all-blank triple",
        "labelled_non_match": "non-matched row carrying labels",
    }
    return [f"{count} row(s): {labels[kind]}" for kind, count in counts.items() if count]


def row_problem(row: OutRow) -> str | None:
    """Return the kind of a row's structural problem, or None when it is well formed."""
    if row.decision not in DECISIONS:
        return INVALID
    if row.decision == MATCHED and row.labels == BLANK:
        return "matched_blank"
    if row.decision != MATCHED and row.labels != BLANK:
        return "labelled_non_match"
    return None


def row_problem_counts(output: Output) -> dict[str, int]:
    """Count invalid decisions, blank matches and labelled non-matches."""
    counts = {INVALID: 0, "matched_blank": 0, "labelled_non_match": 0}
    for row in output.rows:
        kind = row_problem(row)
        if kind is not None:
            counts[kind] += 1
    return counts


# --- statistics ------------------------------------------------------------------------


def binomial_upper_tail(successes: int, trials: int, probability: float) -> float:
    """Return P(X >= successes) for X ~ Binomial(trials, probability), via ``math.lgamma``."""
    if probability <= 0.0:
        return 0.0 if successes > 0 else 1.0
    if probability >= 1.0:
        return 1.0
    log_p = math.log(probability)
    log_q = math.log1p(-probability)
    log_n = math.lgamma(trials + 1)
    terms = [
        log_n
        - math.lgamma(count + 1)
        - math.lgamma(trials - count + 1)
        + count * log_p
        + (trials - count) * log_q
        for count in range(successes, trials + 1)
    ]
    peak = max(terms)
    return min(1.0, math.exp(peak) * math.fsum(math.exp(term - peak) for term in terms))


def cp_lower_bound(successes: int, trials: int, alpha: float = CONFIDENCE_ALPHA) -> float:
    """One-sided exact (Clopper-Pearson) lower bound: the p with P(X >= k | n, p) = alpha.

    Computed by bisection on the binomial tail; 0 when there are no successes.
    """
    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError(f"need 0 <= successes <= trials and trials > 0, got {successes}/{trials}")
    if successes == 0:
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(BISECTION_STEPS):
        middle = (low + high) / 2
        if binomial_upper_tail(successes, trials, middle) < alpha:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def wilson_interval(successes: int, trials: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Wilson score interval, for display only."""
    share = successes / trials
    denominator = 1 + z * z / trials
    centre = (share + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(share * (1 - share) / trials + z * z / (4 * trials * trials))
    return max(0.0, centre - half / denominator), min(1.0, centre + half / denominator)


def ratio(numerator: int, denominator: int) -> float | None:
    """Return numerator / denominator, or None when the denominator is 0."""
    return numerator / denominator if denominator else None


# --- correctness -----------------------------------------------------------------------


def lenient(text: str) -> str:
    """NFC, trim and whitespace collapse, for the lenient line only."""
    return WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def squash(text: str) -> str:
    """Trim and whitespace collapse, without Unicode normalisation."""
    return WHITESPACE.sub(" ", text).strip()


def level_hits(predicted: Triple, reference: Triple) -> tuple[bool, bool, bool]:
    """Cumulative (type, type+usage, triple) hits; a blank reference is wrong at every level."""
    if reference == BLANK:
        return (False, False, False)
    return (
        predicted[0] == reference[0],
        predicted[:2] == reference[:2],
        predicted == reference,
    )


def lenient_equal(predicted: Triple, reference: Triple) -> bool:
    """Whether two triples agree after the lenient normalisation (blank references never)."""
    if reference == BLANK:
        return False
    return tuple(map(lenient, predicted)) == tuple(map(lenient, reference))


def proposal(pair: Pair, has_suggested: bool) -> Triple:
    """Return the top-1 proposal: ``suggested_*`` when present, else the matched labels."""
    if pair.out is None:
        return BLANK
    if has_suggested and pair.out.suggested is not None:
        return pair.out.suggested
    return pair.out.labels if pair.out.decision == MATCHED else BLANK


def decision_of(pair: Pair) -> str:
    """Return the pair's decision, or ``missing`` / ``invalid``."""
    if pair.out is None:
        return MISSING
    return pair.out.decision if pair.out.decision in DECISIONS else INVALID


# --- scoping ---------------------------------------------------------------------------


@dataclass
class Scope:
    """The scored rows of one output and their classes.

    Attributes:
        pairs: Pairs in scope (after the split side filter).
        classes: Reference class per pair (H, L, E, S, A or blank).
        has_classes: Whether a class list was given.

    """

    pairs: list[Pair]
    classes: list[str]
    has_classes: bool

    def where(self, *wanted: str) -> list[Pair]:
        """Return the pairs whose class is one of ``wanted``."""
        return [pair for pair, code in zip(self.pairs, self.classes, strict=True) if code in wanted]


def classify(ref: RefRow, classes: dict[str, str]) -> str:
    """Return a reference row's class from reference-side data only, never from the output.

    Labelled rows are class L; a key in the class list takes that class; otherwise the
    reference's unit column decides item vs header, else its key shape; a row with neither is
    unclassed (U) and is counted in no item denominator.
    """
    if ref.labels != BLANK:
        return CLASS_LABELLED
    if ref.key is not None and ref.key in classes:
        return classes[ref.key]
    if ref.unit is not None:
        return CLASS_BLANK if ref.unit.strip() else CLASS_HEADER
    if ref.key is not None:
        return CLASS_BLANK if ITEM_KEY.match(ref.key) else CLASS_HEADER
    return CLASS_UNCLASSED


def build_scope(pairs: list[Pair], side_ids: set[str] | None, classes: dict[str, str]) -> Scope:
    """Filter pairs to the split side and classify them."""
    kept = [pair for pair in pairs if side_ids is None or (pair.key or "") in side_ids]
    return Scope(
        pairs=kept,
        classes=[classify(pair.ref, classes) for pair in kept],
        has_classes=bool(classes),
    )


def item_pairs(scope: Scope) -> list[Pair]:
    """Pairs whose reference class is an item class (not header, not unclassed)."""
    return [
        pair
        for pair, code in zip(scope.pairs, scope.classes, strict=True)
        if code not in NON_ITEM_CLASSES
    ]


# --- metrics ---------------------------------------------------------------------------


def precision_block(matched: list[Pair]) -> dict[str, Any]:
    """Return matched precision, its one-sided CP lower bound and a display Wilson interval."""
    correct = sum(
        1 for pair in matched if pair.out and level_hits(pair.out.labels, pair.ref.labels)[2]
    )
    total = len(matched)
    if not total:
        return {"correct": 0, "matched": 0, "value": None, "cp_lower_95": None, "wilson_95": None}
    return {
        "correct": correct,
        "matched": total,
        "value": correct / total,
        "cp_lower_95": cp_lower_bound(correct, total),
        "wilson_95": list(wilson_interval(correct, total)),
    }


def matched_pairs(scope: Scope) -> list[Pair]:
    """Pairs decided ``matched``."""
    return [pair for pair in scope.pairs if decision_of(pair) == MATCHED]


def count_correct(pairs: list[Pair], lenient_mode: bool = False) -> int:
    """Count matched pairs whose triple is right (exact, or lenient when asked)."""
    total = 0
    for pair in pairs:
        if pair.out is None or pair.out.decision != MATCHED:
            continue
        if lenient_mode:
            total += int(lenient_equal(pair.out.labels, pair.ref.labels))
        else:
            total += int(level_hits(pair.out.labels, pair.ref.labels)[2])
    return total


def coverage_block(correct: int, denominator: int) -> dict[str, Any]:
    """Return a coverage figure with its counts."""
    return {"correct": correct, "denominator": denominator, "value": ratio(correct, denominator)}


def matched_labels(pair: Pair) -> Triple:
    """Return the labels a pair's output emitted (blank when missing)."""
    return BLANK if pair.out is None else pair.out.labels


def levels_over(pairs: list[Pair], predict: Callable[[Pair], Triple]) -> dict[str, float | None]:
    """Cumulative per-level accuracy of ``predict(pair)`` over ``pairs``."""
    hits = [level_hits(predict(pair), pair.ref.labels) for pair in pairs]
    return {
        level: ratio(sum(1 for row in hits if row[index]), len(pairs))
        for index, level in enumerate(LEVELS)
    }


def per_level_labelled(scope: Scope, has_suggested: bool) -> dict[str, Any]:
    """Proposal accuracy over all labelled lines, and the cascade conditionals."""
    labelled = scope.where(CLASS_LABELLED)
    hits = [level_hits(proposal(pair, has_suggested), pair.ref.labels) for pair in labelled]
    counts = [sum(1 for row in hits if row[index]) for index in range(len(LEVELS))]
    block: dict[str, Any] = {
        level: ratio(counts[index], len(labelled)) for index, level in enumerate(LEVELS)
    }
    block["source"] = "suggested" if has_suggested else "decision"
    cascade = {
        "usage_given_type": ratio(counts[1], counts[0]),
        "subtype_given_type_usage": ratio(counts[2], counts[1]),
    }
    return {"levels": block, "cascade": cascade}


def hit_blocks(scope: Scope, output: Output) -> tuple[dict[str, Any], dict[str, Any]]:
    """hit@1 and hit@2 over ``needs_review`` lines with a labelled reference."""
    review = [pair for pair in scope.where(CLASS_LABELLED) if decision_of(pair) == NEEDS_REVIEW]
    first = [bool(pair.out and pair.out.suggested == pair.ref.labels) for pair in review]
    second = [bool(pair.out and pair.out.suggested_2 == pair.ref.labels) for pair in review]
    hit_1 = sum(first)
    hit_2 = sum(1 for one, two in zip(first, second, strict=True) if one or two)
    block_1 = hit_block(hit_1 if output.has_suggested else None, len(review))
    usable_2 = output.has_suggested and output.has_suggested_2
    block_2 = hit_block(hit_2 if usable_2 else None, len(review))
    if not output.has_suggested_2:
        block_2["note"] = HIT_2_NOTE
    return block_1, block_2


def hit_block(hits: int | None, denominator: int) -> dict[str, Any]:
    """One hit@k figure; ``hits`` is None when the output lacks the suggestion columns."""
    value = None if hits is None else ratio(hits, denominator)
    return {"hits": hits, "denominator": denominator, "value": value}


def shares(pairs: list[Pair]) -> dict[str, Any]:
    """Decision counts and shares over ``pairs``."""
    counts = Counter(decision_of(pair) for pair in pairs)
    ordered = {name: counts.get(name, 0) for name in DECISIONS}
    ordered.update({name: counts[name] for name in (MISSING, INVALID) if counts.get(name)})
    total = len(pairs)
    return {
        "denominator": total,
        "counts": ordered,
        "shares": {name: ratio(count, total) for name, count in ordered.items()},
    }


def mean_of(values: list[float | None], present: bool) -> float | None:
    """Mean of the parsed values, or None when the column is absent or empty."""
    numbers = [value for value in values if value is not None]
    if not present or not numbers:
        return None
    return math.fsum(numbers) / len(numbers)


def confusion(scope: Scope) -> dict[str, dict[str, int]]:
    """Count reference class x decision (non-zero cells only)."""
    table: dict[str, Counter[str]] = {}
    for pair, code in zip(scope.pairs, scope.classes, strict=True):
        table.setdefault(code, Counter())[decision_of(pair)] += 1
    return {code: dict(counter) for code, counter in table.items()}


def whitespace_only(matched: list[Pair]) -> int:
    """Count matched lines wrong exactly but equal after trim and whitespace collapse."""
    total = 0
    for pair in matched:
        if pair.out is None or pair.ref.labels in {pair.out.labels, BLANK}:
            continue
        total += int(tuple(map(squash, pair.out.labels)) == tuple(map(squash, pair.ref.labels)))
    return total


# --- one output's report ---------------------------------------------------------------


@dataclass
class Job:
    """One output to score.

    Attributes:
        label: Display label (for example ``en``).
        path: Output file path.

    """

    label: str
    path: Path


@dataclass
class Settings:
    """Parsed command-line settings.

    Attributes:
        jobs: Outputs to score.
        reference: Reference file path.
        key: ``--key`` override.
        strict: Whether ``--strict`` is on.
        join: ``key`` or ``row-order``.
        split: Split file path, or None.
        side: ``dev``, ``lockbox`` or ``all``.
        classes: Class-list path, or None.
        json_path: Where to write the JSON report, or None.

    """

    jobs: list[Job]
    reference: Path
    key: str | None
    strict: bool
    join: str
    split: Path | None
    side: str
    classes: Path | None
    json_path: Path | None


def counts_block(scope: Scope, joined: Joined) -> dict[str, Any]:
    """Row counts behind every denominator."""
    return {
        "rows": len(scope.pairs),
        "items": sum(1 for code in scope.classes if code not in NON_ITEM_CLASSES),
        "unclassed": scope.classes.count(CLASS_UNCLASSED),
        "labelled": len(scope.where(CLASS_LABELLED)),
        "no_equivalent": len(scope.where(CLASS_NO_EQUIVALENT)) if scope.has_classes else None,
        "missing": sum(1 for pair in scope.pairs if pair.out is None),
        "unscored": len(joined.unscored),
    }


def headline_blocks(scope: Scope) -> dict[str, Any]:
    """Precision, coverage, per-level-over-matched, safety and lenient figures."""
    matched = matched_pairs(scope)
    labelled = scope.where(CLASS_LABELLED)
    material = scope.where(CLASS_LABELLED, CLASS_NO_EQUIVALENT)
    correct = count_correct(labelled)
    lenient_correct = count_correct(labelled, lenient_mode=True)
    return {
        "precision": precision_block(matched),
        "coverage_labelled": coverage_block(correct, len(labelled)),
        "coverage_material": coverage_block(correct, len(material)) if scope.has_classes else None,
        "per_level_matched": levels_over(matched, matched_labels),
        "false_not_a_material": sum(1 for pair in material if decision_of(pair) == NOT_A_MATERIAL),
        "f_nm_scope": F_NM_MATERIAL if scope.has_classes else F_NM_LABELLED,
        "whitespace_only_mismatch": whitespace_only(matched),
        "lenient": {
            "precision": ratio(count_correct(matched, lenient_mode=True), len(matched)),
            "coverage_labelled": ratio(lenient_correct, len(labelled)),
        },
    }


def output_warnings(report: dict[str, Any], output: Output) -> list[str]:
    """Warnings for one output: lenient differences, invalid rows and ``nan`` literals."""
    warnings: list[str] = []
    exact = (report["precision"]["value"], report["coverage_labelled"]["value"])
    loose = (report["lenient"]["precision"], report["lenient"]["coverage_labelled"])
    if exact != loose:
        warnings.append(
            "lenient (NFC + trim + whitespace collapse) differs from the exact headline; "
            "the headline stays exact"
        )
    warnings += [f"output: {problem}" for problem in strict_rows(output)]
    if output.nan_literals:
        warnings.append(f"output: {output.nan_literals} nan-like label cell(s) read as blank")
    if report["counts"]["missing"]:
        warnings.append(f"{report['counts']['missing']} reference row(s) missing from the output")
    if report["counts"]["unscored"]:
        warnings.append(f"{report['counts']['unscored']} output row(s) unscored (not in reference)")
    return warnings


def nam_with_unit(scope: Scope, output: Output) -> int | None:
    """Hostile cross-check: ``not_a_material`` rows whose output unit is non-blank."""
    if not output.has_unit:
        return None
    return sum(
        1
        for pair in scope.pairs
        if decision_of(pair) == NOT_A_MATERIAL and pair.out and (pair.out.unit or "").strip()
    )


def decision_share_blocks(scope: Scope, side_filtered: bool) -> dict[str, Any]:
    """Shares over all rows (n/a under a side filter: sides hold item rows only) and item rows."""
    return {
        "all_rows": None if side_filtered else shares(scope.pairs),
        "item_rows": shares(item_pairs(scope)),
    }


def score_output(
    output: Output, joined: Joined, side_ids: set[str] | None, classes: dict[str, str]
) -> dict[str, Any]:
    """Compute every metric for one joined output."""
    scope = build_scope(joined.pairs, side_ids, classes)
    report: dict[str, Any] = {"counts": counts_block(scope, joined), **headline_blocks(scope)}
    report["not_a_material_with_unit"] = nam_with_unit(scope, output)
    labelled_view = per_level_labelled(scope, output.has_suggested)
    report["per_level_labelled"] = labelled_view["levels"]
    report["cascade"] = labelled_view["cascade"]
    report["hit_at_1"], report["hit_at_2"] = hit_blocks(scope, output)
    report["decision_shares"] = decision_share_blocks(scope, side_ids is not None)
    report["mean_cost_usd"] = mean_of([p.out.cost for p in scope.pairs if p.out], output.has_cost)
    report["mean_latency_ms"] = mean_of(
        [p.out.latency for p in scope.pairs if p.out], output.has_latency
    )
    report["confusion"] = confusion(scope)
    report["missing_keys"] = [pair.key or "" for pair in scope.pairs if pair.out is None]
    report["unscored_keys"] = joined.unscored
    report["warnings"] = output_warnings(report, output)
    return report


# --- inputs: split and classes ---------------------------------------------------------


def load_side_ids(split: Path | None, side: str) -> set[str] | None:
    """Return the item ids of the requested split side, or None for every row."""
    if side == SIDE_ALL:
        return None
    if split is None:
        raise ScoreError(f"--side {side} needs --split")
    try:
        payload = json.loads(split.read_text(encoding="utf-8"))
        return set(payload[SPLIT_FIELDS[side]])
    except (OSError, ValueError, KeyError) as error:
        raise ScoreError(f"cannot read side {side!r} from {split}: {error}") from error


def load_classes(path: Path | None) -> dict[str, str]:
    """Read the optional class list (key, class) into key -> class code."""
    if path is None:
        return {}
    table = read_table(path)
    key_at = table.column((*KEY_ALIASES, "itemno"))
    class_at = table.column(CLASS_ALIASES)
    if key_at is None or class_at is None:
        raise ScoreError(f"{path}: needs a key column and a class column")
    return {
        cell(row, key_at): CLASS_CODES.get(cell(row, class_at), CLASS_BLANK) for row in table.rows
    }


# --- orchestration ---------------------------------------------------------------------


def join_output(settings: Settings, context: Context, output: Output) -> tuple[Joined, list[str]]:
    """Join one output to the reference, enforcing ``--strict`` when asked."""
    reference = context.reference
    if settings.join == JOIN_ROW_ORDER:
        joined = join_by_row_order(reference, output)
        unequal = len(reference.rows) != len(output.rows)
        if settings.strict and unequal:
            raise ScoreError("--join row-order needs equal row counts under --strict")
        return joined, []
    joined = join_by_key(reference, output)
    if settings.strict:
        problems = strict_keys(reference, output, joined, context.side_ids)
        if problems:
            raise ScoreError("--strict: " + "; ".join(problems))
    return joined, key_warnings("output", [row.key or "" for row in output.rows])


def item_rule(reference: Reference) -> str:
    """Describe how unlabelled reference rows are classed as items or headers."""
    if reference.has_unit:
        return "labels, then the class list, then the reference unit column"
    if reference.has_key:
        return "labels, then the class list, then the reference key shape"
    return "labels only (no key or unit column: unlabelled rows are unclassed)"


def reference_warnings(reference: Reference, has_classes: bool) -> list[str]:
    """Warnings about the reference file."""
    warnings: list[str] = []
    if has_classes and not reference.has_key:
        warnings.append("reference has no key column; the class list cannot be applied")
    if reference.has_key:
        warnings += key_warnings("reference", [row.key or "" for row in reference.rows])
    if reference.nan_literals:
        warnings.append(f"reference: {reference.nan_literals} nan-like label cell(s) read as blank")
    if reference.partial:
        warnings.append(
            f"reference: {reference.partial} partly labelled row(s); blanks compared exactly"
        )
    return warnings


@dataclass(frozen=True)
class Context:
    """What every output is scored against.

    Attributes:
        reference: The parsed reference.
        side_ids: Item ids of the split side, or None for every row.
        classes: Key -> reference class code from the optional class list.

    """

    reference: Reference
    side_ids: set[str] | None
    classes: dict[str, str]
    key_in_reference: bool = False


def has_column(table: Table, name: str) -> bool:
    """Whether ``table`` has a column whose folded name equals the folded ``name``."""
    return table.column((normalize_header(name),)) is not None


def key_override_notes(settings: Settings, context: Context, table: Table) -> list[str]:
    """Check ``--key``: an error when neither file has it, a warning when only one has it."""
    if settings.key is None:
        return []
    in_output = has_column(table, settings.key)
    if not (in_output or context.key_in_reference):
        raise ScoreError(
            f"--key {settings.key!r}: column not found in {settings.reference} or {table.path}"
        )
    if in_output and context.key_in_reference:
        return []
    missing_from = settings.reference if in_output else table.path
    return [f"--key {settings.key!r} not in {missing_from}; its key was auto-detected"]


def score_job(settings: Settings, context: Context, job: Job) -> dict[str, Any]:
    """Parse, join and score one output file."""
    table = read_table(job.path)
    key_notes = key_override_notes(settings, context, table)
    output = parse_output(table, settings.key, settings.join == JOIN_KEY)
    problems = strict_rows(output) if settings.strict else []
    if problems:
        raise ScoreError(f"--strict: {job.path}: " + "; ".join(problems))
    joined, join_notes = join_output(settings, context, output)
    result = score_output(output, joined, context.side_ids, context.classes)
    result["warnings"] = key_notes + join_notes + result["warnings"]
    return {"label": job.label, "path": str(job.path), **result}


def run(settings: Settings) -> dict[str, Any]:
    """Score every output and return the JSON-ready report."""
    table = read_table(settings.reference)
    reference = parse_reference(table, settings.key, settings.join == JOIN_KEY)
    context = Context(
        reference=reference,
        side_ids=load_side_ids(settings.split, settings.side),
        classes=load_classes(settings.classes),
        key_in_reference=settings.key is not None and has_column(table, settings.key),
    )
    outputs = [score_job(settings, context, job) for job in settings.jobs]
    warnings = reference_warnings(reference, bool(context.classes))
    warnings += [f"{out['label']}: {note}" for out in outputs for note in out["warnings"]]
    return {
        "reference": {
            "path": str(settings.reference),
            "rows": len(reference.rows),
            "nan_literals": reference.nan_literals,
            "item_rule": item_rule(reference),
        },
        "join": settings.join,
        "side": settings.side,
        "strict": settings.strict,
        "outputs": outputs,
        "warnings": warnings,
    }


# --- text report -----------------------------------------------------------------------


def fmt(value: float | None) -> str:
    """Format a share to three decimals, or ``n/a``."""
    return "n/a" if value is None else f"{value:.{DIGITS}f}"


def fmt_levels(levels: dict[str, Any]) -> str:
    """Format a per-level block."""
    return ", ".join(f"{name.replace('_', '+')} {fmt(levels[name])}" for name in LEVELS)


def fmt_shares(block: dict[str, Any]) -> str:
    """Format a decision-share block."""
    parts = [
        f"{name} {count} ({fmt(block['shares'][name])})" for name, count in block["counts"].items()
    ]
    return f"n={block['denominator']}: " + ", ".join(parts)


def precision_line(precision: dict[str, Any]) -> str:
    """Format the headline precision line, with the claim bound."""
    if precision["value"] is None:
        return "matched precision n/a (0 matched); one-sided 95% CP lower bound n/a"
    low, high = precision["wilson_95"]
    return (
        f"matched precision {precision['value']:.{DIGITS}f} "
        f"({precision['correct']}/{precision['matched']}); "
        f"one-sided 95% CP lower bound {precision['cp_lower_95']:.{DIGITS}f}; "
        f"Wilson 95% [{low:.{DIGITS}f}, {high:.{DIGITS}f}] (display only)"
    )


def fraction(block: dict[str, Any]) -> str:
    """Format a coverage block's counts as ``(k/n)``."""
    return f"({block['correct']}/{block['denominator']})"


def coverage_line(out: dict[str, Any]) -> str:
    """Coverage over labelled lines, and over material lines when classes were given."""
    labelled = out["coverage_labelled"]
    line = f"coverage C_labelled {fmt(labelled['value'])} " + fraction(labelled)
    material = out["coverage_material"]
    if material is not None:
        line += f"; C_material {fmt(material['value'])} " + fraction(material)
    return line


def hit_line(out: dict[str, Any]) -> str:
    """hit@1 and hit@2 on review lines."""
    first, second = out["hit_at_1"], out["hit_at_2"]
    hit_2 = fmt(second["value"])
    if "note" in second:
        hit_2 += f" ({second['note']})"
    return (
        f"hit@1 {fmt(first['value'])}, hit@2 {hit_2} "
        f"over {first['denominator']} needs_review line(s) with a labelled reference"
    )


def f_nm_line(out: dict[str, Any]) -> str:
    """Format the safety count with its scope, and the hostile unit cross-check."""
    if out["f_nm_scope"] == F_NM_LABELLED:
        scope = "F_NM over labelled lines only (no material-without-equivalent list given)"
    else:
        scope = "F_NM over labelled + no-equivalent material lines"
    line = f"false not_a_material ({scope}): {out['false_not_a_material']}"
    with_unit = out["not_a_material_with_unit"]
    if with_unit is not None:
        line += f"; not_a_material rows with a non-blank output unit: {with_unit}"
    return line


def all_rows_line(out: dict[str, Any]) -> str:
    """Decision shares over all rows, or why they are n/a."""
    block = out["decision_shares"]["all_rows"]
    if block is None:
        return "decisions over all rows n/a (a split side holds item rows only; use --side all)"
    return f"decisions over all rows {fmt_shares(block)}"


def key_list_lines(out: dict[str, Any]) -> list[str]:
    """List missing and unscored keys, truncated after ``LIST_LIMIT``."""
    lines: list[str] = []
    for name, field in (("missing", "missing_keys"), ("unscored", "unscored_keys")):
        keys = out[field]
        if not keys:
            continue
        extra = len(keys) - LIST_LIMIT
        suffix = f" ... (+{extra} more)" if extra > 0 else ""
        lines.append(f"{name}: " + ", ".join(keys[:LIST_LIMIT]) + suffix)
    return lines


def render_output(out: dict[str, Any]) -> list[str]:
    """Render one output's report as text lines."""
    counts = out["counts"]
    lenient_block = out["lenient"]
    lines = [
        f"== {out['label']}: {out['path']} ==",
        ", ".join(
            f"{name} {value if value is not None else 'n/a'}" for name, value in counts.items()
        ),
        precision_line(out["precision"]),
        coverage_line(out),
        f"per-level accuracy over matched: {fmt_levels(out['per_level_matched'])}",
        f"per-level accuracy over labelled ({out['per_level_labelled']['source']}): "
        f"{fmt_levels(out['per_level_labelled'])}",
        f"cascade: P(usage|type) {fmt(out['cascade']['usage_given_type'])}, "
        f"P(subtype|type+usage) {fmt(out['cascade']['subtype_given_type_usage'])}",
        all_rows_line(out),
        f"decisions over item rows {fmt_shares(out['decision_shares']['item_rows'])}",
        f_nm_line(out),
        hit_line(out),
        f"mean cost_usd {fmt_cost(out['mean_cost_usd'])}; mean latency_ms "
        f"{fmt_latency(out['mean_latency_ms'])} (attributed, conservative under concurrency)",
        f"whitespace_only_mismatch {out['whitespace_only_mismatch']}",
        f"lenient (NFC + trim + whitespace collapse): precision {fmt(lenient_block['precision'])}, "
        f"coverage {fmt(lenient_block['coverage_labelled'])}",
    ]
    return lines + key_list_lines(out) + [f"WARNING: {warning}" for warning in out["warnings"]]


def fmt_cost(value: float | None) -> str:
    """Format a mean cost."""
    return "n/a" if value is None else f"{value:.{COST_DIGITS}f}"


def fmt_latency(value: float | None) -> str:
    """Format a mean latency."""
    return "n/a" if value is None else f"{value:.{LATENCY_DIGITS}f}"


def render(report: dict[str, Any]) -> str:
    """Render the full report as text."""
    lines = [
        f"reference {report['reference']['path']} | join {report['join']} | side {report['side']}"
    ]
    if report["reference"]["nan_literals"]:
        lines.append(
            f"WARNING: reference: {report['reference']['nan_literals']} nan-like cell(s) "
            "read as blank"
        )
    for out in report["outputs"]:
        lines += ["", *render_output(out)]
    return "\n".join(lines) + "\n"


# --- CLI -------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--output", action="append", required=True, type=Path)
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--key")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--join", choices=(JOIN_KEY, JOIN_ROW_ORDER), default=JOIN_KEY)
    parser.add_argument("--split", type=Path)
    parser.add_argument("--side", choices=(*SPLIT_FIELDS, SIDE_ALL), default=SIDE_ALL)
    parser.add_argument("--classes", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path)
    return parser


def parse_settings(argv: Sequence[str] | None) -> Settings:
    """Parse argv into ``Settings``; labels pair with outputs by position."""
    args = build_parser().parse_args(argv)
    if len(args.label) > len(args.output):
        raise ScoreError("more --label values than --output values")
    labels = [*args.label, *(path.stem for path in args.output[len(args.label) :])]
    return Settings(
        jobs=[Job(label=label, path=path) for label, path in zip(labels, args.output, strict=True)],
        reference=args.reference,
        key=args.key,
        strict=args.strict,
        join=args.join,
        split=args.split,
        side=args.side,
        classes=args.classes,
        json_path=args.json_path,
    )


def use_utf8_stdout() -> None:
    """Print UTF-8 whatever the console code page is."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the scorer; return the process exit code."""
    use_utf8_stdout()
    try:
        settings = parse_settings(argv)
        report = run(settings)
    except ScoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    sys.stdout.write(render(report))
    if settings.json_path is not None:
        text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        settings.json_path.write_text(text, encoding="utf-8")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
