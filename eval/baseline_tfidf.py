r"""B1 TF-IDF baseline and the label-free scorer check (DESIGN.md §10.5, §10.6; A6, A29, A47).

**B1** (``b1`` sub-command): ``char_wb`` 3-5-grams plus word 1-2-grams, cosine scores averaged,
``sublinear_tf``, accents stripped; one vectoriser pair per language, fit on the library rows
plus that language's **dev** item lines only (lockbox lines are only ever transformed, and only
in a post-freeze ``--lockbox-session``). Every item line gets the argmax row over the whole
library as its suggestion; it is ``matched`` (reason ``SIGNAL:b1_cosine_<threshold>``) when its
cosine score reaches the threshold, else ``needs_review`` (``LOW_SIGNAL:b1_cosine``). Rows with
empty Unit and Qty go through the G1 gate of §9.5 first: a structural header is
``not_a_material``/``HEADER`` (D0), a fully empty row ``not_a_material``/``EMPTY_ROW`` (D0a),
any other one ``needs_review``/``HEADER_UNCONFIRMED`` (D0b); a measured row is never
``not_a_material``. The one threshold for both languages is chosen on dev by the §10.6 rule; the
output CSV has the §9.6 shape with ``model = tfidf-b1``, and the per-line cosine scores go to the
summary JSON.

**Label-free check** (``label-free`` sub-command): the configuration measured in
``docs/data-analysis.md`` §3-§4 (word 1-grams, fit on library + all rows of the file, match
all). It is never thresholded and never reported as B1; it exists only to validate the scorer:
it writes a §9.6 match-all output per language and scores it with ``eval/score.py --strict``,
whose top-1 must reproduce the measured EN .405 / FR .159 within .01.

Usage::

    python eval/baseline_tfidf.py b1 --input-en input/boq_dataset_input_en.csv \\
        --input-fr input/boq_dataset_input_fr.csv --library data/oris_materials_global.csv \\
        --reference data/boq_dataset_matched_GT.csv --split eval/split_v1.json --side dev \\
        --output-en B1_EN.csv --output-fr B1_FR.csv --summary B1.json
    python eval/baseline_tfidf.py label-free --input-en ... --input-fr ... --library ... \\
        --reference ...
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray
from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import-untyped]
from sklearn.metrics.pairwise import cosine_similarity  # type: ignore[import-untyped]

ENCODING = "utf-8"
READ_ENCODING = "utf-8-sig"
LINE_TERMINATOR = "\r\n"
HEADER_PATTERNS = (re.compile(r"^\d{1,2}$|^\d{2}\.\d{2}\.$"),)
CODE_SEPARATORS = re.compile(r"[.\-\s]+")
KEY_COLUMN = "Item No."
SHORT_COLUMN = "Short Description"
LONG_COLUMN = "Long Description"
UNIT_COLUMN = "Unit"
QTY_COLUMNS = ("BoQ Qty", "Qty")
KIND_ITEM = "item"
KIND_HEADER = "header"
KIND_EMPTY_ROW = "empty_row"
KIND_HEADER_UNCONFIRMED = "header_unconfirmed"
SCORER_PATH = Path(__file__).resolve().parent / "score.py"
SCORER_MODULE = "oris_eval_score"
SCORER_SOURCE = "eval/score.py"
RULE_PATH = Path(__file__).resolve().parent / "selection_rule.py"
RULE_MODULE = "oris_eval_selection_rule"
LIBRARY_FIELDS = ("material_type", "material_usage", "material_subtype")

CHAR_NGRAMS = (3, 5)
B1_WORD_NGRAMS = (1, 2)
LABEL_FREE_WORD_NGRAMS = (1, 1)
STRIP_ACCENTS = "unicode"
SCORE_DIGITS = 4

LABEL_FREE_EXPECTED = {"en": 0.405, "fr": 0.159}
LABEL_FREE_TOLERANCE = 0.01

MODEL_B1 = "tfidf-b1"
MODEL_RULES = "rules"
PROMPT_VERSION = "none"
REASON_HEADER = "HEADER"
REASON_EMPTY_ROW = "EMPTY_ROW"
REASON_HEADER_UNCONFIRMED = "HEADER_UNCONFIRMED"
REASON_SIGNAL = "SIGNAL:b1_cosine_"
REASON_LOW_SIGNAL = "LOW_SIGNAL:b1_cosine"
REASON_MATCH_ALL = "SIGNAL:label_free_match_all"
MATCHED = "matched"
NEEDS_REVIEW = "needs_review"
NOT_A_MATERIAL = "not_a_material"
ZERO_COST = "0.000000"
ZERO_LATENCY = "0"
SHOWN_IDS = 10
ROW_ID_LENGTH = 12
ROW_ID_SEPARATOR = "\x1f"
OUTPUT_COLUMNS = (
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
)

SIDE_DEV = "dev"
SIDE_LOCKBOX = "lockbox"
SIDE_ALL = "all"
LANGUAGES = ("en", "fr")
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_REFUSED = 3

Triple = tuple[str, str, str]
BLANK: Triple = ("", "", "")
Pool = list[tuple[float, bool]]


class BaselineError(Exception):
    """Input the baseline cannot run on."""


# --- reading ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One BoQ row.

    Attributes:
        cells: Every raw cell, in input column order.
        item_no: Raw ``Item No.``.
        text: Short and long description joined by a space (the query text).
        kind: G1 kind: ``item``, ``header``, ``empty_row`` or ``header_unconfirmed``.

    """

    cells: tuple[str, ...]
    item_no: str
    text: str
    kind: str

    @property
    def is_item(self) -> bool:
        """Whether the row is measured (a non-empty Unit or Qty), so TF-IDF scores it."""
        return self.kind == KIND_ITEM


@dataclass(frozen=True)
class Boq:
    """A BoQ file.

    Attributes:
        header: The input header row, verbatim.
        lines: Every data row in file order.

    """

    header: tuple[str, ...]
    lines: tuple[Line, ...]


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV into dicts of raw strings."""
    with path.open(encoding=READ_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def column_index(header: Sequence[str]) -> dict[str, int]:
    """Index the key, description, unit and quantity columns; raise when one is absent."""
    qty = next((name for name in QTY_COLUMNS if name in header), None)
    names = (KEY_COLUMN, SHORT_COLUMN, LONG_COLUMN, UNIT_COLUMN)
    if qty is None or not set(names) <= set(header):
        raise BaselineError(f"needs the columns {', '.join(names)} and one of {QTY_COLUMNS}")
    index = {name: list(header).index(name) for name in names}
    index[QTY_COLUMNS[0]] = list(header).index(qty)
    return index


def blank(value: str) -> bool:
    """Whether a raw cell holds nothing but whitespace."""
    return not value.strip()


def code_segments(code: str) -> tuple[str, ...]:
    """Split a code on ``.``, ``-`` and spaces; numeric segments lose leading zeros."""
    parts = (part for part in CODE_SEPARATORS.split(code) if part)
    return tuple(str(int(part)) if part.isdecimal() else part.casefold() for part in parts)


def is_strict_prefix(outer: tuple[str, ...], inner: tuple[str, ...]) -> bool:
    """Whether ``outer`` is a strict prefix of ``inner``."""
    return len(outer) < len(inner) and inner[: len(outer)] == outer


def kind_of(row: Sequence[str], index: dict[str, int], next_code: str) -> str:
    """Apply the §9.5 D0 / D0a / D0b gate to one row (DESIGN.md §9.1, A48)."""
    if all(blank(value) for value in row):
        return KIND_EMPTY_ROW
    if not (blank(row[index[UNIT_COLUMN]]) and blank(row[index[QTY_COLUMNS[0]]])):
        return KIND_ITEM
    code = row[index[KEY_COLUMN]]
    if blank(code):
        return KIND_HEADER_UNCONFIRMED
    if any(pattern.fullmatch(code) for pattern in HEADER_PATTERNS):
        return KIND_HEADER
    segments = code_segments(code)
    if segments and is_strict_prefix(segments, code_segments(next_code)):
        return KIND_HEADER
    return KIND_HEADER_UNCONFIRMED


def line_kinds(rows: Sequence[Sequence[str]], header: Sequence[str]) -> list[str]:
    """Return the G1 kind of every row, given the next non-blank code below each one."""
    index = column_index(header)
    next_codes: list[str] = []
    upcoming = ""
    for row in reversed(rows):
        next_codes.append(upcoming)
        if not blank(row[index[KEY_COLUMN]]):
            upcoming = row[index[KEY_COLUMN]]
    following = next_codes[::-1]
    return [kind_of(row, index, code) for row, code in zip(rows, following, strict=True)]


def read_boq(path: Path) -> Boq:
    """Read a BoQ input file, keeping every cell verbatim."""
    with path.open(encoding=READ_ENCODING, newline="") as handle:
        records = list(csv.reader(handle))
    header = tuple(records[0])
    try:
        index = column_index(header)
    except BaselineError as error:
        raise BaselineError(f"{path}: {error}") from error
    kinds = line_kinds(records[1:], header)
    lines = tuple(
        Line(
            cells=tuple(record),
            item_no=record[index[KEY_COLUMN]],
            text=f"{record[index[SHORT_COLUMN]]} {record[index[LONG_COLUMN]]}",
            kind=kind,
        )
        for record, kind in zip(records[1:], kinds, strict=True)
    )
    return Boq(header=header, lines=lines)


def read_library(path: Path) -> list[Triple]:
    """Read the library's raw (type, usage, subtype) triples in file order."""
    return [
        (row[LIBRARY_FIELDS[0]], row[LIBRARY_FIELDS[1]], row[LIBRARY_FIELDS[2]])
        for row in read_rows(path)
    ]


def read_reference(path: Path) -> dict[str, Triple]:
    """Read ground-truth labels by ``Item No.`` (eval-only input)."""
    return {
        row[KEY_COLUMN]: (row[LIBRARY_FIELDS[0]], row[LIBRARY_FIELDS[1]], row[LIBRARY_FIELDS[2]])
        for row in read_rows(path)
    }


def read_split(path: Path) -> tuple[set[str], set[str]]:
    """Read (dev ids, lockbox ids) from the frozen split file."""
    payload = json.loads(path.read_text(encoding=ENCODING))
    return set(payload["item_ids_dev"]), set(payload["item_ids_lockbox"])


def library_text(triple: Triple) -> str:
    """Render a library row as matching text."""
    return " ".join(triple)


def row_id(triple: Triple) -> str:
    """Short SHA-256 of the three raw strings, joined by the unit separator."""
    joined = ROW_ID_SEPARATOR.join(triple)
    return hashlib.sha256(joined.encode(ENCODING)).hexdigest()[:ROW_ID_LENGTH]


def file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- TF-IDF ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TfidfModel:
    """A fitted char + word vectoriser pair and the transformed library.

    Attributes:
        char: ``char_wb`` vectoriser.
        word: Word n-gram vectoriser.
        library: Library triples, in file order.

    """

    char: Any
    word: Any
    library: list[Triple]

    def scores(self, texts: Sequence[str]) -> NDArray[np.float64]:
        """Return the averaged cosine scores, shape (len(texts), library rows)."""
        rows = [library_text(triple) for triple in self.library]
        parts = [
            cosine_similarity(vectoriser.transform(list(texts)), vectoriser.transform(rows))
            for vectoriser in (self.char, self.word)
        ]
        averaged: NDArray[np.float64] = (parts[0] + parts[1]) / len(parts)
        return averaged


def fit_model(
    library: list[Triple], corpus: Sequence[str], word_ngrams: tuple[int, int]
) -> TfidfModel:
    """Fit both vectorisers on ``corpus`` exactly as given."""
    char = TfidfVectorizer(
        analyzer="char_wb", ngram_range=CHAR_NGRAMS, sublinear_tf=True, strip_accents=STRIP_ACCENTS
    )
    word = TfidfVectorizer(
        analyzer="word", ngram_range=word_ngrams, sublinear_tf=True, strip_accents=STRIP_ACCENTS
    )
    return TfidfModel(char=char.fit(corpus), word=word.fit(corpus), library=library)


def fit_texts(library: list[Triple], boq: Boq, dev_ids: set[str]) -> list[str]:
    """B1's fit corpus: library rows plus dev item lines only (never lockbox or header text)."""
    dev_lines = [line.text for line in boq.lines if line.is_item and line.item_no in dev_ids]
    return [library_text(triple) for triple in library] + dev_lines


def fit_b1(library: list[Triple], boq: Boq, dev_ids: set[str]) -> TfidfModel:
    """Fit B1 for one language on library rows + dev lines."""
    return fit_model(library, fit_texts(library, boq, dev_ids), B1_WORD_NGRAMS)


def top1(model: TfidfModel, lines: Sequence[Line]) -> list[tuple[int, float]]:
    """Return (argmax row index, cosine score) per line; ties go to the first library row."""
    if not lines:
        return []
    scores = model.scores([line.text for line in lines])
    best = scores.argmax(axis=1)
    return [(int(index), float(scores[row, index])) for row, index in enumerate(best)]


# --- threshold selection (§10.6) -------------------------------------------------------


def load_sibling(name: str, path: Path) -> ModuleType:
    """Load a standalone ``eval/`` script (not a package module) once, by file path."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BaselineError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if TYPE_CHECKING:
    import selection_rule as rule
else:
    rule = load_sibling(RULE_MODULE, RULE_PATH)

DEV_BAR_PRECISION = rule.DEV_BAR_PRECISION
DEV_BAR_MIN_MATCHED = rule.DEV_BAR_MIN_MATCHED
TIE_WINDOW = rule.TIE_WINDOW
STATUS_MEETS_BAR = rule.STATUS_MEETS_BAR
STATUS_BELOW_BAR = rule.STATUS_BELOW_BAR
LanguageStats = rule.LanguageStats
Selection = rule.Selection
cp_lower = rule.cp_lower
meets_bar = rule.meets_bar
summed_correct = rule.summed_correct
pick_qualifying = rule.pick_qualifying
pick_fallback = rule.pick_fallback


def stats_at(pool: Pool, threshold: float) -> LanguageStats:
    """Dev figures of one language when lines with score >= threshold match."""
    hits = [correct for score, correct in pool if score >= threshold]
    matched = len(hits)
    correct = sum(hits)
    precision = correct / matched if matched else None
    return LanguageStats(matched, correct, precision, cp_lower(correct, matched))


Row = tuple[float, dict[str, LanguageStats]]


def select_threshold(pools: dict[str, Pool]) -> Selection[float]:
    """Apply the §10.6 rule to per-language (score, correct) pools; one threshold for all."""
    candidates = sorted({score for pool in pools.values() for score, _ in pool}, reverse=True)
    if not candidates:
        raise BaselineError("no dev lines to select a threshold on")
    table: list[Row] = [
        (threshold, {lang: stats_at(pool, threshold) for lang, pool in pools.items()})
        for threshold in candidates
    ]
    qualifying = [row for row in table if all(meets_bar(item) for item in row[1].values())]
    if qualifying:
        return pick_qualifying(qualifying, len(table))
    return pick_fallback(table)


# --- B1 run ----------------------------------------------------------------------------


@dataclass(frozen=True)
class LanguageRun:
    """One language's fitted model and its scored dev and output lines.

    Attributes:
        boq: The input file.
        model: B1 fitted on library + dev lines.
        dev_pool: (score, correct) per dev item line.
        emitted: Lines of the requested side, each with its (row index, score).
        hidden: Item ids of the other side; rule-decided rows with these keys are not written.

    """

    boq: Boq
    model: TfidfModel
    dev_pool: Pool
    emitted: dict[int, tuple[int, float]]
    hidden: frozenset[str] = frozenset()


def is_correct(predicted: Triple, reference: Triple) -> bool:
    """Exact raw-string triple match; a blank reference is never correct."""
    return reference != BLANK and predicted == reference


def score_dev(model: TfidfModel, boq: Boq, dev_ids: set[str], gt: dict[str, Triple]) -> Pool:
    """Score every dev item line and mark whether its argmax row is correct."""
    dev_lines = [line for line in boq.lines if line.is_item and line.item_no in dev_ids]
    return [
        (score, is_correct(model.library[index], gt.get(line.item_no, BLANK)))
        for line, (index, score) in zip(dev_lines, top1(model, dev_lines), strict=True)
    ]


def emitted_positions(boq: Boq, side_ids: set[str]) -> list[int]:
    """Positions of the item lines on the requested side."""
    return [
        position
        for position, line in enumerate(boq.lines)
        if line.is_item and line.item_no in side_ids
    ]


def check_coverage(boq: Boq, split: tuple[set[str], set[str]]) -> None:
    """Fail on measured rows in neither split side, instead of dropping them silently."""
    known = split[0] | split[1]
    outside = [line.item_no for line in boq.lines if line.is_item and line.item_no not in known]
    if outside:
        shown = ", ".join(outside[:SHOWN_IDS])
        raise BaselineError(f"{len(outside)} measured row(s) in no split side: {shown}")


def run_language(
    boq: Boq, library: list[Triple], split: tuple[set[str], set[str]], side_ids: set[str]
) -> tuple[TfidfModel, dict[int, tuple[int, float]]]:
    """Fit B1 on dev and score the requested side's item lines."""
    check_coverage(boq, split)
    model = fit_b1(library, boq, split[0])
    positions = emitted_positions(boq, side_ids)
    scored = top1(model, [boq.lines[position] for position in positions])
    return model, dict(zip(positions, scored, strict=True))


RULE_CELLS = {
    KIND_HEADER: (NOT_A_MATERIAL, REASON_HEADER),
    KIND_EMPTY_ROW: (NOT_A_MATERIAL, REASON_EMPTY_ROW),
    KIND_HEADER_UNCONFIRMED: (NEEDS_REVIEW, REASON_HEADER_UNCONFIRMED),
}


def rule_record(line: Line) -> list[str]:
    """One rule-decided (G1 gate) output row in the §9.6 shape."""
    decision, reason = RULE_CELLS[line.kind]
    audit = [reason, MODEL_RULES, PROMPT_VERSION, ZERO_LATENCY, ZERO_COST]
    return [*line.cells, decision, "", "", "", *audit, "", "", "", "", ""]


def scored_record(line: Line, triple: Triple, matched: bool, reason: str) -> list[str]:
    """One TF-IDF-scored output row in the §9.6 shape; the argmax row is always suggested."""
    decision = MATCHED if matched else NEEDS_REVIEW
    labels = list(triple) if matched else ["", "", ""]
    audit = [reason, MODEL_B1, PROMPT_VERSION, ZERO_LATENCY, ZERO_COST]
    return [*line.cells, decision, *labels, *audit, *triple, row_id(triple), ""]


def b1_record(
    line: Line, scored: tuple[int, float], library: list[Triple], threshold: float
) -> list[str]:
    """One B1 item row: matched at or above the threshold, else needs_review."""
    matched = scored[1] >= threshold
    reason = f"{REASON_SIGNAL}{threshold:.{SCORE_DIGITS}f}" if matched else REASON_LOW_SIGNAL
    return scored_record(line, library[scored[0]], matched, reason)


def write_rows(path: Path, header: Sequence[str], records: list[list[str]]) -> None:
    """Write a §9.6 output: UTF-8 without BOM, CRLF, minimal quoting."""
    with path.open("w", encoding=ENCODING, newline="") as handle:
        writer = csv.writer(handle, lineterminator=LINE_TERMINATOR)
        writer.writerow([*header, *OUTPUT_COLUMNS])
        writer.writerows(records)


def b1_records(run: LanguageRun, library: list[Triple], threshold: float) -> list[list[str]]:
    """Rule rows not of the other side, plus the side's scored item lines, in file order."""
    records: list[list[str]] = []
    for position, line in enumerate(run.boq.lines):
        if position in run.emitted:
            records.append(b1_record(line, run.emitted[position], library, threshold))
        elif not line.is_item and line.item_no not in run.hidden:
            records.append(rule_record(line))
    return records


def write_output(path: Path, run: LanguageRun, library: list[Triple], threshold: float) -> int:
    """Write rule rows plus the side's item lines; return the number of rows written."""
    records = b1_records(run, library, threshold)
    write_rows(path, run.boq.header, records)
    return len(records)


# --- CLI -------------------------------------------------------------------------------


def side_ids_for(side: str, split: tuple[set[str], set[str]]) -> set[str]:
    """Item ids emitted for ``side``."""
    if side == SIDE_DEV:
        return split[0]
    if side == SIDE_LOCKBOX:
        return split[1]
    return split[0] | split[1]


def hidden_ids_for(side: str, split: tuple[set[str], set[str]]) -> frozenset[str]:
    """Item ids of the side(s) not emitted for ``side``."""
    return frozenset((split[0] | split[1]) - side_ids_for(side, split))


def build_runs(args: argparse.Namespace) -> dict[str, LanguageRun]:
    """Fit and score both languages."""
    library = read_library(args.library)
    gt = read_reference(args.reference)
    split = read_split(args.split)
    side_ids = side_ids_for(args.side, split)
    hidden = hidden_ids_for(args.side, split)
    runs: dict[str, LanguageRun] = {}
    for lang, path in (("en", args.input_en), ("fr", args.input_fr)):
        boq = read_boq(path)
        model, emitted = run_language(boq, library, split, side_ids)
        pool = score_dev(model, boq, split[0], gt)
        runs[lang] = LanguageRun(boq, model, pool, emitted, hidden)
    return runs


def line_scores(run: LanguageRun) -> list[list[Any]]:
    """(item no, cosine score) of every emitted item line, for the summary JSON."""
    return [
        [run.boq.lines[position].item_no, round(score, SCORE_DIGITS)]
        for position, (_, score) in sorted(run.emitted.items())
    ]


def summary_payload(
    args: argparse.Namespace,
    selection: Selection[float],
    rows_written: dict[str, int],
    runs: dict[str, LanguageRun],
) -> dict[str, Any]:
    """Build the JSON summary of a B1 run."""
    return {
        "rung": "B1",
        "model": MODEL_B1,
        "side": args.side,
        "threshold": selection.threshold,
        "status": selection.status,
        "loosest_qualifying": selection.loosest_qualifying,
        "candidates": selection.candidates,
        "dev_at_threshold": {lang: asdict(stats) for lang, stats in selection.per_language.items()},
        "dev_bar": {"precision": DEV_BAR_PRECISION, "min_matched": DEV_BAR_MIN_MATCHED},
        "config": {
            "char_ngrams": list(CHAR_NGRAMS),
            "word_ngrams": list(B1_WORD_NGRAMS),
            "sublinear_tf": True,
            "strip_accents": STRIP_ACCENTS,
            "fit": "library rows + dev item lines (per language)",
        },
        "library_sha256": file_sha256(args.library),
        "split_sha256": file_sha256(args.split),
        "rows_written": rows_written,
        "cosine_scores": {lang: line_scores(run) for lang, run in runs.items()},
    }


def run_b1(args: argparse.Namespace) -> int:
    """Run B1: select the threshold on dev and write both output files."""
    if args.side != SIDE_DEV and not args.lockbox_session:
        print(
            f"refused: --side {args.side} emits lockbox decisions; only a post-freeze "
            "--lockbox-session may do that",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    runs = build_runs(args)
    selection = select_threshold({lang: run.dev_pool for lang, run in runs.items()})
    outputs = {"en": args.output_en, "fr": args.output_fr}
    written = {
        lang: write_output(outputs[lang], run, run.model.library, selection.threshold)
        for lang, run in runs.items()
    }
    payload = summary_payload(args, selection, written, runs)
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.summary is not None:
        args.summary.write_bytes(text.encode(ENCODING))
    sys.stdout.write(text)
    return EXIT_OK


def label_free_records(boq: Boq, library: list[Triple]) -> list[list[str]]:
    """Fit label-free on library + every row; match every measured row to its argmax."""
    corpus = [library_text(triple) for triple in library] + [line.text for line in boq.lines]
    model = fit_model(library, corpus, LABEL_FREE_WORD_NGRAMS)
    items = [position for position, line in enumerate(boq.lines) if line.is_item]
    scored = dict(zip(items, top1(model, [boq.lines[position] for position in items]), strict=True))
    return [
        scored_record(line, library[scored[position][0]], True, REASON_MATCH_ALL)
        if position in scored
        else rule_record(line)
        for position, line in enumerate(boq.lines)
    ]


def load_scorer() -> ModuleType:
    """Load ``eval/score.py`` (a standalone script, not a package module) once."""
    if SCORER_MODULE in sys.modules:
        return sys.modules[SCORER_MODULE]
    spec = importlib.util.spec_from_file_location(SCORER_MODULE, SCORER_PATH)
    if spec is None or spec.loader is None:
        raise BaselineError(f"cannot load the scorer at {SCORER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[SCORER_MODULE] = module
    spec.loader.exec_module(module)
    return module


def scorer_figures(out: dict[str, Any]) -> dict[str, Any]:
    """Pick the scorer's figures for one match-all output."""
    return {
        "source": SCORER_SOURCE,
        "per_level_labelled": out["per_level_labelled"],
        "cascade": out["cascade"],
        "coverage_labelled": out["coverage_labelled"],
        "precision": out["precision"],
    }


def score_outputs(outputs: dict[str, Path], reference: Path) -> dict[str, dict[str, Any]]:
    """Score each output with ``eval/score.py --strict`` against ``reference``."""
    scorer = load_scorer()
    argv = [
        part for lang, path in outputs.items() for part in ("--output", str(path), "--label", lang)
    ]
    argv += ["--reference", str(reference), "--strict"]
    try:
        report = scorer.run(scorer.parse_settings(argv))
    except scorer.ScoreError as error:
        raise BaselineError(f"scorer rejected the match-all output: {error}") from error
    return {out["label"]: scorer_figures(out) for out in report["outputs"]}


def label_free_top1(
    inputs: dict[str, Path], library_path: Path, reference_path: Path
) -> dict[str, dict[str, Any]]:
    """Write a §9.6 match-all output per language and score it with the scorer."""
    library = read_library(library_path)
    with tempfile.TemporaryDirectory() as folder:
        outputs: dict[str, Path] = {}
        for lang, path in inputs.items():
            boq = read_boq(path)
            outputs[lang] = Path(folder) / f"label_free_{lang}.csv"
            write_rows(outputs[lang], boq.header, label_free_records(boq, library))
        return score_outputs(outputs, reference_path)


def run_label_free(args: argparse.Namespace) -> int:
    """Print the scorer's label-free top-1 and check it against the measured EN .405 / FR .159."""
    result = label_free_top1(
        {"en": args.input_en, "fr": args.input_fr}, args.library, args.reference
    )
    within = {
        lang: abs(result[lang]["per_level_labelled"]["triple"] - expected) <= LABEL_FREE_TOLERANCE
        for lang, expected in LABEL_FREE_EXPECTED.items()
    }
    payload = {
        "label_free_top1": result,
        "expected": LABEL_FREE_EXPECTED,
        "within_tolerance": within,
    }
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    return EXIT_OK if all(within.values()) else EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser with ``b1`` and ``label-free`` sub-commands."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("b1", "label-free"):
        sub = commands.add_parser(name)
        for flag in ("--input-en", "--input-fr", "--library", "--reference"):
            sub.add_argument(flag, type=Path, required=True)
    b1 = commands.choices["b1"]
    b1.add_argument("--split", type=Path, required=True)
    b1.add_argument("--side", choices=(SIDE_DEV, SIDE_LOCKBOX, SIDE_ALL), default=SIDE_DEV)
    b1.add_argument("--lockbox-session", action="store_true")
    b1.add_argument("--output-en", type=Path, required=True)
    b1.add_argument("--output-fr", type=Path, required=True)
    b1.add_argument("--summary", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested sub-command; return the exit code."""
    args = build_parser().parse_args(argv)
    try:
        return run_b1(args) if args.command == "b1" else run_label_free(args)
    except (BaselineError, OSError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
