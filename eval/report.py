r"""G2 report: risk-coverage, precision by v and precision by gap on dev (G2-T9, A60.10-11).

Inputs (paths are arguments; the defaults are the G2 runs):

- the ``B3-dev-sel`` run folders (the T5 votes re-decided at the selected threshold under A62),
  whose ``audit.jsonl`` gives every line's rule, signals, vetoes and ``raw_line_response``;
- ``eval/selection_v1.json``: the selected threshold, the T1-T8 figures with their per-item
  ``item_correct`` (cross-checked against this recomputation) and the match-all row;
- the B1 folder from G2-T0 (``summary.json`` threshold and ``output_<lang>.csv``) and the B2
  ``R-10.9-evidence`` run folders, scored here with ``eval/score.py --side dev --strict``.

**Risk-coverage** (evaluation-protocol.md §4). The population is the dev item rows not settled
by D0-D2. Lines are sorted by score s = (v, b, confidence bucket), descending; each distinct
score is one step. A vetoed line (D3-D8) or a vote tie never matches, whatever its score. Each
step is plotted as task coverage C₂₅₂ (correct / labelled dev lines) against precision P, with
B1, B2, match-all and T1-T8 marked and the selected threshold highlighted. Bands: 1,000
resamples of the dev Item Nos with a recorded seed, one draw shared by EN, FR and every point,
pointwise 2.5-97.5 percentiles; a resample with nothing matched at a point is dropped there and
counted.

**Precision by v** (A60.10). Population V: dev lines where every pass returned a valid answer,
the plurality kind is ``material`` and the plurality top1 is a library code; v and top1 are
recomputed from ``raw_line_response``. P(top1 triple = GT) per v (blank GT counts as wrong)
and the systematic-error share = wrong with v = k / wrong in V.

**Precision by gap.** Over V with v = k, a line's gap is the weakest
``self_reported_candidate_gap`` among the passes whose top1 is the plurality top1 (decisive >
clear > narrow > tossup): a verbal ordinal judgement, not a probability.

Writes SVG figures (Agg, ``metadata={"Date": None}``), each with a ``<name>.json`` sidecar of
its plotted points, and ``eval/report_v1.json`` (sorted keys, LF). ``selection_v1.json`` is
never written.

Usage::

    python eval/report.py [--run-en runs/<B3-dev-sel en>] [--run-fr ...] [--seed N]

Exit codes: 0 ok, 2 bad input or a disagreement with the selection, 4 refused (a run holds a
non-dev item, or is not B3).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import matplotlib
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from numpy.typing import NDArray
from pydantic import ValidationError

from oris_matcher.domain.attributes import AttrResult
from oris_matcher.domain.decision import ConfidenceBucket, Rule, Signals, candidate_thresholds
from oris_matcher.domain.library import Library, load_library
from oris_matcher.domain.validator import is_valid_code
from oris_matcher.io.audit import AUDIT_FILE, portable_path
from oris_matcher.prompts.v1.schema import LineAnswer
from oris_matcher.service import BUCKET_LABELS, RunProfile

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
DEFAULT_RUNS = {
    "en": RUNS / "20261006T011629Z-f1ab3d3b",
    "fr": RUNS / "20261006T011634Z-a834432c",
}
DEFAULT_B1 = RUNS / "b1-dev-b39e6d7f"
DEFAULT_B2 = {
    "en": RUNS / "20261006T000602Z-53d5729d",
    "fr": RUNS / "20261006T000622Z-e869ef14",
}
DEFAULT_SELECTION = EVAL_DIR / "selection_v1.json"
DEFAULT_REFERENCE = ROOT / "data" / "boq_dataset_matched_GT.csv"
DEFAULT_SPLIT = EVAL_DIR / "split_v1.json"
DEFAULT_CLASSES = EVAL_DIR / "annotations" / "blank_line_classes.csv"
DEFAULT_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
DEFAULT_OUTPUT_JSON = EVAL_DIR / "report_v1.json"
DEFAULT_ASSETS = ROOT / "docs" / "gates" / "assets"
DEFAULT_SEED = 20261006
RESAMPLES = 1000
PERCENTILES = (2.5, 97.5)
SCORER_PATH = EVAL_DIR / "score.py"
SCORER_MODULE = "oris_eval_score_for_report"
B1_SUMMARY = "summary.json"
B1_OUTPUT = "output_{lang}.csv"
OUTPUT_FILE = "output.csv"
MANIFEST_FILE = "manifest.json"
ENCODING = "utf-8"
READ_ENCODING = "utf-8-sig"
NEWLINE = "\n"
JSON_INDENT = 2
LANGUAGES = ("en", "fr")
SIDE_DEV = "dev"
DEV_FIELD = "item_ids_dev"
REFUSAL_SAMPLE = 3
BLANK = ("", "", "")
MATERIAL = "material"
STRUCTURAL_RULES = frozenset({Rule.D0.value, Rule.D0A.value, Rule.D0B.value})
SETTLED_RULES = STRUCTURAL_RULES | {Rule.D1.value, Rule.D1B.value, Rule.D2.value}
SCORED_RULES = frozenset({Rule.D9.value, Rule.D10.value})
STRUCTURAL_REASONS = frozenset({"HEADER", "EMPTY_ROW", "HEADER_UNCONFIRMED"})
GAP_ORDER = ("tossup", "narrow", "clear", "decisive")
GAP_LEVELS = tuple(reversed(GAP_ORDER))
GAP_LABEL = "verbal ordinal judgement, not a probability"
BUCKET_OF_LABEL = {label: bucket for bucket, label in BUCKET_LABELS.items()}
POPULATION_NOTE = "dev item rows not settled by D0-D2 (evaluation-protocol.md §4)"
V_NOTE = (
    "every pass valid, plurality kind material, plurality top1 a library code; v and top1 "
    "recomputed from raw_line_response; blank GT counts as wrong (A60.10)"
)
BOOTSTRAP_NOTE = (
    "Item No. resamples, one draw for EN, FR and every point; pointwise percentiles; a "
    "resample with nothing matched (or no labelled line) at a point is dropped there"
)
BACKEND = "Agg"
SVG_SALT = "oris-g2-report"
FIGURE_SIZE = (6.4, 4.8)
BAR_FIGURE_SIZE = (6.4, 3.6)
TEXT = "#0b0b0b"
TEXT_MUTED = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"
SERIES = {"en": "#2a78d6", "fr": "#eb6834"}
CURVE_COLOUR = "#2a78d6"
BAND_COLOUR = "#9ec2ee"
SELECTED_COLOUR = "#e34948"
REFERENCE_STYLE = {
    "B1": ("s", "#eb6834"),
    "B2": ("D", "#1baf7a"),
    "match-all": ("X", "#52514e"),
}
MARKER_SIZE = 8
SELECTED_SIZE = 16
LINE_WIDTH = 2
BAR_WIDTH = 0.38
BAR_FILL = 0.9
BAR_CENTRE = 0.5
BAR_TOP = 1.3
LABEL_OFFSETS = ((4, 4), (4, -11))
SMALL_FONT = 8
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_REFUSED = 4

FloatArray = NDArray[np.float64]
Rank = tuple[int, int, int]
Triple = tuple[str, str, str]

matplotlib.use(BACKEND)


class ReportError(Exception):
    """An input is missing, malformed or disagrees with the selection; nothing is written."""


class RefusedError(Exception):
    """A run holds a non-dev item or is not B3; nothing is written."""


def load_scorer() -> ModuleType:
    """Load ``eval/score.py`` once, by file path (it stays import-free from ``src``)."""
    if SCORER_MODULE in sys.modules:
        return sys.modules[SCORER_MODULE]
    spec = importlib.util.spec_from_file_location(SCORER_MODULE, SCORER_PATH)
    if spec is None or spec.loader is None:
        raise ReportError(f"cannot load {SCORER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[SCORER_MODULE] = module
    spec.loader.exec_module(module)
    return module


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object."""
    payload: dict[str, Any] = json.loads(path.read_text(encoding=ENCODING))
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read every non-blank line of a JSONL file."""
    text = path.read_text(encoding=ENCODING)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# --- refusals and cross-checks ----------------------------------------------------------


def dev_ids(split: Path) -> list[str]:
    """Return the split's dev Item Nos, sorted: the bootstrap's resampling units."""
    return sorted(read_json(split)[DEV_FIELD])


def refuse_outside(label: str, items: Iterable[str], dev: frozenset[str]) -> None:
    """Refuse when any of ``items`` is not a dev item."""
    outside = sorted({item for item in items if item not in dev})
    if outside:
        raise RefusedError(f"{label} holds non-dev items: {outside[:REFUSAL_SAMPLE]}")


def routed_items(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return the Item No. of every audit record a structural rule did not decide."""
    return [str(record["item_no"]) for record in records if record["rule"] not in STRUCTURAL_RULES]


def output_items(path: Path) -> list[str]:
    """Return the Item No. of every output row that is not a structural (header) row."""
    with path.open(encoding=READ_ENCODING, newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [row["Item No."] for row in rows if row.get("reason", "") not in STRUCTURAL_REASONS]


def check_b3_manifest(lang: str, manifest: Mapping[str, Any], selection: Mapping[str, Any]) -> None:
    """Refuse a non-B3 run; reject one not replayed from the selection's run at its threshold."""
    if manifest.get("profile") != RunProfile.B3.value:
        raise RefusedError(f"{lang} run {manifest.get('run_id')} is not B3")
    expected = selection["inputs"]["run_ids"][lang]
    if manifest.get("source_run_id") != expected:
        raise ReportError(f"{lang} run is not a replay of {expected}")
    selected = selection["selected"]["threshold_id"]
    if manifest.get("threshold_id") != selected:
        raise ReportError(
            f"{lang} run is decided at {manifest.get('threshold_id')}, not {selected}"
        )


def check_library(library: Library, manifests: Mapping[str, Mapping[str, Any]]) -> None:
    """Reject a library file that is not the one the runs were decided with."""
    for lang, manifest in manifests.items():
        if manifest.get("library_sha256") != library.sha256:
            raise ReportError(f"--library is not the {lang} run's library")


# --- votes from raw_line_response -------------------------------------------------------


@dataclass(frozen=True)
class Vote:
    """The plurality vote of one line, recomputed from its passes' raw answers.

    Attributes:
        top1: The plurality top1 (the earliest pass's choice among equal counts).
        votes: v, the passes whose top1 equals it.
        gap: The weakest ``self_reported_candidate_gap`` among those passes.

    """

    top1: str
    votes: int
    gap: str


def parse_answer(raw: str) -> LineAnswer | None:
    """Validate one pass's raw answer; None when it is not a valid ``LineAnswer``."""
    try:
        return LineAnswer.model_validate_json(raw)
    except ValidationError:
        return None


def plurality_is_material(answers: Sequence[LineAnswer]) -> bool:
    """Whether ``material`` is the single most frequent kind among the answers."""
    counts = Counter(answer.kind for answer in answers).most_common()
    tied = len(counts) > 1 and counts[0][1] == counts[1][1]
    return counts[0][0] == MATERIAL and not tied


def read_vote(record: Mapping[str, Any], passes: int) -> Vote | None:
    """Return a line's vote when every pass answered validly and the plurality is a material.

    Args:
        record: One ``audit.jsonl`` record.
        passes: k, the passes every line needs.

    Returns:
        The vote, or None when the line is outside population V (the caller checks the code).

    """
    parsed = [parse_answer(raw) for raw in record["raw_line_response"]]
    answers = [answer for answer in parsed if answer is not None]
    if len(parsed) != passes or len(answers) != passes or not plurality_is_material(answers):
        return None
    counts = Counter(answer.top1 for answer in answers)
    votes = max(counts.values())
    leader = next(answer for answer in answers if counts[answer.top1] == votes)
    supporters = [answer for answer in answers if answer.top1 == leader.top1]
    gap = min((answer.self_reported_candidate_gap for answer in supporters), key=GAP_ORDER.index)
    return Vote(leader.top1, votes, gap)


def is_tie(record: Mapping[str, Any]) -> bool:
    """Whether two top1 codes share the most votes among the line's valid answers."""
    answers = [parse_answer(raw) for raw in record["raw_line_response"]]
    counts = Counter(answer.top1 for answer in answers if answer is not None).most_common()
    return len(counts) > 1 and counts[0][1] == counts[1][1]


# --- one language's lines ---------------------------------------------------------------


@dataclass(frozen=True)
class CurveLine:
    """One population line of the risk-coverage curve.

    Attributes:
        item: Its Item No.
        rank: Score s as a comparable tuple, or None when the line has no score.
        matchable: Whether it can match at some threshold (D9/D10, no veto, no vote tie).
        correct: Whether its plurality top1 triple equals the non-blank GT triple.

    """

    item: str
    rank: Rank | None
    matchable: bool
    correct: bool


@dataclass(frozen=True)
class Truth:
    """What correctness is judged against.

    Attributes:
        library: The library the runs were decided with.
        labels: Item No. -> GT triple (blank when unlabelled).

    """

    library: Library
    labels: dict[str, Triple]

    def is_correct(self, item: str, code: str) -> bool:
        """Whether ``code``'s library triple equals the item's non-blank GT triple."""
        expected = self.labels.get(item, BLANK)
        if expected == BLANK or not is_valid_code(code, self.library):
            return False
        row = self.library.by_code[code]
        return (row.material_type, row.material_usage, row.material_subtype) == expected


def rank_of(signals: Mapping[str, Any] | None) -> Rank | None:
    """Return score s of an audit ``signals`` block as a comparable tuple."""
    if signals is None:
        return None
    bucket = ConfidenceBucket(BUCKET_OF_LABEL[signals["confidence_bucket"]])
    return Signals(int(signals["v"]), AttrResult(signals["b"]), bucket).rank()


def curve_line(record: Mapping[str, Any], truth: Truth) -> CurveLine:
    """Build one population line from its audit record."""
    item = str(record["item_no"])
    rank = rank_of(record.get("signals"))
    scored = record["rule"] in SCORED_RULES and rank is not None
    return CurveLine(
        item=item,
        rank=rank,
        matchable=scored and not is_tie(record),
        correct=truth.is_correct(item, str(record.get("top1", ""))),
    )


def population(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return the dev item records not settled by D0-D2."""
    return [record for record in records if record["rule"] not in SETTLED_RULES]


# --- the curve --------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One step of the curve: every matchable line scoring at or above ``rank``.

    Attributes:
        rank: The step's score.
        items: The matched Item Nos.
        correct_items: The matched Item Nos whose triple is correct.

    """

    rank: Rank
    items: frozenset[str]
    correct_items: frozenset[str]

    @property
    def matched(self) -> int:
        """How many lines match."""
        return len(self.items)

    @property
    def correct(self) -> int:
        """How many matched lines are correct."""
        return len(self.correct_items)


def matched_at(lines: Sequence[CurveLine], minimum: Rank) -> list[CurveLine]:
    """Return the matchable lines whose score is at or above ``minimum``."""
    return [
        line for line in lines if line.matchable and line.rank is not None and line.rank >= minimum
    ]


def step_of(lines: Sequence[CurveLine], minimum: Rank) -> Step:
    """Return what matches at ``minimum``."""
    chosen = matched_at(lines, minimum)
    items = frozenset(line.item for line in chosen)
    return Step(minimum, items, frozenset(line.item for line in chosen if line.correct))


def curve_steps(lines: Sequence[CurveLine]) -> list[Step]:
    """Return the curve, one step per distinct score, strictest first (equal scores: one step)."""
    ranks = {line.rank for line in lines if line.matchable and line.rank is not None}
    return [step_of(lines, rank) for rank in sorted(ranks, reverse=True)]


def score_block(rank: Rank) -> dict[str, Any]:
    """Return a score as the audit writes it: v, b and the confidence bucket label."""
    attributes = AttrResult.AGREE if rank[1] else AttrResult.NO_EVIDENCE
    return {
        "v": rank[0],
        "b": attributes.value,
        "confidence_bucket": BUCKET_LABELS[ConfidenceBucket(rank[2])],
    }


def ratio(numerator: int, denominator: int) -> float | None:
    """Return a ratio, or None over 0."""
    return numerator / denominator if denominator else None


def point_figures(step: Step, size: int, labelled: int) -> dict[str, Any]:
    """Return a point's counts, P, risk, C₂₅₂ and selective coverage."""
    precision = ratio(step.correct, step.matched)
    return {
        "matched": step.matched,
        "correct": step.correct,
        "precision": precision,
        "risk": None if precision is None else 1 - precision,
        "coverage_labelled": ratio(step.correct, labelled),
        "selective_coverage": ratio(step.matched, size),
    }


# --- bootstrap --------------------------------------------------------------------------


def resample_weights(items: Sequence[str], seed: int, resamples: int) -> FloatArray:
    """Return the resample multiplicities: one row per resample, one column per Item No.

    Args:
        items: The resampling units, in a fixed order.
        seed: The recorded seed.
        resamples: How many resamples.

    Returns:
        A (resamples x items) array of how often each item was drawn.

    """
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(items), size=(resamples, len(items)))
    weights = np.zeros((resamples, len(items)), dtype=np.float64)
    np.add.at(weights, (np.arange(resamples)[:, None], draws), 1.0)
    return weights


def draw_sha256(weights: FloatArray) -> str:
    """Return a portable fingerprint of a draw."""
    return hashlib.sha256(np.ascontiguousarray(weights, dtype="<f8").tobytes()).hexdigest()


@dataclass(frozen=True)
class Draw:
    """One bootstrap draw, shared by every point of every language.

    Attributes:
        items: The dev Item Nos, the columns of ``weights``.
        weights: Resample multiplicities.
        labelled: Indicator of the labelled dev items (the C₂₅₂ denominator).

    """

    items: tuple[str, ...]
    weights: FloatArray
    labelled: FloatArray

    def indicator(self, chosen: Iterable[str]) -> FloatArray:
        """Return the 0/1 vector of ``chosen`` over the draw's items."""
        wanted = set(chosen)
        return np.array([float(item in wanted) for item in self.items], dtype=np.float64)


def interval(values: FloatArray) -> list[float] | None:
    """Return the pointwise percentile interval, or None when nothing is left."""
    if not values.size:
        return None
    return [float(value) for value in np.percentile(values, PERCENTILES)]


def band(draw: Draw, step: Step) -> dict[str, Any]:
    """Return a point's percentile band of P and C₂₅₂, and the resamples dropped there."""
    total_matched = draw.weights @ draw.indicator(step.items)
    total_correct = draw.weights @ draw.indicator(step.correct_items)
    total_labelled = draw.weights @ draw.labelled
    keep = (total_matched > 0) & (total_labelled > 0)
    return {
        "precision": interval(total_correct[keep] / total_matched[keep]),
        "coverage_labelled": interval(total_correct[keep] / total_labelled[keep]),
        "dropped": int((~keep).sum()),
    }


# --- inputs, read and checked -----------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """Everything the report reads, after every refusal and cross-check.

    Attributes:
        args: The parsed arguments.
        selection: ``selection_v1.json``.
        manifests: Language -> the ``B3-dev-sel`` manifest.
        records: Language -> its audit records.
        truth: Library and GT labels.
        draw: The bootstrap draw.

    """

    args: argparse.Namespace
    selection: dict[str, Any]
    manifests: dict[str, dict[str, Any]]
    records: dict[str, list[dict[str, Any]]]
    truth: Truth
    draw: Draw

    @property
    def labelled(self) -> int:
        """The C₂₅₂ denominator: labelled dev items."""
        return int(self.draw.labelled.sum())


def b3_runs(args: argparse.Namespace) -> dict[str, Path]:
    """Return language -> ``B3-dev-sel`` run folder."""
    return {"en": args.run_en, "fr": args.run_fr}


def b2_runs(args: argparse.Namespace) -> dict[str, Path]:
    """Return language -> B2 (``R-10.9-evidence``) run folder."""
    return {"en": args.b2_en, "fr": args.b2_fr}


def run_items(folder: Path) -> list[str]:
    """Return a run's routed items: from its audit, else from its output's item rows."""
    audit = folder / AUDIT_FILE
    if audit.is_file():
        return routed_items(read_jsonl(audit))
    return output_items(folder / OUTPUT_FILE)


def check_dev_only(args: argparse.Namespace, dev: frozenset[str]) -> None:
    """Refuse when any B3, B2 or B1 input holds a non-dev item (exit 4)."""
    for lang, folder in b3_runs(args).items():
        refuse_outside(f"{lang} run {folder.name}", run_items(folder), dev)
    for lang, folder in b2_runs(args).items():
        refuse_outside(f"B2 {lang} run {folder.name}", run_items(folder), dev)
    for lang in LANGUAGES:
        output = args.b1 / B1_OUTPUT.format(lang=lang)
        refuse_outside(f"B1 {output.name}", output_items(output), dev)


def read_labels(reference: Path) -> dict[str, Triple]:
    """Return Item No. -> GT triple, read by ``eval/score.py``."""
    scorer = load_scorer()
    try:
        parsed = scorer.parse_reference(scorer.read_table(reference), None, True)
    except scorer.ScoreError as error:
        raise ReportError(f"cannot read {reference}: {error}") from error
    return {str(row.key): tuple(row.labels) for row in parsed.rows}


def make_draw(items: Sequence[str], labels: Mapping[str, Triple], seed: int) -> Draw:
    """Build the one bootstrap draw over the dev Item Nos."""
    labelled = np.array([float(labels.get(item, BLANK) != BLANK) for item in items])
    return Draw(tuple(items), resample_weights(items, seed, RESAMPLES), labelled)


def read_context(args: argparse.Namespace) -> Context:
    """Read every input, refuse non-dev items and check the runs against the selection.

    Raises:
        RefusedError: A non-dev item, or a run that is not B3.
        ReportError: A run that is not the selection's replay, or the wrong library.

    """
    items = dev_ids(args.split)
    check_dev_only(args, frozenset(items))
    selection = read_json(args.selection)
    manifests = {lang: read_json(path / MANIFEST_FILE) for lang, path in b3_runs(args).items()}
    for lang, manifest in manifests.items():
        check_b3_manifest(lang, manifest, selection)
    library = load_library(args.library.read_bytes())
    check_library(library, manifests)
    labels = read_labels(args.reference)
    records = {lang: read_jsonl(path / AUDIT_FILE) for lang, path in b3_runs(args).items()}
    draw = make_draw(items, labels, args.seed)
    return Context(args, selection, manifests, records, Truth(library, labels), draw)


# --- risk-coverage: curve and marks -----------------------------------------------------


def passes_of(context: Context, lang: str) -> int:
    """Return k for one language's run."""
    return int(context.manifests[lang]["passes_k"])


def curve_lines(context: Context, lang: str) -> list[CurveLine]:
    """Return the population lines of one language."""
    return [curve_line(record, context.truth) for record in population(context.records[lang])]


def curve_points(context: Context, lines: Sequence[CurveLine]) -> list[dict[str, Any]]:
    """Return the curve's plotted points, each with its band."""
    return [
        {
            "score": score_block(step.rank),
            **point_figures(step, len(lines), context.labelled),
            "band": band(context.draw, step),
        }
        for step in curve_steps(lines)
    ]


def selection_entries(selection: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return threshold id -> its selection entry."""
    return {str(entry["id"]): entry for entry in selection["thresholds"]}


def check_threshold(lang: str, threshold_id: str, step: Step, figures: Mapping[str, Any]) -> None:
    """Reject a recomputed threshold point that disagrees with the selection's figures."""
    expected = {item for item, value in figures["item_correct"].items() if value}
    counts = (int(figures["matched"]), int(figures["correct"]))
    if counts != (step.matched, step.correct) or expected != set(step.correct_items):
        raise ReportError(
            f"{lang} {threshold_id}: recomputed {step.matched} matched / {step.correct} correct "
            f"disagrees with the selection's {counts[0]} / {counts[1]} or its item_correct"
        )


def threshold_mark(
    context: Context, lang: str, step: Step, threshold_id: str, figures: Mapping[str, Any]
) -> dict[str, Any]:
    """Return one threshold's mark: the selection's P and C₂₅₂, with its band."""
    check_threshold(lang, threshold_id, step, figures)
    return {
        "name": threshold_id,
        "kind": "threshold",
        "matched": step.matched,
        "correct": step.correct,
        "precision": figures["precision"],
        "coverage_labelled": figures["coverage_labelled"]["value"],
        "selected": threshold_id == context.selection["selected"]["threshold_id"],
        "band": band(context.draw, step),
    }


def threshold_marks(
    context: Context, lang: str, lines: Sequence[CurveLine]
) -> list[dict[str, Any]]:
    """Return the marks of T1-T8, checked against ``selection_v1.json``."""
    entries = selection_entries(context.selection)
    marks = []
    for threshold in candidate_thresholds(passes_of(context, lang)):
        if threshold.threshold_id not in entries:
            raise ReportError(f"the selection has no {threshold.threshold_id}")
        step = step_of(lines, threshold.minimum.rank())
        figures = entries[threshold.threshold_id]["per_language"][lang]
        marks.append(threshold_mark(context, lang, step, threshold.threshold_id, figures))
    return marks


def scored_figures(output: Path, lang: str, args: argparse.Namespace) -> dict[str, Any]:
    """Score one output with ``eval/score.py --side dev --strict``; return P and C₂₅₂."""
    scorer = load_scorer()
    argv = [
        *("--output", str(output), "--label", lang, "--reference", str(args.reference)),
        *("--split", str(args.split), "--side", SIDE_DEV, "--strict"),
        *("--classes", str(args.classes)),
    ]
    try:
        report = scorer.run(scorer.parse_settings(argv))["outputs"][0]
    except scorer.ScoreError as error:
        raise ReportError(f"the scorer rejected {output}: {error}") from error
    precision = report["precision"]
    return {
        "matched": precision["matched"],
        "correct": precision["correct"],
        "precision": precision["value"],
        "coverage_labelled": report["coverage_labelled"]["value"],
    }


def reference_mark(name: str, figures: Mapping[str, Any]) -> dict[str, Any]:
    """Return a reference row's mark (never a candidate, no band)."""
    return {
        "name": name,
        "kind": "reference",
        "matched": figures["matched"],
        "correct": figures["correct"],
        "precision": figures["precision"],
        "coverage_labelled": figures["coverage_labelled"],
        "selected": False,
        "band": None,
    }


def reference_marks(context: Context, lang: str) -> list[dict[str, Any]]:
    """Return the B1, B2 and match-all marks of one language."""
    args = context.args
    match_all = dict(context.selection["reference_rows"]["match_all"]["per_language"][lang])
    match_all["coverage_labelled"] = match_all["coverage_labelled"]["value"]
    return [
        reference_mark("B1", scored_figures(args.b1 / B1_OUTPUT.format(lang=lang), lang, args)),
        reference_mark("B2", scored_figures(b2_runs(args)[lang] / OUTPUT_FILE, lang, args)),
        reference_mark("match-all", match_all),
    ]


def risk_coverage(context: Context, lang: str) -> dict[str, Any]:
    """Return one language's population, curve and marks."""
    lines = curve_lines(context, lang)
    rules = Counter(str(record["rule"]) for record in population(context.records[lang]))
    return {
        "population": {"lines": len(lines), "by_rule": dict(sorted(rules.items()))},
        "labelled": context.labelled,
        "curve": curve_points(context, lines),
        "marks": [*threshold_marks(context, lang, lines), *reference_marks(context, lang)],
    }


# --- precision by v and by gap ----------------------------------------------------------


def votes_of(context: Context, lang: str) -> list[tuple[Vote, bool]]:
    """Return population V of one language: each line's vote and whether its top1 is right."""
    found = []
    for record in context.records[lang]:
        vote = read_vote(record, passes_of(context, lang))
        if vote is None or not is_valid_code(vote.top1, context.truth.library):
            continue
        found.append((vote, context.truth.is_correct(str(record["item_no"]), vote.top1)))
    return found


def level(outcomes: Sequence[bool]) -> dict[str, Any]:
    """Return n, correct and precision of a group of lines."""
    correct = sum(outcomes)
    return {"n": len(outcomes), "correct": correct, "precision": ratio(correct, len(outcomes))}


def by_v_block(votes: Sequence[tuple[Vote, bool]], passes: int) -> dict[str, Any]:
    """Return P(top1 triple = GT) per v over population V."""
    return {
        "population": len(votes),
        "passes": passes,
        "levels": {
            str(count): level([correct for vote, correct in votes if vote.votes == count])
            for count in range(1, passes + 1)
        },
    }


def systematic_block(votes: Sequence[tuple[Vote, bool]], passes: int) -> dict[str, Any]:
    """Return the systematic-error share: wrong with v = k over wrong in V."""
    wrong = [vote for vote, correct in votes if not correct]
    unanimous = sum(vote.votes == passes for vote in wrong)
    return {"wrong_v_k": unanimous, "wrong": len(wrong), "value": ratio(unanimous, len(wrong))}


def by_gap_block(votes: Sequence[tuple[Vote, bool]], passes: int) -> dict[str, Any]:
    """Return precision per weakest-supporter gap over V with v = k, decisive first."""
    unanimous = [(vote, correct) for vote, correct in votes if vote.votes == passes]
    return {
        "population": len(unanimous),
        "levels": {
            gap: level([correct for vote, correct in unanimous if vote.gap == gap])
            for gap in reversed(GAP_ORDER)
        },
    }


def vote_blocks(context: Context) -> dict[str, Any]:
    """Return ``by_v``, ``systematic_error_share`` and ``by_gap`` for every language."""
    blocks: dict[str, Any] = {
        "by_v": {},
        "systematic_error_share": {},
        "by_gap": {"label": GAP_LABEL},
    }
    for lang in LANGUAGES:
        votes, passes = votes_of(context, lang), passes_of(context, lang)
        blocks["by_v"][lang] = by_v_block(votes, passes)
        blocks["systematic_error_share"][lang] = systematic_block(votes, passes)
        blocks["by_gap"][lang] = by_gap_block(votes, passes)
    return blocks


# --- figures ----------------------------------------------------------------------------


def style_axes(axes: Axes, title: str) -> None:
    """Apply the recessive grid, muted spines and the title."""
    axes.set_facecolor(SURFACE)
    axes.grid(color=GRID, linewidth=1)
    axes.set_axisbelow(True)
    for spine in axes.spines.values():
        spine.set_color(GRID)
    axes.tick_params(colors=TEXT_MUTED, labelsize=SMALL_FONT)
    axes.set_title(title, color=TEXT, fontsize=SMALL_FONT + 2, loc="left")


def draw_band(axes: Axes, point: Mapping[str, Any]) -> None:
    """Draw a point's 95% band as horizontal (C₂₅₂) and vertical (P) error bars."""
    bounds = point["band"]
    if not bounds or bounds["precision"] is None or point["precision"] is None:
        return
    x_value, y_value = point["coverage_labelled"], point["precision"]
    low_x, high_x = bounds["coverage_labelled"]
    low_y, high_y = bounds["precision"]
    axes.errorbar(
        [x_value],
        [y_value],
        xerr=[[max(0.0, x_value - low_x)], [max(0.0, high_x - x_value)]],
        yerr=[[max(0.0, y_value - low_y)], [max(0.0, high_y - y_value)]],
        fmt="none",
        ecolor=BAND_COLOUR,
        elinewidth=1,
        capsize=2,
        zorder=1,
    )


def plot_curve(axes: Axes, curve: Sequence[Mapping[str, Any]]) -> None:
    """Plot the score steps as a line through their (C₂₅₂, P) points, with their bands."""
    shown = [point for point in curve if point["precision"] is not None]
    for point in shown:
        draw_band(axes, point)
    axes.plot(
        [point["coverage_labelled"] for point in shown],
        [point["precision"] for point in shown],
        color=CURVE_COLOUR,
        linewidth=LINE_WIDTH,
        marker="o",
        markersize=MARKER_SIZE / 2,
        label="B3, one point per score step (95% bands)",
        zorder=2,
    )


def grouped_threshold_labels(
    marks: Sequence[Mapping[str, Any]],
) -> dict[tuple[float, float], str]:
    """Return one label per plotted threshold position (ids at one position are joined)."""
    groups: dict[tuple[float, float], list[str]] = {}
    for mark in marks:
        if mark["kind"] == "threshold" and mark["precision"] is not None:
            position = (mark["coverage_labelled"], mark["precision"])
            groups.setdefault(position, []).append(mark["name"])
    return {position: ", ".join(names) for position, names in groups.items()}


def plot_selected(axes: Axes, marks: Sequence[Mapping[str, Any]]) -> None:
    """Highlight the selected threshold with a star."""
    for mark in marks:
        if mark["selected"] and mark["precision"] is not None:
            axes.plot(
                [mark["coverage_labelled"]],
                [mark["precision"]],
                linestyle="none",
                marker="*",
                markersize=SELECTED_SIZE,
                color=SELECTED_COLOUR,
                markeredgecolor=SURFACE,
                label=f"selected {mark['name']}",
                zorder=4,
            )


def plot_thresholds(axes: Axes, marks: Sequence[Mapping[str, Any]]) -> None:
    """Label T1-T8 on the curve and highlight the selected threshold."""
    labels = grouped_threshold_labels(marks).items()
    for index, ((x_value, y_value), text) in enumerate(labels):
        axes.annotate(
            text,
            (x_value, y_value),
            xytext=LABEL_OFFSETS[index % len(LABEL_OFFSETS)],
            textcoords="offset points",
            fontsize=SMALL_FONT,
            color=TEXT_MUTED,
        )
    plot_selected(axes, marks)


def plot_references(axes: Axes, marks: Sequence[Mapping[str, Any]]) -> None:
    """Plot the B1, B2 and match-all reference marks."""
    for mark in marks:
        if mark["kind"] != "reference" or mark["precision"] is None:
            continue
        shape, colour = REFERENCE_STYLE[mark["name"]]
        axes.plot(
            [mark["coverage_labelled"]],
            [mark["precision"]],
            linestyle="none",
            marker=shape,
            markersize=MARKER_SIZE,
            color=colour,
            markeredgecolor=SURFACE,
            label=mark["name"],
            zorder=3,
        )


def risk_coverage_figure(sidecar: Mapping[str, Any]) -> Figure:
    """Draw one language's risk-coverage figure from its sidecar points."""
    figure = Figure(figsize=FIGURE_SIZE, facecolor=SURFACE)
    axes = figure.add_subplot()
    lang, labelled = sidecar["lang"].upper(), sidecar["labelled"]
    style_axes(axes, f"Risk-coverage, {lang} dev, score s = (v, b, confidence)")
    plot_curve(axes, sidecar["curve"])
    plot_thresholds(axes, sidecar["marks"])
    plot_references(axes, sidecar["marks"])
    axes.set_xlabel(f"task coverage $C_{{252}}$ (correct / {labelled} labelled dev lines)")
    axes.set_ylabel("matched precision P")
    axes.legend(fontsize=SMALL_FONT, frameon=False, loc="lower right")
    figure.tight_layout()
    return figure


def bar_figure(title: str, groups: Sequence[str], levels: Mapping[str, Any]) -> Figure:
    """Draw precision per group as bars, one series per language, with n over each bar."""
    figure = Figure(figsize=BAR_FIGURE_SIZE, facecolor=SURFACE)
    axes = figure.add_subplot()
    style_axes(axes, title)
    for index, lang in enumerate(LANGUAGES):
        cells = [levels[lang]["levels"][group] for group in groups]
        offsets = [position + (index - BAR_CENTRE) * BAR_WIDTH for position in range(len(groups))]
        heights = [cell["precision"] or 0.0 for cell in cells]
        bars = axes.bar(
            offsets, heights, BAR_WIDTH * BAR_FILL, color=SERIES[lang], label=lang.upper()
        )
        axes.bar_label(bars, [f"n={cell['n']}" for cell in cells], fontsize=SMALL_FONT, color=TEXT)
    axes.set_xticks(range(len(groups)), list(groups))
    axes.set_ylim(0, BAR_TOP)
    axes.set_ylabel("P(top1 triple = GT)")
    axes.legend(fontsize=SMALL_FONT, frameon=False, loc="upper right", ncols=len(LANGUAGES))
    figure.tight_layout()
    return figure


def save_svg(figure: Figure, path: Path) -> None:
    """Save a figure as an SVG with no date and stable ids, so reruns are byte-identical."""
    with matplotlib.rc_context({"svg.hashsalt": SVG_SALT}):
        figure.savefig(path, format="svg", metadata={"Date": None}, facecolor=SURFACE)


# --- outputs ----------------------------------------------------------------------------


def dumps(payload: Mapping[str, Any]) -> bytes:
    """Serialise JSON with sorted keys and LF line ends."""
    text = json.dumps(payload, sort_keys=True, indent=JSON_INDENT, ensure_ascii=False)
    return (text + NEWLINE).encode(ENCODING)


FigureSpec = tuple[str, dict[str, Any], Figure]


def curve_specs(payload: Mapping[str, Any]) -> list[FigureSpec]:
    """Return (name, sidecar, figure) of each language's risk-coverage figure."""
    specs = []
    for lang in LANGUAGES:
        name = f"risk_coverage_{lang}"
        sidecar = {"figure": name, "lang": lang, **payload["risk_coverage"][lang]}
        sidecar["bootstrap"] = payload["bootstrap"]
        specs.append((name, sidecar, risk_coverage_figure(sidecar)))
    return specs


def bar_specs(payload: Mapping[str, Any]) -> list[FigureSpec]:
    """Return (name, sidecar, figure) of the precision-by-v and precision-by-gap figures."""
    by_v = {
        "figure": "precision_by_v",
        "by_v": payload["by_v"],
        "systematic_error_share": payload["systematic_error_share"],
    }
    passes = max(int(block["passes"]) for block in payload["by_v"].values())
    votes = [str(count) for count in range(1, passes + 1)]
    by_gap = {"figure": "precision_by_gap", "by_gap": payload["by_gap"]}
    gap_title = f"Precision by weakest-supporter gap, v = k{NEWLINE}({GAP_LABEL})"
    return [
        (
            "precision_by_v",
            by_v,
            bar_figure("Precision by votes v (dev, V)", votes, payload["by_v"]),
        ),
        ("precision_by_gap", by_gap, bar_figure(gap_title, GAP_LEVELS, payload["by_gap"])),
    ]


def inputs_block(context: Context) -> dict[str, Any]:
    """Return what the report was computed from: paths, hashes and run ids."""
    args = context.args
    files = {
        "selection": args.selection,
        "split": args.split,
        "reference": args.reference,
        "classes": args.classes,
        "library": args.library,
    }
    runs = {f"b3_{lang}": path for lang, path in b3_runs(args).items()}
    runs |= {f"b2_{lang}": path for lang, path in b2_runs(args).items()} | {"b1": args.b1}
    manifests = context.manifests
    return {
        "paths": {name: portable_path(path, ROOT) for name, path in {**files, **runs}.items()},
        "sha256": {name: file_sha256(path) for name, path in files.items()},
        "run_ids": {lang: manifest["run_id"] for lang, manifest in manifests.items()},
        "source_run_ids": {lang: manifest["source_run_id"] for lang, manifest in manifests.items()},
        "selected": context.selection["selected"]["threshold_id"],
        "b1_threshold": read_json(args.b1 / B1_SUMMARY)["threshold"],
    }


def bootstrap_block(context: Context) -> dict[str, Any]:
    """Return the recorded bootstrap settings and the draw's fingerprint."""
    return {
        "seed": context.args.seed,
        "resamples": RESAMPLES,
        "unit": "Item No. (dev)",
        "n_items": len(context.draw.items),
        "percentiles": list(PERCENTILES),
        "draw_sha256": draw_sha256(context.draw.weights),
        "note": BOOTSTRAP_NOTE,
    }


def build_payload(context: Context) -> dict[str, Any]:
    """Assemble ``report_v1.json`` (the figure list is added once they are written)."""
    return {
        "inputs": inputs_block(context),
        "bootstrap": bootstrap_block(context),
        "risk_coverage": {lang: risk_coverage(context, lang) for lang in LANGUAGES},
        **vote_blocks(context),
        "notes": {"population": POPULATION_NOTE, "by_v": V_NOTE, "by_gap": GAP_LABEL},
    }


def write_outputs(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    """Write every figure with its sidecar, then ``report_v1.json``."""
    specs = [*curve_specs(payload), *bar_specs(payload)]
    assets: Path = args.assets_dir
    assets.mkdir(parents=True, exist_ok=True)
    written = []
    for name, sidecar, figure in specs:
        save_svg(figure, assets / f"{name}.svg")
        (assets / f"{name}.json").write_bytes(dumps(sidecar))
        written += [assets / f"{name}.svg", assets / f"{name}.json"]
    payload["figures"] = [portable_path(path, ROOT) for path in written]
    output: Path = args.output_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(dumps(payload))


# --- command line -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser; every path defaults to the G2 inputs."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    paths = {
        "--run-en": DEFAULT_RUNS["en"],
        "--run-fr": DEFAULT_RUNS["fr"],
        "--selection": DEFAULT_SELECTION,
        "--b1": DEFAULT_B1,
        "--b2-en": DEFAULT_B2["en"],
        "--b2-fr": DEFAULT_B2["fr"],
        "--reference": DEFAULT_REFERENCE,
        "--split": DEFAULT_SPLIT,
        "--classes": DEFAULT_CLASSES,
        "--library": DEFAULT_LIBRARY,
        "--assets-dir": DEFAULT_ASSETS,
        "--output-json": DEFAULT_OUTPUT_JSON,
    }
    for flag, default in paths.items():
        parser.add_argument(flag, type=Path, default=default)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build the report; return the exit code.

    Args:
        argv: Arguments; ``sys.argv[1:]`` when None.

    Returns:
        0 ok, 2 bad input or a disagreement with the selection, 4 refused.

    """
    args = build_parser().parse_args(argv)
    try:
        payload = build_payload(read_context(args))
        write_outputs(args, payload)
    except RefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (ReportError, OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    print(f"wrote {args.output_json} and {len(payload['figures'])} figure files")
    return EXIT_OK


def use_utf8_streams() -> None:
    """Print UTF-8 whatever the console code page is."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding=ENCODING)


if __name__ == "__main__":
    use_utf8_streams()
    sys.exit(main())
