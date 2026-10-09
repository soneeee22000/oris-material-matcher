r"""Record the B1 TF-IDF baseline in the experiment ledger (DESIGN.md §7.2, §10.5; G2-T0).

Runs ``eval/baseline_tfidf.py``'s ``run_b1`` on the dev side, with the one threshold chosen
on dev by the §10.6 rule, into ``runs/b1-dev-<sha8 of summary.json>/`` (``output_en.csv``,
``output_fr.csv``, ``summary.json``). Each language's output is scored as the experiment runner
scores a whole-side run (``--reference --split --side dev --strict --classes``), then one row
per language is appended through ``run_experiment.append_ledger``, both under the one id
``B1-dev``, and ``eval/experiments.md`` is re-rendered. B1 makes no model call: mode
``offline``, $0, no prompt version.

It is a module of its own, not a flag of ``baseline_tfidf.py``, because G2-T2 owns that file.

Usage::

    python eval/b1_ledger.py --ledger eval/experiments.jsonl [--runs-dir runs]

Exit codes: 0 ok, 2 bad input.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from oris_matcher.io.audit import portable_path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).resolve().parent
BASELINE_PATH = EVAL_DIR / "baseline_tfidf.py"
BASELINE_MODULE = "oris_eval_baseline_tfidf_for_b1_ledger"
RUNNER_PATH = EVAL_DIR / "run_experiment.py"
RUNNER_MODULE = "oris_eval_run_experiment_for_b1_ledger"
DEFAULT_LEDGER = ROOT / "eval" / "experiments.jsonl"
DEFAULT_RUNS_DIR = ROOT / "runs"
DEFAULT_INPUTS = {
    "en": ROOT / "input" / "boq_dataset_input_en.csv",
    "fr": ROOT / "input" / "boq_dataset_input_fr.csv",
}
DEFAULT_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
DEFAULT_REFERENCE = ROOT / "data" / "boq_dataset_matched_GT.csv"
DEFAULT_SPLIT = ROOT / "eval" / "split_v1.json"
DEFAULT_CLASSES = ROOT / "eval" / "annotations" / "blank_line_classes.csv"
LANGUAGES = ("en", "fr")
SIDE_DEV = "dev"
SUMMARY_FILE = "summary.json"
ENCODING = "utf-8"
OUTPUT_FILE = "output_{lang}.csv"
RUN_PREFIX = "b1-dev-"
TEMP_PREFIX = ".b1-dev-tmp-"
SHA_PREFIX = 8
LEDGER_ID = "B1-dev"
PROFILE = "b1"
LLM = "tfidf-b1"
MODE = "offline"
STATUS_SCORED = "scored"
EXIT_OK = 0
EXIT_ERROR = 2
THRESHOLD_FORMAT = ".4f"
HYPOTHESIS = (
    "B1 TF-IDF baseline on dev at the one §10.6 threshold, recorded in the ledger (G1 §17 figures)"
)

Clock = Callable[[], datetime]


class B1LedgerError(Exception):
    """A script could not be loaded, or B1 or the scorer failed."""


@dataclass(frozen=True)
class RowContext:
    """Where and when the rows are written.

    Attributes:
        root: The repository root, for portable output paths.
        ledger: The JSONL ledger.
        moment: When the rows are written.

    """

    root: Path
    ledger: Path
    moment: datetime


def _load_script(name: str, path: Path) -> ModuleType:
    """Load a standalone ``eval/`` script (not a package module) once, by file path."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise B1LedgerError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_baseline() -> ModuleType:
    """Load ``eval/baseline_tfidf.py`` once."""
    return _load_script(BASELINE_MODULE, BASELINE_PATH)


def load_runner() -> ModuleType:
    """Load ``eval/run_experiment.py`` once."""
    return _load_script(RUNNER_MODULE, RUNNER_PATH)


def b1_argv(args: argparse.Namespace, folder: Path) -> list[str]:
    """Return ``baseline_tfidf.py b1`` arguments that write the dev outputs into ``folder``."""
    argv = ["b1", "--input-en", str(args.input_en), "--input-fr", str(args.input_fr)]
    argv += ["--library", str(args.library), "--reference", str(args.reference)]
    argv += ["--split", str(args.split), "--side", SIDE_DEV]
    argv += ["--output-en", str(folder / OUTPUT_FILE.format(lang="en"))]
    argv += ["--output-fr", str(folder / OUTPUT_FILE.format(lang="fr"))]
    return [*argv, "--summary", str(folder / SUMMARY_FILE)]


def run_b1_into(args: argparse.Namespace, folder: Path) -> None:
    """Run B1 on dev into ``folder``; its printed summary is kept off stdout.

    Raises:
        B1LedgerError: B1 refused or failed.

    """
    baseline = load_baseline()
    b1_args = baseline.build_parser().parse_args(b1_argv(args, folder))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            code = baseline.run_b1(b1_args)
    except (baseline.BaselineError, OSError, KeyError, ValueError) as error:
        raise B1LedgerError(f"B1 failed: {error}") from error
    if code != EXIT_OK:
        raise B1LedgerError(f"B1 exited with {code}")


def summary_sha8(folder: Path) -> str:
    """Return the first 8 hex digits of ``summary.json``'s SHA-256."""
    return hashlib.sha256((folder / SUMMARY_FILE).read_bytes()).hexdigest()[:SHA_PREFIX]


def produce_run(args: argparse.Namespace) -> Path:
    """Run B1 into a temporary folder, then name it after its summary's hash.

    Args:
        args: The parsed arguments.

    Returns:
        ``<runs-dir>/b1-dev-<sha8>/``. B1 is deterministic, so an existing folder of that
        name already holds the same summary and is kept.

    """
    runs_dir: Path = args.runs_dir
    runs_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=runs_dir))
    try:
        run_b1_into(args, temporary)
        folder = runs_dir / f"{RUN_PREFIX}{summary_sha8(temporary)}"
        if not folder.exists():
            temporary.rename(folder)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return folder


def score_language(args: argparse.Namespace, lang: str, output: Path) -> dict[str, Any]:
    """Score one language's output with the runner's whole-side scorer flags.

    Raises:
        B1LedgerError: The scorer rejected the output.

    """
    runner = load_runner()
    scoring = argparse.Namespace(
        lang=lang,
        reference=args.reference,
        split=args.split,
        side=SIDE_DEV,
        slice=None,
        limit=None,
        lockbox_session=False,
        classes=args.classes,
    )
    scorer = runner.load_scorer()
    try:
        report = scorer.run(scorer.parse_settings(runner.score_argv(scoring, output)))
    except scorer.ScoreError as error:
        raise B1LedgerError(f"the scorer rejected {output}: {error}") from error
    scored: dict[str, Any] = report["outputs"][0]
    return scored


def run_fields(folder: Path, lang: str, summary: dict[str, Any], root: Path) -> dict[str, Any]:
    """Return what identifies one language's B1 run: id, profile, mode, item count and output.

    Args:
        folder: The run folder.
        lang: The language.
        summary: B1's ``summary.json``.
        root: The repository root, for the portable output path.

    Returns:
        The run's ledger fields; B1 makes no call, so it spends nothing.

    """
    return {
        "id": LEDGER_ID,
        "run_id": folder.name,
        "lang": lang,
        "profile": PROFILE,
        "llm": LLM,
        "mode": MODE,
        "side": SIDE_DEV,
        "slice_sha256": None,
        "limit": None,
        "n_lines": len(summary["cosine_scores"][lang]),
        "spend_usd": 0,
        "attributed_cost_usd": 0,
        "output": portable_path(folder / OUTPUT_FILE.format(lang=lang), root),
        "exit_code": EXIT_OK,
    }


def ledger_row(
    folder: Path, lang: str, summary: dict[str, Any], score: dict[str, Any], context: RowContext
) -> dict[str, Any]:
    """Build one language's B1 ledger row, with the runner's null comparison fields.

    Args:
        folder: The run folder.
        lang: The language.
        summary: B1's ``summary.json``.
        score: The scored output of that language.
        context: The repository root, the ledger and the moment.

    Returns:
        The row, ``kept: baseline``.

    """
    runner = load_runner()
    threshold = format(summary["threshold"], THRESHOLD_FORMAT)
    row = {
        **run_fields(folder, lang, summary, context.root),
        "date": context.moment.astimezone(UTC).isoformat(),
        "hypothesis": HYPOTHESIS,
        "cause_targeted": None,
        "change": f"cosine threshold {threshold} (§10.6 on dev)",
        "status": STATUS_SCORED,
        "prompt_version": [],
        "policy_resolution": f"b1_cosine_{threshold}",
        "metrics": runner.metrics_summary(score, True),
        "latency_s_per_routed": None,
    }
    filled: dict[str, Any] = runner.with_comparison(row, None, context.ledger)
    return filled


def record(args: argparse.Namespace, root: Path, clock: Clock) -> Path:
    """Run B1, score both languages, append their rows and re-render the Markdown ledger.

    Args:
        args: The parsed arguments.
        root: The repository root.
        clock: Returns the rows' timestamp.

    Returns:
        The run folder.

    """
    runner = load_runner()
    folder = produce_run(args)
    summary = json.loads((folder / SUMMARY_FILE).read_text(encoding=ENCODING))
    context = RowContext(root, args.ledger, clock())
    for lang in LANGUAGES:
        score = score_language(args, lang, folder / OUTPUT_FILE.format(lang=lang))
        runner.append_ledger(args.ledger, ledger_row(folder, lang, summary, score, context))
        print(f"ledger: appended {LEDGER_ID} {lang} ({folder.name}) to {args.ledger}")
    runner.render_markdown(args.ledger)
    return folder


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--input-en", type=Path, default=DEFAULT_INPUTS["en"])
    parser.add_argument("--input-fr", type=Path, default=DEFAULT_INPUTS["fr"])
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--classes", type=Path, default=DEFAULT_CLASSES)
    return parser


def _now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def main(argv: Sequence[str] | None = None, root: Path = ROOT, clock: Clock = _now) -> int:
    """Record the B1 rows; return the exit code.

    Args:
        argv: Arguments; ``sys.argv[1:]`` when None.
        root: The repository root, for portable output paths.
        clock: Returns the rows' timestamp.

    Returns:
        0 ok, 2 bad input.

    """
    args = build_parser().parse_args(argv)
    try:
        record(args, root, clock)
    except (B1LedgerError, OSError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
