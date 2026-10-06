r"""The dev error taxonomy: ``eval/errors_dev.csv`` and the labeller packet (§10.7; G2-T7).

``build`` reads the two ``B3-dev-sel`` run folders (``audit.jsonl`` and ``output.csv``, decided
at the selected threshold under A62), the inputs, the global library, the reference, the split
and the blank-line classes. Every dev item line is joined to the reference with
``eval/score.py``'s ``parse_reference``, ``join_by_key`` and ``build_scope`` (whose ``classify``
gives the reference class), and gets an error kind (evaluation-protocol.md §6, A60.7):

- ``false_match``: matched, labelled, triple different from the reference;
- ``match_on_blank``: matched on a blank-reference line (E, S or A);
- ``false_nm``: ``not_a_material`` on a labelled or E line;
- ``missed``: ``needs_review`` with the top1 triple equal to the reference;
- ``wrong_proposal_reviewed``: ``needs_review`` with a different top1, or none.

A blank-reference line in ``needs_review`` or ``not_a_material`` is not an error. The first
wrong level comes from score.py's ``level_hits``; it is ``decision`` for missed, false_nm and
match_on_blank, and ``type`` when there is no proposal. v and each pass's answer are recomputed
from ``raw_line_response``. A ``gt_suspect`` candidate (A60.8) is an error row whose passes all
returned valid ``material`` answers with the same library top1 (v = k), that triple differing
from the reference, blank reference included. The cause columns stay blank: the panel fills
them. The packet (``errors_dev_packet.jsonl``) gives the labellers each error line's text,
unit, section path, reference triple, every pass decoded to triples, the extracted attributes,
the sibling rows of the reference and predicted types and the 7 cause definitions, never a
panel output. ``--show ITEM`` prints the decision, rule and reason of any Item No.

``merge`` folds ``labels_A``, ``labels_B`` and the adjudication into the error rows, checks
every cause against the 7, and writes the summary: raw agreement and unweighted Cohen's κ on
the primary cause (pooled and per language, with n; "undefined" when p_e = 1, A60.9) and the
per-language triggers (primary cause, ≥ 5 rows or ≥ 20% of that language's error rows; missed
rows count, owner decision D1 = A, A60.15). A Pareto weighting precision and safety kinds
2 to 1 is for display only.

Usage::

    python eval/errors_dev.py build --run-en runs/<en> --run-fr runs/<fr> [--show ITEM]
    python eval/errors_dev.py merge --labels-a A.csv --labels-b B.csv --adjudication J.csv

Exit codes: 0 ok, 2 bad input, 4 refused (a run routed an item outside the dev side).
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import importlib.util
import io
import json
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import ValidationError

from oris_matcher.domain.attributes import extract
from oris_matcher.domain.boq import BoqLine
from oris_matcher.domain.decision import Decision, Rule
from oris_matcher.domain.library import Library, load_library
from oris_matcher.domain.validator import is_valid_code
from oris_matcher.io.audit import AUDIT_FILE
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.prompts.v1.schema import LineAnswer

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).resolve().parent
ANNOTATIONS_DIR = EVAL_DIR / "annotations"
DEFAULT_INPUTS = {
    "en": ROOT / "input" / "boq_dataset_input_en.csv",
    "fr": ROOT / "input" / "boq_dataset_input_fr.csv",
}
DEFAULT_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
DEFAULT_REFERENCE = ROOT / "data" / "boq_dataset_matched_GT.csv"
DEFAULT_SPLIT = EVAL_DIR / "split_v1.json"
DEFAULT_CLASSES = ANNOTATIONS_DIR / "blank_line_classes.csv"
DEFAULT_OUTPUT = EVAL_DIR / "errors_dev.csv"
DEFAULT_PACKET = ANNOTATIONS_DIR / "errors_dev_packet.jsonl"
DEFAULT_LABELS_A = ANNOTATIONS_DIR / "errors_dev_labels_A.csv"
DEFAULT_LABELS_B = ANNOTATIONS_DIR / "errors_dev_labels_B.csv"
DEFAULT_ADJUDICATION = ANNOTATIONS_DIR / "errors_dev_adjudication.csv"
DEFAULT_SUMMARY = ANNOTATIONS_DIR / "errors_dev_summary.json"
SCORER_PATH = EVAL_DIR / "score.py"
SCORER_MODULE = "oris_eval_score_for_errors_dev"
OUTPUT_FILE = "output.csv"
MANIFEST_FILE = "manifest.json"
ENCODING = "utf-8"
NEWLINE = "\n"
JSON_INDENT = 2
LANGUAGES = ("en", "fr")
SIDE_DEV = "dev"
DEV_FIELD = "item_ids_dev"
REFUSAL_SAMPLE = 3
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_REFUSED = 4

Triple = tuple[str, str, str]
BLANK: Triple = ("", "", "")
TRIPLE_SEPARATOR = " | "
PATH_PART_SEPARATOR = " "
TEXT_SEPARATOR = " "
STRUCTURAL_RULES = frozenset({Rule.D0.value, Rule.D0A.value, Rule.D0B.value})
MATCHED = Decision.MATCHED.value
NOT_A_MATERIAL = Decision.NOT_A_MATERIAL.value
NEEDS_REVIEW = Decision.NEEDS_REVIEW.value
CLASS_LABELLED = "L"
CLASS_NO_EQUIVALENT = "E"
FALSE_NM_CLASSES = frozenset({CLASS_LABELLED, CLASS_NO_EQUIVALENT})
KIND_MATERIAL = "material"

FALSE_MATCH = "false_match"
MATCH_ON_BLANK = "match_on_blank"
FALSE_NM = "false_nm"
MISSED = "missed"
WRONG_PROPOSAL = "wrong_proposal_reviewed"
KIND_DEFINITIONS = {
    FALSE_MATCH: "matched, labelled line, triple different from the reference",
    MATCH_ON_BLANK: "matched on a line whose reference is blank (E, S or A)",
    FALSE_NM: "not_a_material on a labelled line or a material without library equivalent (E)",
    MISSED: "needs_review, while the top1 triple equals the reference (a correct proposal)",
    WRONG_PROPOSAL: "needs_review, the top1 triple differs from the reference or is absent",
}
DECISION_LEVEL_KINDS = frozenset({MISSED, FALSE_NM, MATCH_ON_BLANK})
WEIGHTED_KINDS = frozenset({FALSE_MATCH, MATCH_ON_BLANK, FALSE_NM})
PARETO_WEIGHT = 2
LEVEL_TYPE = "type"
LEVEL_USAGE = "usage"
LEVEL_SUBTYPE = "subtype"
LEVEL_DECISION = "decision"
TRIPLE_LEVELS = (LEVEL_TYPE, LEVEL_USAGE, LEVEL_SUBTYPE)

CAUSE_DEFINITIONS = {
    "lexical_gap": (
        "the term is not bridged between line and library (FR acronyms such as GNT, BPE, "
        "GB/BBSG, 'tiède')"
    ),
    "usage_confuser": (
        "a known confusable pair (piers/piles, base/binder, pile caps -> footings, segmental "
        "rings, ready-mix vs element)"
    ),
    "subtype_parse": (
        "an attribute was mis-extracted or not extracted (strength, CEM, RAP/AE %, EWC, grade)"
    ),
    "header_context": "the answer needs L0/L1 context that was missing or ignored",
    "not_in_library": "there is no valid row, but the system forced one",
    "llm_failure": "timeout, malformed output, missing ID, truncation",
    "gt_convention": (
        "the GT follows a project convention the text does not support, such as 09.01 "
        "ready-mix or limestone filler -> Limestone/for all usage"
    ),
}
CAUSES = tuple(CAUSE_DEFINITIONS)
MISSED_CAUSE_RULE = (
    "A60.15 (D1 = A): a missed row gets the cause of the signal that failed: llm_failure for "
    "D1/D1b, otherwise the best fit for why v or signal b failed"
)
LABELLING_RULE = "exactly one primary cause from the 7 per row, plus an optional cause_2 from the 7"
USAGE_SPLITS = frozenset({"", "element_right_usage_wrong", "element_wrong"})
TRIGGER_MIN_ROWS = 5
TRIGGER_SHARE_PERCENT = 20
PERCENT = 100
PROVENANCE = "builder labels (assistant panel)"
TRUE = "true"
FALSE = "false"
RULINGS = frozenset({TRUE, FALSE})
CANDIDATE_YES = "1"
CANDIDATE_NO = "0"

PROTOCOL_FIELDS = (
    "item_id",
    "lang",
    "split",
    "family",
    "kind",
    "level_first_wrong",
    "usage_split",
    "gt_suspect",
    "gt_suspect_reason",
    "cause",
    "cause_2",
    "evidence",
)
TICKET_FIELDS = (
    "top1",
    "reason",
    "v",
    "labeller_a",
    "labeller_b",
    "adjudication_reason",
    "provenance",
    "date",
)
SIGNAL_FIELDS = ("rule", "signal_b", "confidence_bucket", "gt_suspect_candidate")
ERROR_FIELDS = (*PROTOCOL_FIELDS, *TICKET_FIELDS, *SIGNAL_FIELDS)
LABEL_FIELDS = ("item_id", "lang", "cause", "cause_2", "usage_split", "evidence")
ADJUDICATION_FIELDS = (*LABEL_FIELDS, "adjudication_reason", "gt_suspect", "gt_suspect_reason")

Key = tuple[str, str]
Row = dict[str, str]


class RefusedError(Exception):
    """A run holds an item outside the dev side; nothing is written."""


class BuildError(Exception):
    """The runs, inputs or library do not fit together; nothing is written."""


class MergeError(Exception):
    """The panel files are incomplete or carry an out-of-list value; nothing is written."""


def load_scorer() -> ModuleType:
    """Load ``eval/score.py`` once, by file path (it imports nothing from ``src``)."""
    if SCORER_MODULE in sys.modules:
        return sys.modules[SCORER_MODULE]
    spec = importlib.util.spec_from_file_location(SCORER_MODULE, SCORER_PATH)
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load {SCORER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[SCORER_MODULE] = module
    spec.loader.exec_module(module)
    return module


# --- kinds, levels, family -------------------------------------------------------------


def error_kind(decision: str, ref_class: str, gt: Triple, top1: Triple | None) -> str | None:
    """Return a dev line's error kind (protocol §6), or None when it is not an error.

    Args:
        decision: The output decision.
        ref_class: The reference class (L, E, S, A or blank).
        gt: The reference triple; blank for E, S and A lines.
        top1: The matched triple, or the proposal of an unmatched line; None for no proposal.

    Returns:
        One of the five kinds, or None.

    """
    blank_gt = gt == BLANK
    if decision == MATCHED:
        if blank_gt:
            return MATCH_ON_BLANK
        return None if top1 == gt else FALSE_MATCH
    if decision == NOT_A_MATERIAL:
        return FALSE_NM if ref_class in FALSE_NM_CLASSES else None
    if decision == NEEDS_REVIEW and not blank_gt:
        return MISSED if top1 == gt else WRONG_PROPOSAL
    return None


def first_wrong_level(kind: str, gt: Triple, top1: Triple | None) -> str:
    """Return the first wrong level: ``decision`` for decision kinds, else the first miss.

    Raises:
        ValueError: A triple-level kind whose proposal equals the reference.

    """
    if kind in DECISION_LEVEL_KINDS:
        return LEVEL_DECISION
    if top1 is None:
        return LEVEL_TYPE
    hits = load_scorer().level_hits(top1, gt)
    for level, hit in zip(TRIPLE_LEVELS, hits, strict=True):
        if not hit:
            return level
    raise ValueError(f"{kind}: the proposal {top1} equals the reference")


def family_of(gt: Triple, top1: Triple | None) -> str:
    """Return the reference type, else the predicted type, else blank."""
    if gt != BLANK:
        return gt[0]
    return top1[0] if top1 is not None else ""


# --- passes ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedPass:
    """One pass's raw line response, validated against ``LineAnswer``.

    Attributes:
        raw: The raw response text.
        answer: The validated answer, or None.
        error: Why it did not validate; blank when it did.

    """

    raw: str
    answer: LineAnswer | None
    error: str


def parse_pass(raw: str) -> ParsedPass:
    """Validate one raw line response strictly; an invalid one keeps its first error."""
    try:
        return ParsedPass(raw, LineAnswer.model_validate_json(raw), "")
    except ValidationError as error:
        return ParsedPass(raw, None, str(error.errors()[0]["msg"]))


def valid_answers(raws: Sequence[str]) -> list[LineAnswer]:
    """Return the valid answers of a line, in pass order."""
    parsed = (parse_pass(raw).answer for raw in raws)
    return [answer for answer in parsed if answer is not None]


def votes(raws: Sequence[str]) -> int | None:
    """Return v: the most passes sharing one top1 among the valid answers; None if none."""
    counts = Counter(answer.top1 for answer in valid_answers(raws))
    return max(counts.values()) if counts else None


def read_library(path: Path, catalogue: str | None) -> Library:
    """Load a library file; its codes are the ones the runs' answers name."""
    return load_library(path.read_bytes(), catalogue=catalogue)


def triple_of(code: str, library: Library) -> Triple | None:
    """Return a library code's triple, or None when the code names no row."""
    if not is_valid_code(code, library):
        return None
    row = library.by_code[code]
    return (row.material_type, row.material_usage, row.material_subtype)


def is_gt_suspect_candidate(
    raws: Sequence[str], passes_k: int, library: Library, gt: Triple
) -> bool:
    """Whether all k passes are valid material answers on one library top1 that is not GT.

    Args:
        raws: The line's raw responses, one per pass.
        passes_k: The run's k.
        library: The library the codes name.
        gt: The reference triple, blank included (A60.8).

    Returns:
        True for a candidate.

    """
    answers = valid_answers(raws)
    if len(raws) != passes_k or len(answers) != passes_k:
        return False
    tops = {answer.top1 for answer in answers}
    if len(tops) != 1 or any(answer.kind != KIND_MATERIAL for answer in answers):
        return False
    triple = triple_of(tops.pop(), library)
    return triple is not None and triple != gt


# --- runs ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunData:
    """One language's run folder and its input.

    Attributes:
        lang: ``en`` or ``fr``.
        run_dir: The run folder.
        manifest: Its manifest.
        records: Its audit records, in output row order.
        lines: The input's lines, indexed by position.

    """

    lang: str
    run_dir: Path
    manifest: dict[str, Any]
    records: list[dict[str, Any]]
    lines: tuple[BoqLine, ...]

    @property
    def passes_k(self) -> int:
        """The run's k."""
        return int(self.manifest["passes_k"])


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSON-lines file."""
    lines = path.read_text(encoding=ENCODING).splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def load_run(lang: str, run_dir: Path, input_path: Path) -> RunData:
    """Read a run folder and its input; refuse an input the run did not read.

    Raises:
        BuildError: The input's SHA-256 is not the manifest's.

    """
    manifest = json.loads((run_dir / MANIFEST_FILE).read_text(encoding=ENCODING))
    data = input_path.read_bytes()
    recorded = manifest.get("input_sha256")
    if recorded is not None and recorded != hashlib.sha256(data).hexdigest():
        raise BuildError(f"{lang}: {input_path.name} is not the input of {run_dir.name}")
    records = read_jsonl(run_dir / AUDIT_FILE)
    return RunData(lang, run_dir, manifest, records, read_boq(data).lines)


def dev_ids(split: Path) -> frozenset[str]:
    """Return the split's dev item ids."""
    return frozenset(json.loads(split.read_text(encoding=ENCODING))[DEV_FIELD])


def check_dev(run: RunData, dev: frozenset[str]) -> None:
    """Refuse a run that routed an item outside the dev side.

    Raises:
        RefusedError: Such an item exists.

    """
    routed = {
        str(record["item_no"]) for record in run.records if record["rule"] not in STRUCTURAL_RULES
    }
    outside = sorted(routed - dev)
    if outside:
        sample = outside[:REFUSAL_SAMPLE]
        raise RefusedError(f"{run.lang} run {run.run_dir.name} holds non-dev items: {sample}")


def check_library(run: RunData, library: Library) -> None:
    """Refuse a library that is not the one the run decided with.

    Raises:
        BuildError: The SHA-256 differs from the manifest's.

    """
    if run.manifest.get("library_sha256") != library.sha256:
        raise BuildError(f"{run.lang}: the library is not the one of {run.run_dir.name}")


# --- joining the run to the reference --------------------------------------------------


@dataclass(frozen=True)
class ScoreInputs:
    """The reference side of the join.

    Attributes:
        reference: The ground-truth CSV.
        split: The split file.
        classes: The blank-line class list.

    """

    reference: Path
    split: Path
    classes: Path


@dataclass(frozen=True)
class Case:
    """One dev item line of one language, joined to its reference row and audit record.

    Attributes:
        lang: The language.
        item_id: The Item No.
        ref_class: The reference class.
        gt: The reference triple.
        decision: The output decision.
        top1: The matched triple, else the proposal; None for no proposal.
        record: The audit record.
        line: The input line.

    """

    lang: str
    item_id: str
    ref_class: str
    gt: Triple
    decision: str
    top1: Triple | None
    record: Mapping[str, Any]
    line: BoqLine


def parse_tables(run: RunData, inputs: ScoreInputs) -> tuple[Any, Any, Any, Any]:
    """Parse the reference, the run's output, the dev ids and the classes with score.py.

    Raises:
        BuildError: The scorer rejected a file.

    """
    scorer = load_scorer()
    try:
        reference = scorer.parse_reference(scorer.read_table(inputs.reference), None, True)
        output = scorer.parse_output(scorer.read_table(run.run_dir / OUTPUT_FILE), None, True)
        side = scorer.load_side_ids(inputs.split, SIDE_DEV)
        classes = scorer.load_classes(inputs.classes)
    except scorer.ScoreError as error:
        raise BuildError(f"{run.lang}: {error}") from error
    return reference, output, side, classes


def records_by_row(run: RunData, output: Any) -> dict[int, dict[str, Any]]:
    """Map each output row (by identity) to its audit record; they share one order.

    Raises:
        BuildError: The output and the audit disagree in length or Item No.

    """
    if len(output.rows) != len(run.records):
        raise BuildError(f"{run.lang}: output.csv and audit.jsonl differ in length")
    for row, record in zip(output.rows, run.records, strict=True):
        if row.key != str(record["item_no"]):
            raise BuildError(f"{run.lang}: output row {row.key} vs audit {record['item_no']}")
    return {id(row): record for row, record in zip(output.rows, run.records, strict=True)}


def proposal_of(out: Any) -> Triple | None:
    """Return the matched labels of a matched row, else its suggestion; None if blank."""
    triple: Triple = out.labels if out.decision == MATCHED else (out.suggested or BLANK)
    return None if triple == BLANK else triple


def line_of(run: RunData, record: Mapping[str, Any]) -> BoqLine:
    """Return the input line of an audit record, checked by Item No.

    Raises:
        BuildError: The record's position names another line.

    """
    position = int(record["position"])
    line = run.lines[position] if position < len(run.lines) else None
    if line is None or line.item_no != record["item_no"]:
        raise BuildError(f"{run.lang}: audit position {position} is not {record['item_no']}")
    return line


def make_case(run: RunData, pair: Any, ref_class: str, record: Mapping[str, Any]) -> Case:
    """Build one line's case from its joined pair, class and audit record."""
    return Case(
        lang=run.lang,
        item_id=str(pair.key),
        ref_class=ref_class,
        gt=pair.ref.labels,
        decision=pair.out.decision,
        top1=proposal_of(pair.out),
        record=record,
        line=line_of(run, record),
    )


def cases_of(run: RunData, inputs: ScoreInputs) -> list[Case]:
    """Return every dev item line of a run, in reference order.

    Raises:
        BuildError: A dev item has no output row.

    """
    scorer = load_scorer()
    reference, output, side, classes = parse_tables(run, inputs)
    record_of = records_by_row(run, output)
    scope = scorer.build_scope(scorer.join_by_key(reference, output).pairs, side, classes)
    cases: list[Case] = []
    for pair, code in zip(scope.pairs, scope.classes, strict=True):
        if code in scorer.NON_ITEM_CLASSES:
            continue
        if pair.out is None:
            raise BuildError(f"{run.lang}: dev item {pair.key} is missing from the output")
        cases.append(make_case(run, pair, code, record_of[id(pair.out)]))
    return cases


# --- error rows ------------------------------------------------------------------------


def joined(triple: Triple | None) -> str:
    """Render a triple as ``type | usage | subtype``; blank for None."""
    return "" if triple is None else TRIPLE_SEPARATOR.join(triple)


def signal_cells(record: Mapping[str, Any]) -> Row:
    """Return the record's rule and the signals it carried (blank when not scored)."""
    signals = record.get("signals") or {}
    return {
        "rule": str(record["rule"]),
        "signal_b": str(signals.get("b", "")),
        "confidence_bucket": str(signals.get("confidence_bucket", "")),
    }


def error_row(case: Case, kind: str, candidate: bool, date: str) -> Row:
    """Return one ``errors_dev.csv`` row; the panel's columns are blank."""
    count = votes(case.record["raw_line_response"])
    row = dict.fromkeys(ERROR_FIELDS, "")
    row.update(
        item_id=case.item_id,
        lang=case.lang,
        split=SIDE_DEV,
        family=family_of(case.gt, case.top1),
        kind=kind,
        level_first_wrong=first_wrong_level(kind, case.gt, case.top1),
        top1=joined(case.top1),
        reason=str(case.record["reason"]),
        v="" if count is None else str(count),
        provenance=PROVENANCE,
        date=date,
        gt_suspect_candidate=CANDIDATE_YES if candidate else CANDIDATE_NO,
        **signal_cells(case.record),
    )
    return row


# --- the labeller packet ---------------------------------------------------------------


def jsonable(value: Any) -> Any:
    """Return an attribute value as JSON: enums by value, sets sorted, tuples as lists."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, frozenset):
        return sorted(jsonable(item) for item in value)
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    return value


def attributes_block(line: BoqLine) -> dict[str, Any]:
    """Return the attributes extracted from the line's short and long descriptions."""
    attributes = extract(TEXT_SEPARATOR.join((line.short, line.long)))
    return {
        field.name: jsonable(getattr(attributes, field.name))
        for field in dataclasses.fields(attributes)
    }


def code_block(code: str, library: Library) -> dict[str, Any]:
    """Return a code and its decoded triple (None when the code names no row)."""
    triple = triple_of(code, library)
    return {"code": code, "triple": None if triple is None else list(triple)}


def pass_block(number: int, parsed: ParsedPass, library: Library) -> dict[str, Any]:
    """Return one pass's answer decoded to triples, or its raw text when invalid."""
    answer = parsed.answer
    if answer is None:
        return {"pass": number, "valid": False, "error": parsed.error, "raw": parsed.raw}
    return {
        "pass": number,
        "valid": True,
        "kind": answer.kind,
        "top1": code_block(answer.top1, library),
        "top2": code_block(answer.top2, library),
        "confidence": answer.confidence,
        "self_reported_candidate_gap": answer.self_reported_candidate_gap,
        "element_or_application": answer.element_or_application,
        "material_family": answer.material_family,
        "nm_category": answer.nm_category,
        "evidence": answer.evidence,
    }


def predicted_types(case: Case, library: Library) -> set[str]:
    """Return the reference type, the proposal's type and every pass top1's type."""
    triples = [triple_of(answer.top1, library) for answer in valid_answers(raws_of(case))]
    found = {triple[0] for triple in [*triples, case.top1] if triple is not None}
    if case.gt != BLANK:
        found.add(case.gt[0])
    return found


def siblings_block(types: Iterable[str], library: Library) -> dict[str, list[list[str]]]:
    """Return every library row of each type as ``[code, usage, subtype]``."""
    return {
        name: [
            [row.code, row.material_usage, row.material_subtype]
            for row in library.rows
            if row.material_type == name
        ]
        for name in sorted(types)
    }


def raws_of(case: Case) -> list[str]:
    """Return the case's raw line responses."""
    return [str(raw) for raw in case.record["raw_line_response"]]


def line_block(line: BoqLine) -> dict[str, Any]:
    """Return the line's text, unit and section path as the model was shown them."""
    path = [
        PATH_PART_SEPARATOR.join(part for part in (header.item_no, header.text) if part)
        for header in line.section_path
    ]
    return {"short": line.short, "long": line.long, "unit": line.unit, "section_path": path}


def packet_row(case: Case, row: Row, library: Library) -> dict[str, Any]:
    """Return one error line's packet record: computed fields and context, no panel output."""
    top1_code = str(case.record.get("top1") or "")
    return {
        "record": "error_row",
        **{name: row[name] for name in ("item_id", "lang", "kind", "level_first_wrong")},
        **{name: row[name] for name in ("family", "reason", "rule", "v")},
        "decision": case.decision,
        "signals": case.record.get("signals"),
        "gt_suspect_candidate": row["gt_suspect_candidate"] == CANDIDATE_YES,
        "line": line_block(case.line),
        "gt": {"class": case.ref_class, "triple": None if case.gt == BLANK else list(case.gt)},
        "proposal": {"code": top1_code, "triple": None if case.top1 is None else list(case.top1)},
        "passes": [
            pass_block(number, parse_pass(raw), library)
            for number, raw in enumerate(raws_of(case), 1)
        ],
        "attributes": attributes_block(case.line),
        "siblings": siblings_block(predicted_types(case, library), library),
    }


def packet_header(runs: Sequence[RunData], date: str) -> dict[str, Any]:
    """Return the packet's first record: the runs, the definitions and the labelling rule."""
    return {
        "record": "header",
        "date": date,
        "provenance": PROVENANCE,
        "runs": {run.lang: run.manifest.get("run_id", run.run_dir.name) for run in runs},
        "cause_definitions": CAUSE_DEFINITIONS,
        "kind_definitions": KIND_DEFINITIONS,
        "labelling_rule": LABELLING_RULE,
        "missed_rule": MISSED_CAUSE_RULE,
    }


# --- build -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Built:
    """What ``build`` writes.

    Attributes:
        rows: The error rows, EN then FR, each in reference order.
        packet: The packet records, header first.
        cases: Every dev item line, by language.

    """

    rows: list[Row]
    packet: list[dict[str, Any]]
    cases: dict[str, list[Case]]


def errors_of(run: RunData, cases: Sequence[Case], library: Library, date: str) -> Built:
    """Return one language's error rows and packet records."""
    rows: list[Row] = []
    packet: list[dict[str, Any]] = []
    for case in cases:
        kind = error_kind(case.decision, case.ref_class, case.gt, case.top1)
        if kind is None:
            continue
        candidate = is_gt_suspect_candidate(raws_of(case), run.passes_k, library, case.gt)
        row = error_row(case, kind, candidate, date)
        rows.append(row)
        packet.append(packet_row(case, row, library))
    return Built(rows, packet, {run.lang: list(cases)})


def load_runs(args: argparse.Namespace) -> list[RunData]:
    """Read both runs and refuse any holding a non-dev item."""
    dev = dev_ids(args.split)
    runs = [
        load_run("en", args.run_en, args.input_en),
        load_run("fr", args.run_fr, args.input_fr),
    ]
    for run in runs:
        check_dev(run, dev)
    return runs


def build(args: argparse.Namespace) -> Built:
    """Refuse, read, join and classify both runs into error rows and packet records."""
    runs = load_runs(args)
    library = read_library(args.library, runs[0].manifest.get("library_id"))
    inputs = ScoreInputs(args.reference, args.split, args.classes)
    rows: list[Row] = []
    packet: list[dict[str, Any]] = [packet_header(runs, args.date)]
    cases: dict[str, list[Case]] = {}
    for run in runs:
        check_library(run, library)
        built = errors_of(run, cases_of(run, inputs), library, args.date)
        rows += built.rows
        packet += built.packet
        cases.update(built.cases)
    return Built(rows, packet, cases)


def show_lines(built: Built, items: Sequence[str]) -> list[str]:
    """Return, per requested Item No. and language, its decision, rule, reason and status."""
    kinds = {(row["lang"], row["item_id"]): row["kind"] for row in built.rows}
    lines: list[str] = []
    for item in items:
        for lang, cases in built.cases.items():
            case = next((found for found in cases if found.item_id == item), None)
            if case is None:
                lines.append(f"{item} {lang}: not a dev item line of the run")
                continue
            kind = kinds.get((lang, item))
            status = f"error row: {kind}" if kind else "not an error row"
            record = case.record
            lines.append(
                f"{item} {lang}: {case.decision} {record['rule']} {record['reason']} ({status})"
                f" signals {json.dumps(record.get('signals'), sort_keys=True)}"
            )
    return lines


# --- writing ---------------------------------------------------------------------------


def csv_bytes(fields: Sequence[str], rows: Iterable[Mapping[str, str]]) -> bytes:
    """Render rows as UTF-8 CSV with LF line ends."""
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator=NEWLINE)
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue().encode(ENCODING)


def jsonl_bytes(records: Iterable[Mapping[str, Any]]) -> bytes:
    """Render records as sorted-key JSON lines."""
    lines = (json.dumps(record, sort_keys=True, ensure_ascii=False) for record in records)
    return "".join(line + NEWLINE for line in lines).encode(ENCODING)


def write_bytes(path: Path, data: bytes) -> None:
    """Write a file, creating its folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def run_build(args: argparse.Namespace) -> int:
    """Build, write the error CSV and the packet, and print any ``--show`` lines."""
    built = build(args)
    write_bytes(args.output, csv_bytes(ERROR_FIELDS, built.rows))
    write_bytes(args.packet, jsonl_bytes(built.packet))
    for line in show_lines(built, args.show):
        print(line)
    counts = Counter(row["lang"] for row in built.rows)
    print(f"error rows: en {counts['en']}, fr {counts['fr']}; wrote {args.output}")
    return EXIT_OK


# --- agreement and triggers ------------------------------------------------------------


@dataclass(frozen=True)
class Agreement:
    """Agreement of two labellers on one category per row.

    Attributes:
        n: Rows compared.
        raw_agreement: Share of rows with equal labels; None when n = 0.
        expected: Chance agreement p_e; None when n = 0.
        kappa: Unweighted Cohen's κ; None when undefined (n = 0 or p_e = 1).

    """

    n: int
    raw_agreement: float | None
    expected: float | None
    kappa: float | None


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> Agreement:
    """Return raw agreement and unweighted Cohen's κ of (labeller A, labeller B) pairs."""
    total = len(pairs)
    if total == 0:
        return Agreement(0, None, None, None)
    observed = sum(first == second for first, second in pairs) / total
    first_counts = Counter(first for first, _ in pairs)
    second_counts = Counter(second for _, second in pairs)
    chance_mass = sum(count * second_counts[label] for label, count in first_counts.items())
    expected = chance_mass / (total * total)
    if chance_mass == total * total:
        return Agreement(total, observed, expected, None)
    return Agreement(total, observed, expected, (observed - expected) / (1 - expected))


def agreement_block(agreement: Agreement) -> dict[str, Any]:
    """Return an agreement as JSON, κ ``undefined`` when it is not defined."""
    return {
        "n": agreement.n,
        "raw_agreement": agreement.raw_agreement,
        "expected_agreement": agreement.expected,
        "kappa": "undefined" if agreement.kappa is None else agreement.kappa,
    }


def cause_trigger(count: int, total: int) -> dict[str, Any]:
    """Return a cause's count, share and whether it triggers (≥ 5 rows or ≥ 20%)."""
    by_share = count > 0 and count * PERCENT >= TRIGGER_SHARE_PERCENT * total
    return {
        "count": count,
        "share": count / total if total else 0.0,
        "triggered": count >= TRIGGER_MIN_ROWS or by_share,
    }


def weighted_pareto(rows: Sequence[Mapping[str, str]]) -> dict[str, int]:
    """Return the display-only Pareto: precision and safety kinds weigh 2."""
    weights: Counter[str] = Counter()
    for row in rows:
        weights[row["cause"]] += PARETO_WEIGHT if row["kind"] in WEIGHTED_KINDS else 1
    return {cause: weights[cause] for cause in CAUSES}


def language_triggers(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """Return one language's primary-cause counts and triggers over all its error rows."""
    total = len(rows)
    counts = Counter(row["cause"] for row in rows)
    causes = {cause: cause_trigger(counts[cause], total) for cause in CAUSES}
    return {
        "error_rows": total,
        "causes": causes,
        "triggered": [cause for cause in CAUSES if causes[cause]["triggered"]],
        "pareto_weighted_display_only": weighted_pareto(rows),
    }


def language_order(lang: str) -> tuple[int, str]:
    """Sort EN, FR first, then any other language by name."""
    return (LANGUAGES.index(lang) if lang in LANGUAGES else len(LANGUAGES), lang)


def trigger_counts(rows: Sequence[Mapping[str, str]]) -> dict[str, dict[str, Any]]:
    """Return per-language trigger counts of the primary cause (§10.7, A60.7, A60.15).

    Args:
        rows: Error rows with ``lang``, ``cause`` and ``kind``; missed rows included.

    Returns:
        Language -> error rows, per-cause count, share and trigger, the triggered causes and
        the display-only weighted Pareto.

    """
    langs = sorted({row["lang"] for row in rows}, key=language_order)
    return {lang: language_triggers([row for row in rows if row["lang"] == lang]) for lang in langs}


# --- merge -----------------------------------------------------------------------------


def read_rows(path: Path, fields: Sequence[str]) -> list[Row]:
    """Read a CSV as rows of strings; refuse a file missing a needed column.

    Raises:
        MergeError: A column is missing.

    """
    with path.open(encoding=ENCODING, newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(set(fields) - set(reader.fieldnames or ()))
        if missing:
            raise MergeError(f"{path.name}: missing columns {missing}")
        return [{key: value or "" for key, value in row.items()} for row in reader]


def keyed(rows: Sequence[Row], name: str) -> dict[Key, Row]:
    """Index rows by (Item No., language); refuse duplicates.

    Raises:
        MergeError: A key appears twice.

    """
    found: dict[Key, Row] = {}
    for row in rows:
        key = (row["item_id"], row["lang"])
        if key in found:
            raise MergeError(f"{name}: {key} appears twice")
        found[key] = row
    return found


def has_bad_value(row: Row, cause_required: bool) -> bool:
    """Whether a panel row's cause, cause_2 or usage split is outside its list."""
    bad_cause = bool(row["cause"] or cause_required) and row["cause"] not in CAUSES
    bad_second = bool(row["cause_2"]) and row["cause_2"] not in CAUSES
    return bad_cause or bad_second or row["usage_split"] not in USAGE_SPLITS


def check_values(rows: Mapping[Key, Row], name: str, cause_required: bool) -> None:
    """Refuse an out-of-list cause, cause_2 or usage split, or a missing primary cause.

    Raises:
        MergeError: Naming the rows to relabel.

    """
    bad = [
        f"{key}: {row['cause']!r}/{row['cause_2']!r}/{row['usage_split']!r}"
        for key, row in sorted(rows.items())
        if has_bad_value(row, cause_required)
    ]
    if bad:
        raise MergeError(f"{name}: out-of-list values, return for relabelling: {bad}")


def check_coverage(errors: Mapping[Key, Row], panel: Mapping[str, Mapping[Key, Row]]) -> None:
    """Refuse labels that miss or add error rows, or an adjudication of a non-error row.

    Raises:
        MergeError: Naming the rows.

    """
    for name, rows in panel.items():
        extra = sorted(set(rows) - set(errors))
        missing = sorted(set(errors) - set(rows)) if name != "adjudication" else []
        if extra or missing:
            raise MergeError(f"{name}: rows missing {missing}, rows not in the errors {extra}")


def resolve_cause(key: Key, first: Row, second: Row, ruling: Row | None) -> str:
    """Return the adjudicated cause, else the labellers' common cause.

    Raises:
        MergeError: The labellers disagree and nothing adjudicates it.

    """
    if ruling is not None and ruling["cause"]:
        return ruling["cause"]
    if first["cause"] == second["cause"]:
        return first["cause"]
    raise MergeError(f"{key}: {first['cause']} vs {second['cause']} has no adjudication")


def resolve_optional(field: str, first: Row, second: Row, ruling: Row | None) -> str:
    """Return the adjudicated value, else the labellers' common value, else blank."""
    if ruling is not None and ruling[field]:
        return ruling[field]
    return first[field] if first[field] == second[field] else ""


def merged_row(error: Row, first: Row, second: Row, ruling: Row | None) -> Row:
    """Return an error row with the panel's labels folded in.

    Raises:
        MergeError: An unadjudicated disagreement, or a usage split on a non-usage row.

    """
    key = (error["item_id"], error["lang"])
    usage_split = resolve_optional("usage_split", first, second, ruling)
    if usage_split and error["level_first_wrong"] != LEVEL_USAGE:
        raise MergeError(f"{key}: a usage split on a {error['level_first_wrong']} row")
    return {
        **error,
        "cause": resolve_cause(key, first, second, ruling),
        "cause_2": resolve_optional("cause_2", first, second, ruling),
        "usage_split": usage_split,
        "evidence": (ruling or {}).get("evidence") or first["evidence"],
        "labeller_a": first["cause"],
        "labeller_b": second["cause"],
        "adjudication_reason": "" if ruling is None else ruling["adjudication_reason"],
    }


def gt_rulings(adjudication: Mapping[Key, Row]) -> dict[str, tuple[str, str]]:
    """Return Item No. -> (gt_suspect, reason): one ruling per Item No., with a reason.

    Raises:
        MergeError: A ruling that is not true/false, has no reason, or conflicts.

    """
    rulings: dict[str, tuple[str, str]] = {}
    for (item, _), row in sorted(adjudication.items()):
        if not row["gt_suspect"]:
            continue
        ruling = (row["gt_suspect"], row["gt_suspect_reason"])
        if ruling[0] not in RULINGS or not ruling[1]:
            raise MergeError(f"{item}: gt_suspect needs true/false and a written reason")
        if rulings.setdefault(item, ruling) != ruling:
            raise MergeError(f"{item}: conflicting gt_suspect rulings")
    return rulings


def apply_rulings(rows: Sequence[Row], rulings: Mapping[str, tuple[str, str]]) -> list[Row]:
    """Put each Item No.'s ruling on both language rows; refuse an unruled candidate.

    Raises:
        MergeError: A gt_suspect candidate has no ruling.

    """
    candidates = {row["item_id"] for row in rows if row["gt_suspect_candidate"] == CANDIDATE_YES}
    unruled = sorted(candidates - set(rulings))
    if unruled:
        raise MergeError(f"gt_suspect candidates without a ruling: {unruled}")
    unset = ("", "")
    applied: list[Row] = []
    for row in rows:
        suspect, reason = rulings.get(row["item_id"], unset)
        applied.append({**row, "gt_suspect": suspect, "gt_suspect_reason": reason})
    return applied


def merge(errors: Sequence[Row], panel: Mapping[str, Sequence[Row]]) -> list[Row]:
    """Fold labeller A, labeller B and the adjudication into the error rows.

    Args:
        errors: The rows of ``errors_dev.csv``.
        panel: ``labels_a``, ``labels_b`` and ``adjudication`` rows.

    Returns:
        The merged rows, in the error file's order.

    """
    indexed = {name: keyed(rows, name) for name, rows in panel.items()}
    by_key = keyed(errors, "errors")
    check_coverage(by_key, indexed)
    check_values(indexed["labels_a"], "labels_a", cause_required=True)
    check_values(indexed["labels_b"], "labels_b", cause_required=True)
    check_values(indexed["adjudication"], "adjudication", cause_required=False)
    rows = [
        merged_row(
            row,
            indexed["labels_a"][key],
            indexed["labels_b"][key],
            indexed["adjudication"].get(key),
        )
        for key, row in by_key.items()
    ]
    return apply_rulings(rows, gt_rulings(indexed["adjudication"]))


def agreement_summary(rows: Sequence[Row]) -> dict[str, Any]:
    """Return labeller agreement on the primary cause, pooled and per language."""
    pairs = {
        lang: [(row["labeller_a"], row["labeller_b"]) for row in rows if row["lang"] == lang]
        for lang in sorted({row["lang"] for row in rows}, key=language_order)
    }
    pooled = [pair for found in pairs.values() for pair in found]
    blocks = {lang: agreement_block(cohen_kappa(found)) for lang, found in pairs.items()}
    return {"pooled": agreement_block(cohen_kappa(pooled)), **blocks}


def summary_of(rows: Sequence[Row]) -> dict[str, Any]:
    """Return the panel summary: agreement, triggers and the gt_suspect rulings."""
    candidates = {row["item_id"] for row in rows if row["gt_suspect_candidate"] == CANDIDATE_YES}
    suspect = {row["item_id"] for row in rows if row["gt_suspect"] == TRUE}
    return {
        "agreement": agreement_summary(rows),
        "triggers": trigger_counts(rows),
        "gt_suspect": {"candidates": sorted(candidates), "ruled_suspect": sorted(suspect)},
        "notes": {
            "kappa": "unweighted Cohen's kappa on the primary cause; undefined when p_e = 1",
            "triggers": "primary cause, >= 5 rows or >= 20% of the language's error rows; "
            "missed rows included (A60.15)",
        },
    }


def run_merge(args: argparse.Namespace) -> int:
    """Merge the panel files, write the merged CSV and the summary, and print the summary."""
    errors = read_rows(args.errors, ERROR_FIELDS)
    panel = {
        "labels_a": read_rows(args.labels_a, LABEL_FIELDS),
        "labels_b": read_rows(args.labels_b, LABEL_FIELDS),
        "adjudication": read_rows(args.adjudication, ADJUDICATION_FIELDS),
    }
    rows = merge(errors, panel)
    summary = summary_of(rows)
    text = json.dumps(summary, sort_keys=True, indent=JSON_INDENT, ensure_ascii=False)
    write_bytes(args.output or args.errors, csv_bytes(ERROR_FIELDS, rows))
    write_bytes(args.summary, (text + NEWLINE).encode(ENCODING))
    print(text)
    return EXIT_OK


# --- command line ----------------------------------------------------------------------


def add_build_parser(commands: Any) -> None:
    """Add the ``build`` subcommand."""
    parser = commands.add_parser("build", help="write errors_dev.csv and the packet")
    parser.add_argument("--run-en", type=Path, required=True)
    parser.add_argument("--run-fr", type=Path, required=True)
    parser.add_argument("--input-en", type=Path, default=DEFAULT_INPUTS["en"])
    parser.add_argument("--input-fr", type=Path, default=DEFAULT_INPUTS["fr"])
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--classes", type=Path, default=DEFAULT_CLASSES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--show", action="append", default=[], metavar="ITEM_NO")
    parser.set_defaults(handler=run_build)


def add_merge_parser(commands: Any) -> None:
    """Add the ``merge`` subcommand."""
    parser = commands.add_parser("merge", help="fold the panel's labels into errors_dev.csv")
    parser.add_argument("--errors", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--labels-a", type=Path, default=DEFAULT_LABELS_A)
    parser.add_argument("--labels-b", type=Path, default=DEFAULT_LABELS_B)
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    parser.add_argument("--output", type=Path, help="default: rewrite --errors in place")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.set_defaults(handler=run_merge)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    add_build_parser(commands)
    add_merge_parser(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a subcommand; return the exit code.

    Args:
        argv: Arguments; ``sys.argv[1:]`` when None.

    Returns:
        0 ok, 2 bad input, 4 refused.

    """
    args = build_parser().parse_args(argv)
    try:
        code: int = args.handler(args)
    except RefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (BuildError, MergeError, OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    return code


def use_utf8_streams() -> None:
    """Print UTF-8 whatever the console code page is."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding=ENCODING)


if __name__ == "__main__":
    use_utf8_streams()
    sys.exit(main())
