r"""Experiment runner: the only writer of ``eval/experiments.jsonl`` (DESIGN.md §7.2, §10.3).

One invocation is one configuration on one language. It reads the whole BoQ (so headers and
section paths are computed on the full file), selects the rule-decided rows plus the item
lines of the requested split side, optionally narrowed to ``--slice`` and to the first
``--limit`` selected items in file order, and runs ``MatchService`` on that selection only:
no other item line is batched, rendered or sent. It writes ``runs/<run_id>/`` with the output
CSV, scores the output with ``eval/score.py`` (``--strict`` when the whole side was run) and
appends one ledger row, append-only. With ``--baseline-id <ledger id>`` the row also carries
the §7.2 comparison against that id's run in the same language: before/after correct matches,
d (discordant items, pooled per item over EN and FR once both languages' rows exist), the exact
one-sided sign-test p and the keep verdict; without it those fields are null and kept is
``baseline``.

The lockbox (§10.3) is reached only by the post-freeze session: ``--side all
--lockbox-session``, refused unless the git tag ``eval-freeze`` points at HEAD, with neither
``--slice``, ``--limit`` nor ``--baseline-id``. It runs the full file once per frozen rung
(profile, language, code SHA), never reads the response cache, scores the lockbox side and
appends its row to ``eval/lockbox_log.md``; a second session of the same rung is refused.
``--side lockbox`` alone is always refused.

Usage::

    python eval/run_experiment.py --lang en --input input/boq_dataset_input_en.csv \
        --library data/oris_materials_global.csv --split eval/split_v1.json --side dev \
        [--slice eval/slice_v1.json] [--limit 5] [--profile b2|b3] [--llm fake] \
        [--budget-usd 1.00] [--baseline-id E-00] --id E-00 --hypothesis "..." \
        --change "..."

Exit codes: 0 ok, 2 bad input, 3 a line was ``LLM_UNAVAILABLE`` or a replay miss, 4 refused.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from types import ModuleType
from typing import Any

from oris_matcher.cli import MatchJob, MatchOutcome, execute_match
from oris_matcher.doctor import Runtime, WiringError, parse_llm_spec
from oris_matcher.domain.boq import BoqFile, LineKind
from oris_matcher.io.audit import code_version, portable_path
from oris_matcher.service import RunProfile
from oris_matcher.settings import ConfigError, Settings

ROOT = Path(__file__).resolve().parents[1]
ENCODING = "utf-8"
LEDGER_NEWLINE = "\n"
SCORER_PATH = Path(__file__).resolve().parent / "score.py"
SCORER_MODULE = "oris_eval_score_for_experiments"
DEFAULT_LEDGER = ROOT / "eval" / "experiments.jsonl"
DEFAULT_LOCKBOX_LOG = ROOT / "eval" / "lockbox_log.md"
DEFAULT_REFERENCE = ROOT / "data" / "boq_dataset_matched_GT.csv"
DEFAULT_CLASSES = ROOT / "eval" / "annotations" / "blank_line_classes.csv"
DEFAULT_BUDGET_USD = 1.00
DEFAULT_LLM = "anthropic"
SCORE_FILE = "score.json"
OUTPUT_FILE = "output.csv"
MANIFEST_FILE = "manifest.json"
SIDE_DEV = "dev"
SIDE_LOCKBOX = "lockbox"
SIDE_ALL = "all"
SIDES = (SIDE_DEV, SIDE_LOCKBOX, SIDE_ALL)
SPLIT_FIELDS = {SIDE_DEV: "item_ids_dev", SIDE_LOCKBOX: "item_ids_lockbox"}
SLICE_IDS_FIELD = "item_ids"
SLICE_SPLIT_FIELD = "split_sha256"
FREEZE_TAG = "eval-freeze"
GIT_TAGS_AT_HEAD = ("git", "tag", "--points-at", "HEAD")
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_REFUSED = 4
JSON_INDENT = 2
KEEP_MIN_GAIN = 6
KEEP_SQRT_FACTOR = 2.0
MAX_LANGUAGE_LOSS = 2
DEV_BAR_PRECISION = 0.95
DEV_BAR_MIN_MATCHED = 40
MAX_COST_PER_100_LINES_USD = 2.0
MAX_SECONDS_PER_ROUTED_LINE = 2.0
LINES_PER_COST_UNIT = 100
KEPT_BASELINE = "baseline"
LIVE_MODE = "live"
LOG_CELL_SEPARATOR = "|"
LOG_MIN_CELLS = 13
LOG_COMMIT_CELL = 1
LOG_LANG_CELL = 3
LOG_HEADER = "timestamp"
DIGITS = 3


class ExperimentError(Exception):
    """The experiment's inputs are inconsistent: an unreadable split, a slice outside the side."""


class RefusedError(Exception):
    """The run would expose lockbox lines outside the single post-freeze lockbox session."""


@dataclass(frozen=True)
class Selection:
    """Which item ids may reach the model.

    Attributes:
        chosen: Item ids of the side, narrowed by the slice.
        forbidden: Item ids of the other side; none may ever be selected.
        limit: Keep only the first N chosen items in file order, or None.

    """

    chosen: frozenset[str]
    forbidden: frozenset[str]
    limit: int | None

    def line_ids(self, boq: BoqFile) -> frozenset[str]:
        """Return the rule-decided rows plus the chosen item lines of a whole parsed file.

        Args:
            boq: The parsed input.

        Returns:
            The selected line ids.

        Raises:
            RefusedError: A selected item belongs to the other side.

        """
        items = [line for line in boq.lines if line.kind == LineKind.ITEM]
        chosen = [line for line in items if line.item_no in self.chosen]
        if self.limit is not None:
            chosen = chosen[: self.limit]
        leaked = [line.item_no for line in chosen if line.item_no in self.forbidden]
        if leaked:
            raise RefusedError(f"selection holds items of the other side: {leaked[:3]}")
        rule_rows = [line for line in boq.lines if line.kind != LineKind.ITEM]
        return frozenset(line.line_id for line in (*rule_rows, *chosen))


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, raising ExperimentError when it cannot be read."""
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding=ENCODING))
    except (OSError, ValueError) as error:
        raise ExperimentError(f"cannot read {path}: {error}") from error
    return payload


def is_lockbox_session(args: argparse.Namespace) -> bool:
    """Tell whether the invocation is the full-file lockbox session."""
    return bool(args.lockbox_session) and args.side == SIDE_ALL


def build_selection(args: argparse.Namespace) -> Selection | None:
    """Read the split side, the optional slice and the limit.

    Args:
        args: The parsed arguments.

    Returns:
        The selection; None for the lockbox session, which runs every line of the file.

    Raises:
        ExperimentError: The split cannot be read, or the slice is not of this split and side.

    """
    split = _read_json(args.split)
    if is_lockbox_session(args):
        return None
    try:
        side = frozenset(split[SPLIT_FIELDS[args.side]])
        other = frozenset(split[SPLIT_FIELDS[_other_side(args.side)]])
    except KeyError as error:
        raise ExperimentError(f"{args.split}: no field {error}") from error
    if args.slice is None:
        return Selection(side, other, args.limit)
    slice_payload = _read_json(args.slice)
    if slice_payload.get(SLICE_SPLIT_FIELD) != file_sha256(args.split):
        raise ExperimentError(f"{args.slice} was not drawn from {args.split}")
    chosen = frozenset(slice_payload.get(SLICE_IDS_FIELD, ()))
    if not chosen <= side:
        raise ExperimentError(f"{args.slice} holds ids outside the {args.side} side")
    return Selection(chosen, other, args.limit)


def _other_side(side: str) -> str:
    """Return the other split side."""
    return SIDE_LOCKBOX if side == SIDE_DEV else SIDE_DEV


def _refuse_partial_lockbox(args: argparse.Namespace) -> None:
    """Refuse every lockbox route except a whole-file ``--side all --lockbox-session``."""
    if args.side == SIDE_LOCKBOX:
        raise RefusedError(
            "--side lockbox is refused: the lockbox session runs the full file "
            "(--side all --lockbox-session, after the freeze)"
        )
    if args.side == SIDE_ALL and not args.lockbox_session:
        raise RefusedError("--side all is refused: only a post-freeze --lockbox-session runs it")
    if args.lockbox_session and args.side != SIDE_ALL:
        raise RefusedError("--lockbox-session runs only --side all")
    if args.slice is not None or args.limit is not None or args.baseline_id is not None:
        raise RefusedError(
            "a lockbox session runs the whole file once: no --slice, --limit or --baseline-id"
        )


def check_lockbox_gate(args: argparse.Namespace, runtime: Runtime) -> None:
    """Refuse lockbox lines outside the single post-freeze, full-file session (§10.3).

    Args:
        args: The parsed arguments.
        runtime: Runs the read-only ``git tag --points-at HEAD``.

    Raises:
        RefusedError: A partial lockbox route, a session narrowed by slice, limit or baseline,
            or ``eval-freeze`` is not at HEAD.

    """
    if args.side == SIDE_DEV and not args.lockbox_session:
        return
    _refuse_partial_lockbox(args)
    try:
        tags = runtime.git(GIT_TAGS_AT_HEAD, runtime.root()).split()
    except (OSError, subprocess.SubprocessError) as error:
        raise RefusedError(f"cannot read the tags at HEAD ({error})") from error
    if FREEZE_TAG not in tags:
        raise RefusedError(f"--lockbox-session is refused: the tag {FREEZE_TAG} is not at HEAD")


def lockbox_log_rows(path: Path) -> list[list[str]]:
    """Return the data rows of the lockbox log's table, as stripped cells.

    Args:
        path: ``eval/lockbox_log.md``.

    Returns:
        One cell list per data row; header and separator rows are skipped.

    """
    if not path.is_file():
        return []
    rows: list[list[str]] = []
    for line in path.read_text(encoding=ENCODING).splitlines():
        cells = [cell.strip() for cell in line.strip().strip(LOG_CELL_SEPARATOR).split("|")]
        if len(cells) < LOG_MIN_CELLS or cells[0] == LOG_HEADER or not cells[0].strip("-"):
            continue
        rows.append(cells)
    return rows


def _rung_token(profile: str) -> re.Pattern[str]:
    """Match the rung name a log row's notes start with, e.g. ``B3``."""
    return re.compile(rf"^{re.escape(profile.upper())}\b")


def check_once_only(args: argparse.Namespace, runtime: Runtime) -> None:
    """Refuse a second lockbox session of the same frozen rung (profile, language, code SHA).

    Args:
        args: The parsed arguments.
        runtime: Reads the code SHA with ``git rev-parse HEAD``.

    Raises:
        RefusedError: The code SHA is unknown, or the log already holds this rung.

    """
    if not is_lockbox_session(args):
        return
    sha = code_version(runtime.root(), runtime.git).sha
    if sha is None:
        raise RefusedError("cannot read HEAD; the once-only rule needs the code SHA")
    token = _rung_token(args.profile)
    for cells in lockbox_log_rows(args.lockbox_log):
        same = sha in cells[LOG_COMMIT_CELL] and cells[LOG_LANG_CELL] == args.lang
        if same and token.search(cells[-1]):
            raise RefusedError(
                f"the {args.profile.upper()} {args.lang} lockbox run at {sha[:12]} is already "
                f"in {args.lockbox_log.name}; the lockbox is run once per frozen rung"
            )


def load_scorer() -> ModuleType:
    """Load ``eval/score.py`` (a standalone script, not a package module) once."""
    if SCORER_MODULE in sys.modules:
        return sys.modules[SCORER_MODULE]
    spec = importlib.util.spec_from_file_location(SCORER_MODULE, SCORER_PATH)
    if spec is None or spec.loader is None:
        raise ExperimentError(f"cannot load the scorer at {SCORER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[SCORER_MODULE] = module
    spec.loader.exec_module(module)
    return module


def is_strict_valid(args: argparse.Namespace) -> bool:
    """Tell whether ``--strict`` applies: only when every item of the side was run."""
    return args.slice is None and args.limit is None


def scored_side(args: argparse.Namespace) -> str:
    """Return the split side the output is scored on: the lockbox side for the session."""
    return SIDE_LOCKBOX if is_lockbox_session(args) else args.side


def portable_report(report: dict[str, Any], root: Path) -> dict[str, Any]:
    """Return the scorer's report with every file path made portable."""
    reference = {
        **report["reference"],
        "path": portable_path(Path(report["reference"]["path"]), root),
    }
    outputs = [
        {**output, "path": portable_path(Path(output["path"]), root)}
        for output in report["outputs"]
    ]
    return {**report, "reference": reference, "outputs": outputs}


def write_score(folder: Path, report: dict[str, Any], root: Path) -> None:
    """Write ``score.json``: sorted keys, LF line ends, UTF-8 and no host path, on every OS."""
    text = json.dumps(
        portable_report(report, root), sort_keys=True, indent=JSON_INDENT, ensure_ascii=False
    )
    (folder / SCORE_FILE).write_bytes((text + LEDGER_NEWLINE).encode(ENCODING))


def score_argv(args: argparse.Namespace, output: Path) -> list[str]:
    """Return the scorer's arguments for one output on the scored side."""
    argv = ["--output", str(output), "--label", args.lang]
    argv += ["--reference", str(args.reference), "--split", str(args.split)]
    argv += ["--side", scored_side(args)]
    argv += ["--strict"] if is_strict_valid(args) else []
    argv += ["--classes", str(args.classes)] if args.classes is not None else []
    return argv


def score_run(args: argparse.Namespace, outcome: MatchOutcome, root: Path) -> dict[str, Any]:
    """Score the run's output on its side and save the JSON report in the run folder.

    Args:
        args: The parsed arguments.
        outcome: The finished run.
        root: The repository root, for portable paths in ``score.json``.

    Returns:
        The scorer's report for this output.

    Raises:
        ExperimentError: The scorer rejects the output.

    """
    scorer = load_scorer()
    try:
        report = scorer.run(scorer.parse_settings(score_argv(args, outcome.output_path)))
    except scorer.ScoreError as error:
        raise ExperimentError(f"the scorer rejected the output: {error}") from error
    write_score(outcome.folder, report, root)
    sys.stdout.write(scorer.render(report))
    output: dict[str, Any] = report["outputs"][0]
    return output


def metrics_summary(score: dict[str, Any], strict: bool) -> dict[str, Any]:
    """Pick the ledger's metrics from one scored output.

    Args:
        score: One output of the scorer's report.
        strict: Whether ``--strict`` was applied.

    Returns:
        Precision with its bounds, coverage, F_NM, per-level proposal accuracy, hit@k,
        decision shares on item rows and the counts behind them.

    """
    return {
        "strict": strict,
        "precision": score["precision"],
        "coverage_labelled": score["coverage_labelled"],
        "coverage_material": score["coverage_material"],
        "false_not_a_material": score["false_not_a_material"],
        "per_level_labelled": score["per_level_labelled"],
        "hit_at_1": score["hit_at_1"]["value"],
        "hit_at_2": score["hit_at_2"]["value"],
        "decision_shares_items": score["decision_shares"]["item_rows"],
        "counts": score["counts"],
        "mean_cost_usd": score["mean_cost_usd"],
        "mean_latency_ms": score["mean_latency_ms"],
    }


def latency_per_routed(manifest: Mapping[str, Any]) -> float | None:
    """Return wall-clock seconds per routed line of a cold live run, else None (RQ8)."""
    if manifest.get("mode") != LIVE_MODE or manifest.get("cache_hits") != 0:
        return None
    routed = manifest.get("n_routed") or 0
    return float(manifest["wall_clock_s"]) / routed if routed else None


# --- the §7.2 comparison ---------------------------------------------------------------


def item_correctness(output: Path, args: argparse.Namespace) -> dict[str, int]:
    """Score each labelled item of the side as 1 (correct match) or 0, by its key.

    Args:
        output: An output CSV.
        args: The parsed arguments: reference, split and side.

    Returns:
        Item key -> 1 when the output matched it with the reference triple, else 0.

    """
    scorer = load_scorer()
    try:
        reference = scorer.parse_reference(scorer.read_table(args.reference), None, True)
        parsed = scorer.parse_output(scorer.read_table(output), None, True)
        side_ids = scorer.load_side_ids(args.split, scored_side(args))
    except scorer.ScoreError as error:
        raise ExperimentError(f"cannot score {output}: {error}") from error
    scope = scorer.build_scope(scorer.join_by_key(reference, parsed).pairs, side_ids, {})
    labelled = [pair for pair in scope.pairs if pair.ref.labels != scorer.BLANK]
    return {pair.key or "": scorer.count_correct([pair]) for pair in labelled}


def sign_test_p(improved: int, discordant: int) -> float:
    """Return the exact one-sided sign-test p: P(X >= improved), X ~ Binomial(d, 1/2).

    Args:
        improved: Discordant items the change got right and the baseline got wrong.
        discordant: d, every item whose correctness differs between the two runs.

    Returns:
        The p-value; 1.0 when there is no discordant item.

    """
    tail = sum(math.comb(discordant, count) for count in range(improved, discordant + 1))
    return float(Fraction(tail, 2**discordant))


def baseline_row(args: argparse.Namespace) -> dict[str, Any]:
    """Return the latest ledger row of ``--baseline-id`` in this language.

    Args:
        args: The parsed arguments.

    Returns:
        The baseline row.

    Raises:
        ExperimentError: The ledger holds no such row, or it ran another side, slice or limit,
            so its item set differs.

    """
    rows = [
        row
        for row in ledger_rows(args.ledger)
        if row.get("id") == args.baseline_id and row.get("lang") == args.lang
    ]
    if not rows:
        raise ExperimentError(f"--baseline-id {args.baseline_id}: no {args.lang} ledger row")
    row = rows[-1]
    mine = {
        "side": args.side,
        "slice_sha256": file_sha256(args.slice) if args.slice else None,
        "limit": args.limit,
    }
    differing = sorted(key for key, value in mine.items() if row.get(key) != value)
    if differing:
        raise ExperimentError(f"--baseline-id {args.baseline_id} ran another {differing}")
    return row


def _baseline_output(args: argparse.Namespace, row: Mapping[str, Any], root: Path) -> Path:
    """Return the baseline run's output CSV: its run folder's copy, else its recorded path."""
    candidates = [args.runs_dir / str(row["run_id"]) / OUTPUT_FILE]
    if isinstance(row.get("output"), str):
        candidates.append(root / row["output"])
    found = next((path for path in candidates if path.is_file()), None)
    if found is None:
        raise ExperimentError(f"--baseline-id {args.baseline_id}: no output of {row['run_id']}")
    return found


def compare_with_baseline(
    args: argparse.Namespace, after_output: Path, root: Path
) -> dict[str, Any] | None:
    """Compare this run with the ``--baseline-id`` row's run item by item, on this language.

    Args:
        args: The parsed arguments.
        after_output: This run's output CSV.
        root: The repository root.

    Returns:
        The baseline id and run id, correct counts before and after, and the per-item deltas
        (after - before, non-zero only); None without ``--baseline-id``.

    Raises:
        ExperimentError: No baseline row or output, or it scored another item set.

    """
    if args.baseline_id is None:
        return None
    row = baseline_row(args)
    before = item_correctness(_baseline_output(args, row, root), args)
    after = item_correctness(after_output, args)
    if set(before) != set(after):
        raise ExperimentError(f"--baseline-id {args.baseline_id} was scored on other items")
    deltas = {key: after[key] - before[key] for key in sorted(after) if after[key] != before[key]}
    return {
        "baseline_id": args.baseline_id,
        "before_run_id": str(row["run_id"]),
        "before_correct": sum(before.values()),
        "after_correct": sum(after.values()),
        "item_deltas": deltas,
    }


def _dev_bar(metrics: dict[str, Any]) -> bool:
    """Whether one language meets the §10.6 dev bar on the whole side."""
    precision = metrics["precision"]
    value = precision.get("value")
    enough = precision.get("matched", 0) >= DEV_BAR_MIN_MATCHED
    return bool(metrics["strict"]) and value is not None and value >= DEV_BAR_PRECISION and enough


def _cost_ok(metrics: dict[str, Any]) -> bool:
    """Whether one language's mean attributed cost stays within $2.00 per 100 lines."""
    mean = metrics.get("mean_cost_usd")
    return mean is not None and mean * LINES_PER_COST_UNIT <= MAX_COST_PER_100_LINES_USD


def keep_checks(rows: Sequence[dict[str, Any]], gain: int, discordant: int) -> dict[str, bool]:
    """Return the gating §7.2 keep conditions over the compared rows (one per language).

    Args:
        rows: This row, and the other language's row when it exists.
        gain: Summed correct-match delta over the rows.
        discordant: d, the discordant items pooled per item.

    Returns:
        ``gain`` (>= max(6, 2 sqrt d)), ``language_loss`` (no language loses more than 2),
        ``f_nm_zero``, ``dev_bar`` (§10.6 in each language), ``cost`` (within $2.00 per 100
        lines) and ``latency`` (within 2 s per routed line; passes when no cold live run
        measured it, which ``keep_notes`` records).

    """
    deltas = [
        row["comparison"]["after_correct"] - row["comparison"]["before_correct"] for row in rows
    ]
    return {
        "gain": gain >= max(KEEP_MIN_GAIN, KEEP_SQRT_FACTOR * math.sqrt(discordant)),
        "language_loss": all(delta >= -MAX_LANGUAGE_LOSS for delta in deltas),
        "f_nm_zero": all(row["metrics"]["false_not_a_material"] == 0 for row in rows),
        "dev_bar": all(_dev_bar(row["metrics"]) for row in rows),
        "cost": all(_cost_ok(row["metrics"]) for row in rows),
        "latency": all(value <= MAX_SECONDS_PER_ROUTED_LINE for value in _latencies(rows)),
    }


def _latencies(rows: Sequence[dict[str, Any]]) -> list[float]:
    """Return the cold live latencies (s per routed line) the rows measured."""
    return [row["latency_s_per_routed"] for row in rows if row.get("latency_s_per_routed")]


def keep_notes(rows: Sequence[dict[str, Any]]) -> dict[str, bool]:
    """Return what the gating checks could not observe: whether latency was measured.

    Args:
        rows: The compared rows.

    Returns:
        ``latency_measured``: False when no cold live run measured latency.

    """
    return {"latency_measured": bool(_latencies(rows))}


def _pooled_deltas(rows: Sequence[dict[str, Any]]) -> Counter[str]:
    """Pool the rows' per-item deltas, keyed by language and item."""
    pooled: Counter[str] = Counter()
    for row in rows:
        deltas = row["comparison"]["item_deltas"].items()
        pooled.update({f"{row['lang']}:{key}": value for key, value in deltas})
    return pooled


def keep_verdict(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Pool the rows' item deltas and apply the §7.2 keep rule.

    Args:
        rows: This row, and the other language's row of the same experiment when it exists.

    Returns:
        d, the exact one-sided sign-test p (a note, not a gate), the gain, the gating checks,
        the notes and kept.

    """
    discordant = [delta for delta in _pooled_deltas(rows).values() if delta != 0]
    gain = sum(discordant)
    checks = keep_checks(rows, gain, len(discordant))
    return {
        "d": len(discordant),
        "sign_test_p": sign_test_p(sum(delta > 0 for delta in discordant), len(discordant)),
        "gain": gain,
        "keep_checks": checks,
        "keep_notes": keep_notes(rows),
        "kept": all(checks.values()),
    }


def ledger_rows(path: Path) -> list[dict[str, Any]]:
    """Read the ledger's rows, oldest first; an absent ledger has none."""
    if not path.is_file():
        return []
    lines = path.read_text(encoding=ENCODING).splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _pair_row(row: dict[str, Any], ledger: Path) -> dict[str, Any] | None:
    """Return the latest compared row of this experiment and baseline in the other language."""
    same = ("id", "side", "profile", "slice_sha256", "limit", "baseline_id")
    for other in reversed(ledger_rows(ledger)):
        if other.get("comparison") is None or other.get("lang") == row["lang"]:
            continue
        if all(other.get(key) == row.get(key) for key in same):
            return dict(other)
    return None


def with_comparison(
    row: dict[str, Any], comparison: dict[str, Any] | None, ledger: Path
) -> dict[str, Any]:
    """Fill a ledger row's before/after, d, sign-test p and kept from the comparison.

    Args:
        row: The row without its comparison fields.
        comparison: From ``compare_with_baseline``, or None.
        ledger: The ledger, searched for the other language's row of the experiment.

    Returns:
        The row. Without a baseline, before/after/d/p are null and kept is ``baseline``. With
        one, the verdict covers this language, pooled with the other language's compared row
        of the same experiment and baseline when it exists.

    """
    if comparison is None:
        nulls = dict.fromkeys(("before", "after", "d", "sign_test_p", "comparison", "baseline_id"))
        return {**row, **nulls, "kept": KEPT_BASELINE}
    filled = {
        **row,
        "baseline_id": comparison["baseline_id"],
        "before": {"run_id": comparison["before_run_id"], "correct": comparison["before_correct"]},
        "after": {"run_id": row["run_id"], "correct": comparison["after_correct"]},
        "comparison": comparison,
    }
    other = _pair_row(filled, ledger)
    verdict = keep_verdict([filled] if other is None else [filled, other])
    pooled_with = None if other is None else other["run_id"]
    filled["comparison"] = {**comparison, "pooled_with": pooled_with, **verdict}
    return {
        **filled,
        "d": verdict["d"],
        "sign_test_p": verdict["sign_test_p"],
        "kept": verdict["kept"],
    }


def ledger_row(
    args: argparse.Namespace,
    outcome: MatchOutcome,
    score: dict[str, Any],
    moment: datetime,
    root: Path,
) -> dict[str, Any]:
    """Build one ledger row (§7.2) before its comparison fields.

    Args:
        args: The parsed arguments.
        outcome: The finished run.
        score: The scored output.
        moment: When the row is written.
        root: The repository root, for the portable output path.

    Returns:
        The row.

    """
    manifest = outcome.manifest
    routed = sum(item.line.kind == LineKind.ITEM for item in outcome.result.lines)
    return {
        "id": args.id,
        "date": moment.astimezone(UTC).isoformat(),
        "hypothesis": args.hypothesis,
        "change": args.change,
        "run_id": outcome.result.run_id,
        "lang": args.lang,
        "profile": outcome.result.profile.value,
        "llm": manifest["llm"],
        "mode": manifest["mode"],
        "side": args.side,
        "slice_sha256": file_sha256(args.slice) if args.slice else None,
        "limit": args.limit,
        "prompt_version": list(outcome.result.prompt_versions),
        "policy_resolution": manifest["policy_resolution"],
        "n_lines": routed,
        "metrics": metrics_summary(score, is_strict_valid(args)),
        "spend_usd": manifest["spend_usd"],
        "attributed_cost_usd": manifest["attributed_cost_usd"],
        "latency_s_per_routed": latency_per_routed(manifest),
        "output": portable_path(outcome.output_path, root),
        "exit_code": outcome.result.exit_code,
    }


def append_ledger(path: Path, row: dict[str, Any]) -> None:
    """Append one row to the JSONL ledger; earlier rows are never rewritten.

    Args:
        path: The ledger file.
        row: The row.

    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, ensure_ascii=False) + LEDGER_NEWLINE
    with path.open("a", encoding=ENCODING, newline=LEDGER_NEWLINE) as handle:
        handle.write(line)


def _fmt(value: float | None) -> str:
    """Format a proportion or a rate for the lockbox log, ``n/a`` when absent."""
    return "n/a" if value is None else f"{value:.{DIGITS}f}"


def lockbox_log_line(
    args: argparse.Namespace, outcome: MatchOutcome, score: dict[str, Any], moment: datetime
) -> str:
    """Return the lockbox log's table row for one session run (§10.3).

    Args:
        args: The parsed arguments.
        outcome: The finished full-file run.
        score: The output scored on the lockbox side.
        moment: When the row is written.

    Returns:
        One Markdown table row, LF-terminated.

    """
    manifest, precision = outcome.manifest, score["precision"]
    wilson = precision.get("wilson_95") or [None, None]
    mean_cost = score.get("mean_cost_usd")
    coverage_material = (score.get("coverage_material") or {}).get("value")
    cells = [
        moment.astimezone(UTC).isoformat(),
        f"{FREEZE_TAG} / {manifest.get('code_sha')}",
        str(manifest.get("split_sha256")),
        args.lang,
        f"{_fmt(precision.get('value'))} [{_fmt(wilson[0])}, {_fmt(wilson[1])}]",
        str(precision.get("matched")),
        _fmt(score["coverage_labelled"]["value"]),
        _fmt(coverage_material),
        "n/a",
        str(score["false_not_a_material"]),
        _fmt(None if mean_cost is None else mean_cost * LINES_PER_COST_UNIT),
        _fmt(latency_per_routed(manifest)),
        f"{outcome.result.profile.value.upper()} run {outcome.result.run_id}; lockbox side, strict",
    ]
    return f"| {' | '.join(cells)} |{LEDGER_NEWLINE}"


def append_lockbox_log(path: Path, line: str) -> None:
    """Append one row to ``eval/lockbox_log.md``; earlier rows are never rewritten."""
    with path.open("a", encoding=ENCODING, newline=LEDGER_NEWLINE) as handle:
        handle.write(line)


def _budget(args: argparse.Namespace) -> float | None:
    """Return the hard budget: as given, else $1.00 for dev and the §11.3 cap for the session."""
    if args.budget_usd is not None:
        return float(args.budget_usd)
    return None if is_lockbox_session(args) else DEFAULT_BUDGET_USD


def build_job(args: argparse.Namespace, selection: Selection | None) -> MatchJob:
    """Describe the experiment's run as a match job.

    Args:
        args: The parsed arguments.
        selection: Which items may reach the model; None for the full-file lockbox session.

    Returns:
        The job; the lockbox session never reads the cache.

    """
    return MatchJob(
        input_path=args.input,
        library_path=args.library,
        llm=parse_llm_spec(args.llm),
        output_path=args.output,
        profile=RunProfile(args.profile),
        use_cache=not args.no_cache and not is_lockbox_session(args),
        runs_dir=args.runs_dir,
        selector=selection.line_ids if selection is not None else None,
        split_sha256=file_sha256(args.split),
        budget_usd=_budget(args),
    )


def record(args: argparse.Namespace, outcome: MatchOutcome, runtime: Runtime) -> None:
    """Score the run and append its ledger row, or its lockbox log row for the session."""
    score = score_run(args, outcome, runtime.root())
    moment = runtime.clock()
    if is_lockbox_session(args):
        append_lockbox_log(args.lockbox_log, lockbox_log_line(args, outcome, score, moment))
        print(f"lockbox log: appended {outcome.result.run_id} to {args.lockbox_log}")
        return
    root = runtime.root()
    comparison = compare_with_baseline(args, outcome.output_path, root)
    row = ledger_row(args, outcome, score, moment, root)
    row = with_comparison(row, comparison, args.ledger)
    append_ledger(args.ledger, row)
    print(f"ledger: appended {args.id} ({outcome.result.run_id}) to {args.ledger}")


def run(args: argparse.Namespace, runtime: Runtime) -> int:
    """Run, score and log one experiment.

    Args:
        args: The parsed arguments.
        runtime: The environment.

    Returns:
        The run's exit code: 0, or 3 when a line was ``LLM_UNAVAILABLE`` or a replay miss.

    """
    check_lockbox_gate(args, runtime)
    check_once_only(args, runtime)
    selection = build_selection(args)
    outcome = execute_match(build_job(args, selection), Settings(), runtime)
    record(args, outcome, runtime)
    return outcome.result.exit_code


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--lang", choices=("en", "fr"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--side", choices=SIDES, default=SIDE_DEV)
    parser.add_argument("--lockbox-session", action="store_true")
    parser.add_argument("--slice", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--profile", choices=[profile.value for profile in RunProfile])
    parser.add_argument("--llm", default=DEFAULT_LLM)
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument("--baseline-id")
    parser.add_argument("--id", required=True)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--change", required=True)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--classes", type=Path, default=DEFAULT_CLASSES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--lockbox-log", type=Path, default=DEFAULT_LOCKBOX_LOG)
    parser.add_argument("--no-cache", action="store_true")
    parser.set_defaults(profile=RunProfile.B3.value)
    return parser


def main(argv: Sequence[str] | None = None, runtime: Runtime | None = None) -> int:
    """Run the experiment; return the exit code.

    Args:
        argv: Arguments; ``sys.argv[1:]`` when None.
        runtime: The environment; the default one when None.

    Returns:
        0 ok, 2 bad input, 3 an unavailable model or a replay miss, 4 refused.

    """
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        print("error: --limit must be at least 1", file=sys.stderr)
        return EXIT_ERROR
    try:
        return run(args, runtime or Runtime())
    except RefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (ExperimentError, WiringError, ConfigError, OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
