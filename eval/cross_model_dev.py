r"""Cross-model agreement on dev: could a second small model raise coverage? (post-freeze).

This is an analysis added on 9 Oct 2026, after the lockbox was scored. It reads two recorded dev
runs on the same library, the shipped Haiku configuration and gpt-4o-mini (``FB-dev``, G2 O26),
and asks what a cross-model vote would have done. It never calls a model and never reads a
lockbox item, so it cannot change any claim; every figure is dev-only.

The policies compare the second model's per-pass top-1 row codes with the primary's top-1:

- ``shipped``: the primary's own decisions;
- ``rescue_any`` / ``rescue_both``: a primary ``needs_review`` line becomes a match on the
  primary's suggestion when at least one / both second-model passes name the same row;
- ``veto_any``: a primary match is kept only when at least one second-model pass agrees.

The run folders are local (``runs/*`` is git-ignored), so ``extract`` writes the per-item votes
to a committed JSON file and ``report`` works from that file alone.

Usage::

    python eval/cross_model_dev.py extract --split eval/split_v1.json \
        --primary en=runs/<id> --primary fr=runs/<id> \
        --second en=runs/<id> --second fr=runs/<id> --output eval/cross_model_dev_votes.json
    python eval/cross_model_dev.py report --votes eval/cross_model_dev_votes.json \
        --reference data/boq_dataset_matched_GT.csv --output eval/cross_model_dev [--check]

Exit codes: 0 written (or ``--check`` current), 1 ``--check`` drift, 2 bad input.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

ENCODING = "utf-8"
READ_ENCODING = "utf-8-sig"
JSON_INDENT = 2
SCORER_MODULE = "oris_eval_score_cross_model"
SCORER_PATH = Path(__file__).resolve().parent / "score.py"
AUDIT_FILE = "audit.jsonl"
MANIFEST_FILE = "manifest.json"
OUTPUT_FILE = "output.csv"
ITEM_COLUMN = "Item No."
LABEL_COLUMNS = ("material_type", "material_usage", "material_subtype")
SUGGESTED_COLUMNS = ("suggested_type", "suggested_usage", "suggested_subtype")
MATCHED = "matched"
NEEDS_REVIEW = "needs_review"
LANGUAGES = ("en", "fr")
POLICIES = ("shipped", "rescue_any", "rescue_both", "veto_any")
RESCUE_POLICIES = ("rescue_any", "rescue_both")
BOTH_PASSES = 2
RULE_MIN_ADDED_CORRECT = 3
MANIFEST_FIELDS = (
    "run_id",
    "requested_model",
    "mode",
    "library_sha256",
    "input_sha256",
    "threshold_id",
    "enrichment_sha256",
    "attributed_cost_usd",
)
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

Triple = tuple[str, str, str]


class AnalysisError(Exception):
    """The runs or the votes file cannot be analysed."""


@dataclass(frozen=True)
class Line:
    """One labelled dev line, seen by both models."""

    decision: str
    agreeing: int
    primary_correct: bool
    second_correct: bool
    second_type_correct: bool


def load_scorer() -> ModuleType:
    """Load ``eval/score.py`` (a standalone script, not a package module) once."""
    if SCORER_MODULE in sys.modules:
        return sys.modules[SCORER_MODULE]
    spec = importlib.util.spec_from_file_location(SCORER_MODULE, SCORER_PATH)
    if spec is None or spec.loader is None:
        raise AnalysisError(f"cannot load the scorer at {SCORER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[SCORER_MODULE] = module
    spec.loader.exec_module(module)
    return module


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV as dicts, BOM tolerated."""
    with path.open(encoding=READ_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def read_audit(path: Path) -> dict[str, dict[str, Any]]:
    """Return a run's audit records by item number."""
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding=ENCODING) as handle:
        for text in handle:
            record = json.loads(text)
            records[str(record["item_no"])] = record
    return records


def pass_top1s(record: Mapping[str, Any]) -> list[str]:
    """Return each pass's top-1 row code; an unparsable pass gives an empty code."""
    codes: list[str] = []
    for raw in record.get("raw_line_response") or []:
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            parsed = {}
        codes.append(str(parsed.get("top1") or "") if isinstance(parsed, dict) else "")
    return codes


def run_meta(run: Path) -> dict[str, Any]:
    """Return the manifest fields that identify a run."""
    manifest = json.loads((run / MANIFEST_FILE).read_text(encoding=ENCODING))
    return {field: manifest.get(field) for field in MANIFEST_FIELDS}


def run_items(run: Path, dev: set[str]) -> dict[str, dict[str, Any]]:
    """Return, per dev item of a run, its decision, top-1 code, pass codes and suggestion."""
    audit = read_audit(run / AUDIT_FILE)
    items: dict[str, dict[str, Any]] = {}
    for row in read_rows(run / OUTPUT_FILE):
        item = row[ITEM_COLUMN]
        if item not in dev:
            continue
        record = audit.get(item, {})
        items[item] = {
            "decision": row["decision"],
            "top1": str(record.get("top1") or ""),
            "pass_top1": pass_top1s(record),
            "suggested": [row[column] for column in SUGGESTED_COLUMNS],
        }
    return items


def extract_language(primary: Path, second: Path, dev: set[str]) -> dict[str, Any]:
    """Return one language's votes from the two runs, refusing runs on different inputs."""
    meta = {"primary": run_meta(primary), "second": run_meta(second)}
    for field in ("library_sha256", "input_sha256"):
        if meta["primary"][field] != meta["second"][field]:
            raise AnalysisError(f"the two runs differ in {field}")
    first, other = run_items(primary, dev), run_items(second, dev)
    if set(first) != set(other):
        raise AnalysisError("the two runs do not hold the same dev items")
    items = {
        item: {
            "primary": {key: first[item][key] for key in ("decision", "top1", "suggested")},
            "second": {key: other[item][key] for key in ("pass_top1", "suggested")},
        }
        for item in sorted(first)
    }
    return {"runs": meta, "items": items}


def extract(split: Path, primary: Mapping[str, Path], second: Mapping[str, Path]) -> dict[str, Any]:
    """Return the votes document for every language given."""
    dev = set(json.loads(split.read_text(encoding=ENCODING))["item_ids_dev"])
    if set(primary) != set(second):
        raise AnalysisError("--primary and --second must name the same languages")
    return {
        "note": "post-freeze, dev items only; no lockbox item is read",
        "split": split.as_posix(),
        "languages": {
            language: extract_language(primary[language], second[language], dev)
            for language in sorted(primary)
        },
    }


def load_reference(path: Path) -> dict[str, Triple]:
    """Return the reference labels by item number, ``nan``-like cells blanked as the scorer does."""
    scorer = load_scorer()
    labels: dict[str, Triple] = {}
    for row in read_rows(path):
        raw: Triple = (row[LABEL_COLUMNS[0]], row[LABEL_COLUMNS[1]], row[LABEL_COLUMNS[2]])
        cleaned, _ = scorer.blank_nan(raw)
        labels[row[ITEM_COLUMN]] = cleaned
    return labels


def as_triple(values: Sequence[str]) -> Triple:
    """Return a three-item suggestion as a triple."""
    return (values[0], values[1], values[2])


def classify(items: Mapping[str, Any], reference: Mapping[str, Triple]) -> list[Line]:
    """Return one ``Line`` per labelled item."""
    lines: list[Line] = []
    for item, votes in items.items():
        label = reference.get(item, ("", "", ""))
        if not any(label):
            continue
        primary, second = votes["primary"], votes["second"]
        second_suggestion = as_triple(second["suggested"])
        lines.append(
            Line(
                decision=primary["decision"],
                agreeing=sum(1 for code in second["pass_top1"] if code and code == primary["top1"]),
                primary_correct=as_triple(primary["suggested"]) == label,
                second_correct=second_suggestion == label,
                second_type_correct=second_suggestion[0] == label[0],
            )
        )
    return lines


def is_matched(line: Line, policy: str) -> bool:
    """Return whether a policy matches the line on the primary's suggestion."""
    if policy == "veto_any":
        return line.decision == MATCHED and line.agreeing >= 1
    if line.decision == MATCHED:
        return True
    needed = {"rescue_any": 1, "rescue_both": BOTH_PASSES}.get(policy)
    return line.decision == NEEDS_REVIEW and needed is not None and line.agreeing >= needed


def policy_figures(lines: Sequence[Line], policy: str) -> dict[str, Any]:
    """Return matched, correct, precision, its CP lower bound and coverage for one policy."""
    scorer = load_scorer()
    matched = [line for line in lines if is_matched(line, policy)]
    correct = sum(1 for line in matched if line.primary_correct)
    return {
        "matched": len(matched),
        "correct": correct,
        "precision": scorer.ratio(correct, len(matched)),
        "cp_lower_bound": scorer.cp_lower_bound(correct, len(matched)) if matched else None,
        "coverage": scorer.ratio(correct, len(lines)),
    }


def agreement_table(lines: Sequence[Line]) -> list[dict[str, Any]]:
    """Return counts by primary decision, agreeing second-model passes and primary correctness."""
    counts = Counter((line.decision, line.agreeing, line.primary_correct) for line in lines)
    return [
        {"decision": decision, "agreeing_passes": agreeing, "primary_correct": correct, "n": n}
        for (decision, agreeing, correct), n in sorted(counts.items())
    ]


def summarise_language(votes: Mapping[str, Any], reference: Mapping[str, Triple]) -> dict[str, Any]:
    """Return one language's figures."""
    lines = classify(votes["items"], reference)
    return {
        "runs": votes["runs"],
        "labelled": len(lines),
        "suggestion_correct": {
            "primary": sum(line.primary_correct for line in lines),
            "second": sum(line.second_correct for line in lines),
            "second_type_only": sum(line.second_type_correct for line in lines),
        },
        "agreement": agreement_table(lines),
        "policies": {policy: policy_figures(lines, policy) for policy in POLICIES},
    }


def rule_verdict(languages: Mapping[str, Mapping[str, Any]], policy: str) -> dict[str, Any]:
    """Apply the pre-registered E-02(d) rule (DESIGN.md §7.2) to one rescue policy.

    A cross-model vote is adopted only if it adds at least three summed correct matches at
    equal precision; "equal" is read here as "not lower in any language".
    """
    added = 0
    precision_held = True
    for figures in languages.values():
        shipped, policy_row = figures["policies"]["shipped"], figures["policies"][policy]
        added += policy_row["correct"] - shipped["correct"]
        if policy_row["matched"] and policy_row["precision"] < shipped["precision"]:
            precision_held = False
    return {
        "added_correct": added,
        "precision_not_lower": precision_held,
        "adopted": added >= RULE_MIN_ADDED_CORRECT and precision_held,
    }


def build_report(votes: Mapping[str, Any], reference: Mapping[str, Triple]) -> dict[str, Any]:
    """Return the report document."""
    languages = {
        language: summarise_language(language_votes, reference)
        for language, language_votes in votes["languages"].items()
    }
    return {
        "note": votes["note"],
        "languages": languages,
        "e02d_rule": {policy: rule_verdict(languages, policy) for policy in RESCUE_POLICIES},
    }


def fmt(value: float | None) -> str:
    """Return a ratio to three decimals, or ``n/a``."""
    return "n/a" if value is None else f"{value:.3f}"


def runs_lines(runs: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return the Markdown table that identifies the two runs."""
    lines = [
        "| role | model | mode | run | threshold | enrichment | attributed cost (USD) |",
        "|---|---|---|---|---|---|---|",
    ]
    for role in ("primary", "second"):
        meta = runs[role]
        enrichment = str(meta["enrichment_sha256"] or "none")[:12]
        lines.append(
            f"| {role} | {meta['requested_model']} | {meta['mode']} | `{meta['run_id']}` "
            f"| {meta['threshold_id']} | {enrichment} | {meta['attributed_cost_usd']:.4f} |"
        )
    return lines


def policy_lines(policies: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return the Markdown table of the policies."""
    lines = [
        "| policy | matched | correct | precision | CP-LB | coverage |",
        "|---|---|---|---|---|---|",
    ]
    for policy in POLICIES:
        row = policies[policy]
        lines.append(
            f"| {policy} | {row['matched']} | {row['correct']} | {fmt(row['precision'])} "
            f"| {fmt(row['cp_lower_bound'])} | {fmt(row['coverage'])} |"
        )
    return lines


def agreement_lines(table: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return the Markdown agreement table."""
    lines = [
        "| primary decision | second-model passes agreeing | primary correct | lines |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {row['decision']} | {row['agreeing_passes']} | {row['primary_correct']} | {row['n']} |"
        for row in table
    )
    return lines


def language_lines(language: str, figures: Mapping[str, Any]) -> list[str]:
    """Return one language's Markdown section."""
    correct = figures["suggestion_correct"]
    labelled = figures["labelled"]
    return [
        f"## {language.upper()}",
        "",
        *runs_lines(figures["runs"]),
        "",
        f"Top-1 suggestion correct over {labelled} labelled dev lines: primary "
        f"{correct['primary']}/{labelled}, second {correct['second']}/{labelled} "
        f"(material type alone: {correct['second_type_only']}/{labelled}).",
        "",
        *policy_lines(figures["policies"]),
        "",
        *agreement_lines(figures["agreement"]),
        "",
    ]


def rule_lines(verdicts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return the Markdown section that applies the pre-registered E-02(d) rule."""
    lines = [
        "## The pre-registered E-02(d) rule",
        "",
        "DESIGN.md §7.2: a cross-model vote is adopted only if it adds at least "
        f"{RULE_MIN_ADDED_CORRECT} summed correct matches at equal precision.",
        "",
        "| policy | correct matches added (EN + FR) | precision not lower | adopted |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {policy} | {row['added_correct']} | {row['precision_not_lower']} | {row['adopted']} |"
        for policy, row in verdicts.items()
    )
    return [*lines, ""]


def render_markdown(report: Mapping[str, Any]) -> str:
    """Return the report as Markdown."""
    lines = [
        "# Cross-model agreement on dev (post-freeze analysis)",
        "",
        "Rendered by `eval/cross_model_dev.py` from `eval/cross_model_dev_votes.json`. Dev items "
        "only, after the lockbox was scored: no claim changes. The second model ran the B3 "
        "profile on the primary's prompt, without the verifier or the enrichment (G2 O26).",
        "",
        "Policies: `rescue_any`/`rescue_both` turn a primary `needs_review` line into a match "
        "when one/both second-model passes name the primary's top-1 row; `veto_any` keeps a "
        "primary match only when at least one second-model pass agrees.",
        "",
    ]
    for language, figures in report["languages"].items():
        lines.extend(language_lines(language, figures))
    lines.extend(rule_lines(report["e02d_rule"]))
    return "\n".join(lines)


def dump_json(document: Mapping[str, Any]) -> str:
    """Return deterministic JSON text."""
    return json.dumps(document, indent=JSON_INDENT, sort_keys=True, ensure_ascii=False) + "\n"


def run_pairs(values: Sequence[str]) -> dict[str, Path]:
    """Parse ``lang=path`` arguments."""
    pairs: dict[str, Path] = {}
    for value in values:
        language, separator, path = value.partition("=")
        if not separator or language not in LANGUAGES:
            raise AnalysisError(f"expected en=PATH or fr=PATH, got {value!r}")
        pairs[language] = Path(path)
    return pairs


def write_or_check(outputs: Mapping[Path, str], check: bool) -> int:
    """Write the files, or with ``check`` report whether they are current."""
    if not check:
        for path, text in outputs.items():
            path.write_text(text, encoding=ENCODING, newline="\n")
        return EXIT_OK
    stale = [
        path.as_posix()
        for path, text in outputs.items()
        if not path.exists() or path.read_text(encoding=ENCODING) != text
    ]
    for path_text in stale:
        print(f"stale: {path_text}", file=sys.stderr)
    return EXIT_DRIFT if stale else EXIT_OK


def parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    root = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = root.add_subparsers(dest="command", required=True)
    extract_command = commands.add_parser("extract", help="votes from two local run folders")
    extract_command.add_argument("--split", type=Path, required=True)
    extract_command.add_argument("--primary", action="append", default=[])
    extract_command.add_argument("--second", action="append", default=[])
    extract_command.add_argument("--output", type=Path, required=True)
    report_command = commands.add_parser("report", help="figures from a committed votes file")
    report_command.add_argument("--votes", type=Path, required=True)
    report_command.add_argument("--reference", type=Path, required=True)
    report_command.add_argument("--output", type=Path, required=True, help="path stem")
    report_command.add_argument("--check", action="store_true")
    return root


def dispatch(arguments: argparse.Namespace) -> int:
    """Run the chosen command."""
    if arguments.command == "extract":
        primary, second = run_pairs(arguments.primary), run_pairs(arguments.second)
        votes = extract(arguments.split, primary, second)
        return write_or_check({arguments.output: dump_json(votes)}, check=False)
    votes = json.loads(arguments.votes.read_text(encoding=ENCODING))
    report = build_report(votes, load_reference(arguments.reference))
    stem = arguments.output
    outputs = {
        stem.with_suffix(".json"): dump_json(report),
        stem.with_suffix(".md"): render_markdown(report),
    }
    return write_or_check(outputs, check=arguments.check)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the exit code."""
    arguments = parser().parse_args(argv)
    try:
        return dispatch(arguments)
    except (AnalysisError, OSError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
