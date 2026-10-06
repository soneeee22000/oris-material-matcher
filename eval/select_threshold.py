r"""``oris select``: the §10.6 threshold, chosen from replayed B3 dev votes at $0 (A59.3, A60.3).

For each B3 dev run (EN and FR) and each candidate threshold ``T1`` (strictest) to ``T8`` of
the run's ``passes_k``, the run is replayed under a temporary override ``policy.yaml`` that
forces that threshold for the manifest's (requested model, library SHA-256); the policy is not
part of the cache key (§11.5), so every replay sends the same requests and only D9/D10 lines
move. ``config/policy.yaml`` never decides anything here. A replay is aborted unless it is mode
``replay``, resolves ``override`` at the forced id and has no replay miss. Each re-decided
output is scored in process with ``eval/score.py --side dev --strict`` and the class list, and
the §10.6 rule (``eval/selection_rule.py``) picks one threshold for both languages.

Refused (exit 4) before any replay: a run that is not B3, a run where the fallback engaged, EN
and FR runs that differ in model, library, prompt version or ``passes_k`` (one threshold for
both languages needs the same T-definitions), and a run that routed an item outside the
split's dev side.

Writes ``eval/selection_v1.json`` (sorted keys, LF, portable paths) and the trade-off table
``eval/selection_v1.md``. Reference rows, never candidates: match-all (every valid in-library
plurality top-1 matched) and B2 (the ``R-10.9-evidence`` ledger rows, when present).

Usage::

    python eval/select_threshold.py --run-en runs/<en run> --run-fr runs/<fr run> \
        [--target 0.85]

Exit codes: 0 ok, 2 bad input or an aborted replay, 4 refused.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from oris_matcher.cli import prepare_run, replay_job, run_match, settings_from_manifest
from oris_matcher.doctor import Runtime, WiringError, read_manifest
from oris_matcher.domain.decision import (
    Decision,
    LLMFailureKind,
    Rule,
    Threshold,
    candidate_thresholds,
    llm_failure_reason,
)
from oris_matcher.domain.validator import is_valid_code
from oris_matcher.io.audit import AUDIT_FILE, portable_path
from oris_matcher.io.writer import render_csv
from oris_matcher.service import BUCKET_LABELS, PolicyResolution, RunProfile, RunResult
from oris_matcher.settings import ConfigError

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_REFERENCE = ROOT / "data" / "boq_dataset_matched_GT.csv"
DEFAULT_SPLIT = EVAL_DIR / "split_v1.json"
DEFAULT_CLASSES = EVAL_DIR / "annotations" / "blank_line_classes.csv"
DEFAULT_LEDGER = EVAL_DIR / "experiments.jsonl"
DEFAULT_JSON = EVAL_DIR / "selection_v1.json"
DEFAULT_MARKDOWN = EVAL_DIR / "selection_v1.md"
DEFAULT_B2_ID = "R-10.9-evidence"
RUNS_DIR = "runs"
OUTPUT_FILE = "output.csv"
SCORER_PATH = EVAL_DIR / "score.py"
SCORER_MODULE = "oris_eval_score_for_selection"
RULE_PATH = EVAL_DIR / "selection_rule.py"
RULE_MODULE = "oris_eval_selection_rule"
ENCODING = "utf-8"
READ_ENCODING = "utf-8-sig"
NEWLINE = "\n"
JSON_INDENT = 2
LANGUAGES = ("en", "fr")
SIDE_DEV = "dev"
DEV_FIELD = "item_ids_dev"
REPLAY_MODE = "replay"
CERTIFIED_BY = "dev_selection"
REPLAY_MISS = llm_failure_reason(LLMFailureKind.REPLAY_MISS)
STRUCTURAL_RULES = frozenset({Rule.D0.value, Rule.D0A.value, Rule.D0B.value})
SHARED_FIELDS = ("requested_model", "library_sha256", "prompt_version", "passes_k")
DECISION_COLUMN = "decision"
LABEL_COLUMNS = ("material_type", "material_usage", "material_subtype")
H265_CLASSES = ("L", "E")
LINES_PER_UNIT = 100
DIGITS = 3
REVIEW_DIGITS = 1
REFUSAL_SAMPLE = 3
TARGET_MIN = 0.0
TARGET_MAX = 1.0
COST_DIGITS = 6
NOT_AVAILABLE = "n/a"
COST_NOTE = "identical across thresholds (same calls)"
REVIEW_NOTE = "needs_review per 100 output lines, N basis (evaluation-protocol.md:87)"
H265_NOTE = "diagnostic, never used for selection"
MATCH_ALL_NOTE = "every valid in-library plurality top1 matched; a reference row, never a candidate"
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_REFUSED = 4


class SelectionError(Exception):
    """A replay or a score could not be produced as the rule requires; nothing is written."""


class RefusedError(Exception):
    """The runs are not B3 dev votes of one configuration; nothing is replayed."""


def load_script(name: str, path: Path) -> ModuleType:
    """Load a standalone ``eval/`` script (not a package module) once, by file path."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SelectionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if TYPE_CHECKING:
    import selection_rule as rule
else:
    rule = load_script(RULE_MODULE, RULE_PATH)


def load_scorer() -> ModuleType:
    """Load ``eval/score.py`` once."""
    return load_script(SCORER_MODULE, SCORER_PATH)


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- refusals ---------------------------------------------------------------------------


def dev_ids(split: Path) -> frozenset[str]:
    """Return the split's dev item ids."""
    payload = json.loads(split.read_text(encoding=ENCODING))
    return frozenset(payload[DEV_FIELD])


def routed_items(run_dir: Path) -> list[str]:
    """Return the Item No. of every audit record a structural rule did not decide."""
    text = (run_dir / AUDIT_FILE).read_text(encoding=ENCODING)
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [str(item["item_no"]) for item in records if item["rule"] not in STRUCTURAL_RULES]


def check_run(lang: str, run_dir: Path, manifest: Mapping[str, Any], dev: frozenset[str]) -> None:
    """Refuse a run that is not B3, engaged the fallback, or routed a non-dev item."""
    if manifest.get("profile") != RunProfile.B3.value:
        raise RefusedError(f"{lang} run {run_dir.name} is profile {manifest.get('profile')}")
    if manifest.get("fallback_engaged"):
        raise RefusedError(f"{lang} run {run_dir.name} engaged the fallback model")
    outside = sorted({item for item in routed_items(run_dir) if item not in dev})
    if outside:
        sample = outside[:REFUSAL_SAMPLE]
        raise RefusedError(f"{lang} run {run_dir.name} routed non-dev items: {sample}")


def check_runs(
    run_dirs: Mapping[str, Path], manifests: Mapping[str, Mapping[str, Any]], split: Path
) -> None:
    """Refuse runs that are not B3 dev votes of one model, library, prompt version and k.

    Args:
        run_dirs: Language -> run folder.
        manifests: Language -> its manifest.
        split: The split file.

    Raises:
        RefusedError: Any refusal of the module docstring.

    """
    dev = dev_ids(split)
    for lang, run_dir in run_dirs.items():
        check_run(lang, run_dir, manifests[lang], dev)
    for field in SHARED_FIELDS:
        values = {json.dumps(manifest.get(field)) for manifest in manifests.values()}
        if len(values) > 1:
            raise RefusedError(f"the EN and FR runs differ in {field}: {sorted(values)}")


# --- re-deciding by replay --------------------------------------------------------------


@dataclass(frozen=True)
class Redecided:
    """One run replayed at one forced threshold.

    Attributes:
        threshold_id: The forced threshold.
        result: The replayed run.
        csv: Its rendered output.

    """

    threshold_id: str
    result: RunResult
    csv: bytes


def write_policy(path: Path, manifest: Mapping[str, Any], threshold_id: str) -> Path:
    """Write an override ``policy.yaml`` (JSON is YAML) forcing one threshold for the run."""
    entry = {"policy_id": threshold_id, "certified_by": CERTIFIED_BY}
    policy = {"policies": {manifest["requested_model"]: {manifest["library_sha256"]: entry}}}
    path.write_text(json.dumps(policy, sort_keys=True), encoding=ENCODING)
    return path


def check_redecided(result: RunResult, threshold_id: str) -> None:
    """Abort unless the replay is mode replay, an override at the id, without replay misses."""
    misses = sum(item.decision.reason == REPLAY_MISS for item in result.lines)
    problems = [
        f"mode {result.manifest.get('mode')}" if result.manifest.get("mode") != REPLAY_MODE else "",
        f"policy_resolution {result.policy.resolution.value}"
        if result.policy.resolution != PolicyResolution.OVERRIDE
        else "",
        f"policy_id {result.policy.policy_id}" if result.policy.policy_id != threshold_id else "",
        f"{misses} replay miss(es)" if misses else "",
    ]
    found = [problem for problem in problems if problem]
    if found:
        raise SelectionError(f"replay at {threshold_id} of {result.run_id}: {'; '.join(found)}")


def redecide(run_dir: Path, threshold_id: str, runtime: Runtime, scratch: Path) -> Redecided:
    """Replay a run with one threshold forced by a temporary override policy.

    Args:
        run_dir: The recorded B3 run.
        threshold_id: ``T1`` to ``T8``.
        runtime: The environment; its root resolves the manifest's paths.
        scratch: Where the temporary policy file goes.

    Returns:
        The replayed run and its rendered CSV.

    Raises:
        SelectionError: The replay is not an exact override replay.

    """
    manifest = read_manifest(run_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    policy = write_policy(scratch / f"policy_{threshold_id}.yaml", manifest, threshold_id)
    settings = settings_from_manifest(manifest)
    job = replay_job(run_dir, settings, policy, runtime.root())
    result, _ = run_match(job, prepare_run(job, settings, runtime), runtime)
    check_redecided(result, threshold_id)
    return Redecided(threshold_id, result, render_csv(result))


def match_all_csv(redecided: Redecided) -> bytes:
    """Rewrite a re-decided output so every line with a valid library top1 is matched to it."""
    rows = list(csv.reader(io.StringIO(redecided.csv.decode(READ_ENCODING), newline="")))
    header, body = rows[0], rows[1:]
    decision_at = header.index(DECISION_COLUMN)
    label_at = [header.index(name) for name in LABEL_COLUMNS]
    library = redecided.result.library
    for row, record in zip(body, redecided.result.audit, strict=True):
        code = str(record["top1"])
        if not is_valid_code(code, library):
            continue
        matched = library.by_code[code]
        row[decision_at] = Decision.MATCHED.value
        triple = (matched.material_type, matched.material_usage, matched.material_subtype)
        for index, value in zip(label_at, triple, strict=True):
            row[index] = value
    handle = io.StringIO()
    csv.writer(handle, lineterminator=NEWLINE).writerows([header, *body])
    return handle.getvalue().encode(ENCODING)


# --- scoring ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreInputs:
    """What every output is scored against: exactly the runner's ``score_argv`` flags.

    Attributes:
        reference: The ground-truth CSV.
        split: The split file.
        classes: The blank-line class list.

    """

    reference: Path
    split: Path
    classes: Path

    def argv(self, output: Path, lang: str) -> list[str]:
        """Return the scorer's arguments for one output on the dev side."""
        return [
            *("--output", str(output), "--label", lang),
            *("--reference", str(self.reference), "--split", str(self.split)),
            *("--side", SIDE_DEV, "--strict", "--classes", str(self.classes)),
        ]


def score_output(output: Path, lang: str, inputs: ScoreInputs) -> dict[str, Any]:
    """Score one output in process with ``eval/score.py``; return its report."""
    scorer = load_scorer()
    try:
        report = scorer.run(scorer.parse_settings(inputs.argv(output, lang)))
    except scorer.ScoreError as error:
        raise SelectionError(f"the scorer rejected {output.name}: {error}") from error
    scored: dict[str, Any] = report["outputs"][0]
    return scored


def item_correct(output: Path, inputs: ScoreInputs) -> dict[str, int]:
    """Return Item No. -> 1 (matched with the reference triple) or 0, for every dev item."""
    scorer = load_scorer()
    try:
        reference = scorer.parse_reference(scorer.read_table(inputs.reference), None, True)
        parsed = scorer.parse_output(scorer.read_table(output), None, True)
        side = scorer.load_side_ids(inputs.split, SIDE_DEV)
    except scorer.ScoreError as error:
        raise SelectionError(f"cannot score {output.name}: {error}") from error
    scope = scorer.build_scope(scorer.join_by_key(reference, parsed).pairs, side, {})
    return {str(pair.key): int(scorer.count_correct([pair])) for pair in scope.pairs}


def decisions_of(output: Path) -> list[str]:
    """Return the decision of every output row (all rows: the N basis of review load)."""
    with output.open(encoding=READ_ENCODING, newline="") as handle:
        return [row[DECISION_COLUMN] for row in csv.DictReader(handle)]


def h265(report: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return H₂₆₅ (diagnostic): correct matches plus reviewed L or E lines, over L plus E."""
    material = report.get("coverage_material")
    if not material:
        return None
    confusion = report["confusion"]
    reviewed = sum(
        confusion.get(code, {}).get(Decision.NEEDS_REVIEW.value, 0) for code in H265_CLASSES
    )
    numerator = int(report["coverage_labelled"]["correct"]) + reviewed
    denominator = int(material["denominator"])
    value = numerator / denominator if denominator else None
    return {"numerator": numerator, "denominator": denominator, "value": value}


def review_load(decisions: Sequence[str]) -> dict[str, Any]:
    """Return needs_review per 100 output lines (N basis) and the counts behind it."""
    count = sum(decision == Decision.NEEDS_REVIEW.value for decision in decisions)
    per_100 = count * LINES_PER_UNIT / len(decisions) if decisions else None
    return {"needs_review": count, "lines": len(decisions), "review_load_per_100": per_100}


def language_figures(output: Path, lang: str, inputs: ScoreInputs) -> dict[str, Any]:
    """Return one output's trade-off figures for one language."""
    report = score_output(output, lang, inputs)
    precision = report["precision"]
    matched, correct = int(precision["matched"]), int(precision["correct"])
    item_counts = report["decision_shares"]["item_rows"]["counts"]
    return {
        "matched": matched,
        "correct": correct,
        "precision": precision["value"],
        "cp_lower_95": rule.cp_lower(correct, matched),
        "coverage_labelled": report["coverage_labelled"],
        "h265": h265(report),
        **review_load(decisions_of(output)),
        "not_a_material": item_counts.get(Decision.NOT_A_MATERIAL.value, 0),
        "false_not_a_material": report["false_not_a_material"],
        "mean_cost_usd": report["mean_cost_usd"],
        "item_correct": item_correct(output, inputs),
    }


def figures_of_bytes(data: bytes, path: Path, lang: str, inputs: ScoreInputs) -> dict[str, Any]:
    """Write an output to a scratch file and return its figures."""
    path.write_bytes(data)
    return language_figures(path, lang, inputs)


# --- one language: every threshold ------------------------------------------------------


@dataclass(frozen=True)
class LanguageResults:
    """One language's figures at every candidate threshold, plus match-all.

    Attributes:
        thresholds: The candidate thresholds, strictest first.
        figures: Threshold id -> figures.
        match_all: Figures of the match-all reference row.

    """

    thresholds: tuple[Threshold, ...]
    figures: dict[str, dict[str, Any]]
    match_all: dict[str, Any]


def evaluate_run(
    lang: str, run_dir: Path, inputs: ScoreInputs, runtime: Runtime, scratch: Path
) -> LanguageResults:
    """Replay and score one language's run at every candidate threshold.

    Args:
        lang: ``en`` or ``fr``.
        run_dir: The recorded B3 run.
        inputs: Reference, split and classes.
        runtime: The environment.
        scratch: A temporary folder.

    Returns:
        The figures per threshold and of match-all.

    """
    thresholds = candidate_thresholds(int(read_manifest(run_dir)["passes_k"]))
    figures: dict[str, dict[str, Any]] = {}
    redecided: Redecided | None = None
    for threshold in thresholds:
        redecided = redecide(run_dir, threshold.threshold_id, runtime, scratch)
        target = scratch / f"{lang}_{threshold.threshold_id}.csv"
        figures[threshold.threshold_id] = figures_of_bytes(redecided.csv, target, lang, inputs)
    if redecided is None:
        raise SelectionError(f"{run_dir.name}: no candidate thresholds")
    match_all = match_all_csv(redecided)
    all_figures = figures_of_bytes(match_all, scratch / f"{lang}_match_all.csv", lang, inputs)
    return LanguageResults(thresholds, figures, all_figures)


# --- reference rows from the ledger -----------------------------------------------------


def ledger_rows(path: Path) -> list[dict[str, Any]]:
    """Read the ledger's rows, oldest first; an absent ledger has none."""
    if not path.is_file():
        return []
    lines = path.read_text(encoding=ENCODING).splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def ledger_id_of(rows: Sequence[Mapping[str, Any]], run_id: str) -> str | None:
    """Return the ledger id of the latest row recording ``run_id``, else None."""
    ids = [str(row["id"]) for row in rows if row.get("run_id") == run_id]
    return ids[-1] if ids else None


def row_output(row: Mapping[str, Any], root: Path) -> Path:
    """Return a ledger row's output CSV: its recorded path, else its run folder's copy."""
    candidates = [root / RUNS_DIR / str(row["run_id"]) / OUTPUT_FILE]
    if isinstance(row.get("output"), str):
        candidates.insert(0, root / str(row["output"]))
    found = next((path for path in candidates if path.is_file()), None)
    if found is None:
        raise SelectionError(f"ledger row {row['id']} ({row['run_id']}): no output CSV")
    return found


def b2_reference(
    rows: Sequence[Mapping[str, Any]], b2_id: str, root: Path, inputs: ScoreInputs
) -> dict[str, Any] | None:
    """Score the latest B2 ledger rows of ``b2_id`` in both languages; None if one is absent."""
    latest = {
        lang: [row for row in rows if row.get("id") == b2_id and row.get("lang") == lang]
        for lang in LANGUAGES
    }
    if not all(latest.values()):
        return None
    chosen = {lang: found[-1] for lang, found in latest.items()}
    return {
        "ledger_id": b2_id,
        "run_ids": {lang: str(row["run_id"]) for lang, row in chosen.items()},
        "per_language": {
            lang: language_figures(row_output(row, root), lang, inputs)
            for lang, row in chosen.items()
        },
    }


# --- the selection payload --------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """Everything the payload records besides the figures.

    Attributes:
        args: The parsed arguments.
        root: The repository root, for portable paths.
        run_dirs: Language -> run folder.
        manifests: Language -> manifest.
        ledger: The ledger's rows.

    """

    args: argparse.Namespace
    root: Path
    run_dirs: dict[str, Path]
    manifests: dict[str, dict[str, Any]]
    ledger: list[dict[str, Any]]


def inputs_block(context: Context) -> dict[str, Any]:
    """Return the payload's ``inputs``: runs, ledger ids, model and every input hash."""
    args, manifests, root = context.args, context.manifests, context.root
    run_ids = {lang: str(manifest["run_id"]) for lang, manifest in manifests.items()}
    return {
        "run_ids": run_ids,
        "ledger_ids": {lang: ledger_id_of(context.ledger, run) for lang, run in run_ids.items()},
        "model": manifests[LANGUAGES[0]]["requested_model"],
        "input_sha256": {lang: manifest["input_sha256"] for lang, manifest in manifests.items()},
        "library_sha256": manifests[LANGUAGES[0]]["library_sha256"],
        "split_sha256": file_sha256(args.split),
        "reference_sha256": file_sha256(args.reference),
        "prompt_versions": {
            lang: manifest["prompt_version"] for lang, manifest in manifests.items()
        },
        "paths": {
            "runs": {lang: portable_path(path, root) for lang, path in context.run_dirs.items()},
            "reference": portable_path(args.reference, root),
            "split": portable_path(args.split, root),
            "classes": portable_path(args.classes, root),
        },
    }


def minimum_block(threshold: Threshold) -> dict[str, Any]:
    """Return a threshold's minimum score s: votes, signal (b) and confidence bucket."""
    minimum = threshold.minimum
    return {
        "votes": minimum.votes,
        "attributes": minimum.attributes.value,
        "confidence_bucket": BUCKET_LABELS[minimum.confidence],
    }


def rule_table(results: Mapping[str, LanguageResults]) -> list[rule.Row[str]]:
    """Return the strictest-first (threshold id, per-language stats) table of the rule."""
    thresholds = results[LANGUAGES[0]].thresholds
    return [
        (
            threshold.threshold_id,
            {
                lang: rule.language_stats(
                    result.figures[threshold.threshold_id]["matched"],
                    result.figures[threshold.threshold_id]["correct"],
                )
                for lang, result in results.items()
            },
        )
        for threshold in thresholds
    ]


def thresholds_block(results: Mapping[str, LanguageResults]) -> list[dict[str, Any]]:
    """Return one entry per candidate threshold with its minimum, objective and figures."""
    table = dict(rule_table(results))
    return [
        {
            "id": threshold.threshold_id,
            "minimum": minimum_block(threshold),
            "summed_correct": rule.summed_correct(table[threshold.threshold_id]),
            "qualifies": rule.qualifies(table[threshold.threshold_id]),
            "per_language": {
                lang: result.figures[threshold.threshold_id] for lang, result in results.items()
            },
        }
        for threshold in results[LANGUAGES[0]].thresholds
    ]


def choice(selection: rule.Selection[str]) -> dict[str, Any]:
    """Return a sensitivity row's threshold and status."""
    return {"threshold_id": selection.threshold, "status": selection.status}


def selection_blocks(outcome: rule.RuleOutcome[str]) -> dict[str, Any]:
    """Return the payload's ``selected`` and ``sensitivity`` blocks."""
    selected = outcome.selected
    target = outcome.target
    return {
        "selected": {
            "threshold_id": selected.threshold,
            "status": selected.status,
            "loosest_qualifying": selected.loosest_qualifying,
            "dev_bar_met": selected.status == rule.STATUS_MEETS_BAR,
        },
        "sensitivity": {
            "per_language_optimum": {
                lang: choice(item) for lang, item in outcome.per_language.items()
            },
            "target": None if target is None else {"value": outcome.target_value, **choice(target)},
        },
    }


def rule_block(args: argparse.Namespace) -> dict[str, Any]:
    """Return the payload's ``rule``: the §10.6 constants and the sensitivity target."""
    return {
        "precision_bar": rule.DEV_BAR_PRECISION,
        "min_matched": rule.DEV_BAR_MIN_MATCHED,
        "tie_window": rule.TIE_WINDOW,
        "target": args.target,
    }


def build_payload(
    context: Context, results: Mapping[str, LanguageResults], b2: dict[str, Any] | None
) -> dict[str, Any]:
    """Assemble ``selection_v1.json``.

    Args:
        context: Arguments, root, runs, manifests and ledger.
        results: Language -> figures at every threshold.
        b2: The B2 reference row, or None.

    Returns:
        The payload.

    """
    outcome = rule.run_rule(rule_table(results), context.args.target)
    return {
        "inputs": inputs_block(context),
        "rule": rule_block(context.args),
        "thresholds": thresholds_block(results),
        "reference_rows": {
            "match_all": {
                "note": MATCH_ALL_NOTE,
                "per_language": {lang: result.match_all for lang, result in results.items()},
            },
            "b2": b2,
        },
        **selection_blocks(outcome),
        "notes": {
            "mean_cost_usd": COST_NOTE,
            "review_load_per_100": REVIEW_NOTE,
            "h265": H265_NOTE,
        },
    }


# --- the trade-off table ----------------------------------------------------------------


def fmt(value: float | None, digits: int = DIGITS) -> str:
    """Format a number, or ``n/a``."""
    return NOT_AVAILABLE if value is None else f"{value:.{digits}f}"


def table_row(name: str, figures: Mapping[str, Any]) -> str:
    """Render one language's trade-off row."""
    h265_block = figures["h265"]
    cells = [
        name,
        str(figures["matched"]),
        str(figures["correct"]),
        fmt(figures["precision"]),
        fmt(figures["cp_lower_95"]),
        fmt(figures["coverage_labelled"]["value"]),
        fmt(h265_block["value"] if h265_block else None),
        fmt(figures["review_load_per_100"], REVIEW_DIGITS),
        str(figures["not_a_material"]),
        str(figures["false_not_a_material"]),
        fmt(figures["mean_cost_usd"], COST_DIGITS),
    ]
    return "| " + " | ".join(cells) + " |"


TABLE_HEADER = (
    "| row | matched | correct | P | CP-LB | C₂₅₂ | H₂₆₅ | review /100 | not_a_material "
    "| F_NM | mean $/line |\n|---|---|---|---|---|---|---|---|---|---|---|"
)


def language_table(lang: str, payload: Mapping[str, Any]) -> list[str]:
    """Render one language's trade-off table: T1-T8, then the reference rows."""
    lines = [f"## {lang.upper()}", "", TABLE_HEADER]
    for entry in payload["thresholds"]:
        lines.append(table_row(entry["id"], entry["per_language"][lang]))
    references = payload["reference_rows"]
    lines.append(table_row("match-all", references["match_all"]["per_language"][lang]))
    if references["b2"] is not None:
        lines.append(
            table_row(
                f"B2 ({references['b2']['ledger_id']})", references["b2"]["per_language"][lang]
            )
        )
    return [*lines, ""]


def summary_lines(payload: Mapping[str, Any]) -> list[str]:
    """Render the selection, the per-threshold objective and the sensitivity rows."""
    selected, sensitivity = payload["selected"], payload["sensitivity"]
    lines = [
        f"Selected: **{selected['threshold_id']}**, {selected['status']} "
        f"(loosest qualifying: {selected['loosest_qualifying'] or 'none'}).",
        "",
        "| threshold | minimum (v, b, confidence) | Σ correct | qualifies |",
        "|---|---|---|---|",
    ]
    for entry in payload["thresholds"]:
        minimum = entry["minimum"]
        shape = f"{minimum['votes']}, {minimum['attributes']}, {minimum['confidence_bucket']}"
        lines.append(
            f"| {entry['id']} | {shape} | {entry['summed_correct']} | {entry['qualifies']} |"
        )
    optimum = sensitivity["per_language_optimum"]
    lines += [
        "",
        "Sensitivity rows (never shipped): "
        + ", ".join(
            f"{lang} alone {item['threshold_id']} ({item['status']})"
            for lang, item in optimum.items()
        ),
    ]
    if sensitivity["target"] is not None:
        target = sensitivity["target"]
        lines.append(f"Target {target['value']}: {target['threshold_id']} ({target['status']}).")
    return [*lines, ""]


def render_markdown(payload: Mapping[str, Any]) -> str:
    """Render ``selection_v1.md`` from the payload."""
    notes = payload["notes"]
    lines = [
        "# Threshold selection (§10.6)",
        "",
        f"Rendered from the selection JSON. Mean cost: {notes['mean_cost_usd']}. "
        f"Review load: {notes['review_load_per_100']}. H₂₆₅: {notes['h265']}.",
        "",
        *summary_lines(payload),
    ]
    for lang in LANGUAGES:
        lines += language_table(lang, payload)
    return NEWLINE.join(lines)


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line ends on every OS."""
    path.write_bytes(text.encode(ENCODING))


def write_outputs(args: argparse.Namespace, payload: Mapping[str, Any]) -> None:
    """Write the JSON (sorted keys) and the Markdown trade-off table."""
    text = json.dumps(payload, sort_keys=True, indent=JSON_INDENT, ensure_ascii=False)
    write_text(args.output_json, text + NEWLINE)
    write_text(args.output_md, render_markdown(payload))


# --- command line -----------------------------------------------------------------------


def run(args: argparse.Namespace, runtime: Runtime) -> int:
    """Refuse, replay, score, select and write.

    Args:
        args: The parsed arguments.
        runtime: The environment.

    Returns:
        0 once both files are written.

    """
    run_dirs = {"en": args.run_en, "fr": args.run_fr}
    manifests = {lang: read_manifest(path) for lang, path in run_dirs.items()}
    check_runs(run_dirs, manifests, args.split)
    inputs = ScoreInputs(args.reference, args.split, args.classes)
    with tempfile.TemporaryDirectory() as folder:
        results = {
            lang: evaluate_run(lang, path, inputs, runtime, Path(folder) / lang)
            for lang, path in run_dirs.items()
        }
    root = runtime.root()
    ledger = ledger_rows(args.ledger)
    context = Context(args, root, run_dirs, manifests, ledger)
    payload = build_payload(context, results, b2_reference(ledger, args.b2_id, root, inputs))
    write_outputs(args, payload)
    selected = payload["selected"]
    print(f"selected {selected['threshold_id']} ({selected['status']}); wrote {args.output_json}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run-en", type=Path, required=True)
    parser.add_argument("--run-fr", type=Path, required=True)
    parser.add_argument("--target", type=float)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--classes", type=Path, default=DEFAULT_CLASSES)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--b2-id", default=DEFAULT_B2_ID)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MARKDOWN)
    return parser


def main(argv: Sequence[str] | None = None, runtime: Runtime | None = None) -> int:
    """Run the selection; return the exit code.

    Args:
        argv: Arguments; ``sys.argv[1:]`` when None.
        runtime: The environment; rooted at the repository when None.

    Returns:
        0 ok, 2 bad input or an aborted replay, 4 refused.

    """
    args = build_parser().parse_args(argv)
    if args.target is not None and not TARGET_MIN < args.target <= TARGET_MAX:
        print("error: --target must be in (0, 1]", file=sys.stderr)
        return EXIT_ERROR
    try:
        return run(args, runtime or Runtime(root=lambda: ROOT))
    except RefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (SelectionError, WiringError, ConfigError, OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR


def use_utf8_streams() -> None:
    """Print UTF-8 whatever the console code page is, so ``oris select`` can decode it."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding=ENCODING)


if __name__ == "__main__":
    use_utf8_streams()
    sys.exit(main())
