"""Export the project page's data file from committed files only (no model call, no network).

Every figure on the page is computed or parsed here from files in the repository: the lockbox
and all-labelled metrics through ``eval/score.py``, cost and latency from the lockbox run
manifests, and the smoke result, policy, ledger, release list, traced cases, library sizes,
library tree with label usage and per-row pipeline classes from their own files. The output is
deterministic JSON (sorted keys, indent 2, LF, trailing newline).

Usage::

    python scripts/export_site_data.py [--output site/data.json] [--check]

Exit codes: 0 written (or ``--check`` found the file current), 1 ``--check`` found the file
missing or stale, 2 a source file cannot be read or parsed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "site" / "data.json"
SCHEMA_VERSION = 1
ENCODING = "utf-8"
CSV_ENCODING = "utf-8-sig"
JSON_INDENT = 2
NEWLINE = "\n"
EXIT_OK = 0
EXIT_STALE = 1
EXIT_ERROR = 2
PERCENT_BASE = 100
LANGS = ("en", "fr")
SCORER_MODULE = "oris_eval_score_for_site"

SCORER = "eval/score.py"
REFERENCE = "data/boq_dataset_matched_GT.csv"
SPLIT = "eval/split_v1.json"
SMOKE_RESULT = "eval/smoke_fr_v1_result.json"
SMOKE_GATE = "docs/gates/G6.md"
POLICY = "config/policy.yaml"
LEDGER = "eval/experiments.jsonl"
CHANGELOG = "CHANGELOG.md"
TRACED_CASES = "docs/traced-cases.md"
OUTPUTS = {lang: f"output/improved_output_{lang}.csv" for lang in LANGS}
LIBRARIES = {"global": "data/oris_materials_global.csv", "fr": "data/oris_materials_fr.csv"}
SECTION_SOURCE = "input/boq_dataset_input_en.csv"
LEAF_FIELDS = ("material_type", "material_usage", "material_subtype")
ITEM_FIELD = "Item No."
SECTION_TITLE_FIELD = "Short Description"
ITEM_SEPARATOR = "."
SUBMISSION = "runs/submission"
B3_RUNS = {"en": "20261008T023928Z-56f85fb8", "fr": "20261008T024244Z-de394c39"}
SESSION_RUNS = (
    "20261008T023626Z-ea37e3a1",
    "20261008T023750Z-be2dd33e",
    B3_RUNS["en"],
    B3_RUNS["fr"],
)
PIPELINE_LANG = "en"

DEV_LADDER: tuple[tuple[str, str, str], ...] = (
    ("B1 TF-IDF", "B1-dev", "B1-dev"),
    ("B2 one Haiku pass", "B2-dev-en", "B2-dev-fr"),
    ("B2, 25-word evidence cap", "R-10.9-evidence", "R-10.9-evidence"),
    ("B3, k = 2, selected T8", "B3-dev-sel", "B3-dev-sel"),
    ("B3 + E-08 verifier", "B3-dev-e08", "B3-dev-e08"),
    ("B3 + verifier + E-01 enrichment (shipped)", "B3-dev-e01", "B3-dev-e01"),
)
LADDER_NOTE = (
    "Rung labels follow the README dev-results table; each figure is the last ledger row with "
    "that id and language. B0 (rules only) has no ledger row and makes no matches."
)

HEADER_REASON = "HEADER"
CLASS_HEADER = "header"
RELEASE_HEADING = re.compile(r"^## (\S+) \((\d{4}-\d{2}-\d{2})\)$")
FIRST_SENTENCE = re.compile(r"^(.+?\.)(?:\s|$)")
CASE_HEADING = re.compile(r"^## (\d+)\. (.+) \((.+), `(.+)`\)$")
CASE_RUN = re.compile(r"^item \S+ .* in run (\S+) ")
CASE_INPUT = re.compile(r"^input: (['\"])(.*?)\1 \|")
CASE_DECISION = re.compile(r"^decision: (\w+), reason (\S+), rule (\w+) ")
CASE_BULLET = re.compile(r"^- \*\*(.+?):\*\* (.+)$")
TAKEAWAY_LABELS = ("What it shows", "The trade-off it shows", "What the reviewer gets", "Outcome")


class ExportError(Exception):
    """A source file is missing or does not have the expected shape."""


class Sources:
    """Reads repository files and remembers every path it read."""

    def __init__(self, root: Path) -> None:
        """Remember the repository root."""
        self.root = root
        self.used: set[str] = set()

    def path(self, relative: str) -> Path:
        """Return the absolute path of ``relative`` and record it as a source."""
        target = self.root / relative
        if not target.is_file():
            raise ExportError(f"missing source file: {relative}")
        self.used.add(relative)
        return target

    def text(self, relative: str) -> str:
        """Return a UTF-8 text file's contents."""
        return self.path(relative).read_text(encoding=ENCODING)

    def json(self, relative: str) -> Any:
        """Return a parsed JSON file."""
        return json.loads(self.text(relative))

    def jsonl(self, relative: str) -> list[dict[str, Any]]:
        """Return the objects of a JSON-lines file."""
        return [json.loads(line) for line in self.text(relative).splitlines() if line.strip()]

    def csv_rows(self, relative: str) -> list[dict[str, str]]:
        """Return a CSV file's rows as dicts keyed by header."""
        with self.path(relative).open(encoding=CSV_ENCODING, newline="") as handle:
            return list(csv.DictReader(handle))


def load_scorer(sources: Sources) -> ModuleType:
    """Load ``eval/score.py`` by file path, once, and record it as a source on every call."""
    scorer_path = sources.path(SCORER)
    if SCORER_MODULE in sys.modules:
        return sys.modules[SCORER_MODULE]
    spec = importlib.util.spec_from_file_location(SCORER_MODULE, scorer_path)
    if spec is None or spec.loader is None:
        raise ExportError(f"cannot load {SCORER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[SCORER_MODULE] = module
    spec.loader.exec_module(module)
    return module


def score_side(sources: Sources, side: str) -> dict[str, dict[str, Any]]:
    """Score both committed outputs on one split side with the repository's scorer."""
    scorer = load_scorer(sources)
    settings = scorer.Settings(
        jobs=[scorer.Job(label=lang, path=sources.path(OUTPUTS[lang])) for lang in LANGS],
        reference=sources.path(REFERENCE),
        key=None,
        strict=True,
        join=scorer.JOIN_KEY,
        split=sources.path(SPLIT),
        side=side,
        classes=None,
        json_path=None,
    )
    report: dict[str, Any] = scorer.run(settings)
    return {out["label"]: out for out in report["outputs"]}


def metric_block(out: dict[str, Any]) -> dict[str, Any]:
    """Pick the page's figures from one scorer output report."""
    return {
        "matched": out["precision"]["matched"],
        "correct": out["precision"]["correct"],
        "precision": out["precision"]["value"],
        "cp_lower_95": out["precision"]["cp_lower_95"],
        "coverage": out["coverage_labelled"]["value"],
        "labelled": out["counts"]["labelled"],
        "items": out["counts"]["items"],
        "false_not_a_material": out["false_not_a_material"],
        "per_level_matched": out["per_level_matched"],
        "per_level_labelled": out["per_level_labelled"],
        "hit_at_1": out["hit_at_1"]["value"],
        "hit_at_2": out["hit_at_2"]["value"],
        "decisions_item_rows": out["decision_shares"]["item_rows"],
    }


def scored_blocks(sources: Sources) -> dict[str, Any]:
    """Lockbox and all-labelled metrics per language."""
    lockbox = score_side(sources, "lockbox")
    everything = score_side(sources, "all")
    all_labelled = {
        lang: {
            **metric_block(everything[lang]),
            "decisions_all_rows": everything[lang]["decision_shares"]["all_rows"],
        }
        for lang in LANGS
    }
    return {
        "lockbox": {lang: metric_block(lockbox[lang]) for lang in LANGS},
        "all_labelled": all_labelled,
    }


def manifest(sources: Sources, run_id: str) -> dict[str, Any]:
    """Return one submission run's manifest."""
    payload: dict[str, Any] = sources.json(f"{SUBMISSION}/{run_id}/manifest.json")
    return payload


def operations_block(sources: Sources, lang: str) -> dict[str, Any]:
    """Cost per 100 lines and wall clock per routed line of one lockbox B3 run."""
    run = manifest(sources, B3_RUNS[lang])
    return {
        "run_id": run["run_id"],
        "profile": run["profile"],
        "model": run["requested_model"],
        "spend_usd": run["spend_usd"],
        "lines": run["line_count"],
        "routed_lines": run["n_routed"],
        "wall_clock_s": run["wall_clock_s"],
        "calls": run["call_count"],
        "cost_per_100_lines_usd": run["spend_usd"] / run["line_count"] * PERCENT_BASE,
        "latency_s_per_routed_line": run["wall_clock_s"] / run["n_routed"],
    }


def session_block(sources: Sources) -> dict[str, Any]:
    """Return the four runs of the lockbox session and their total spend."""
    runs = []
    for run_id in SESSION_RUNS:
        run = manifest(sources, run_id)
        lang = "fr" if run["input_path"].endswith("_fr.csv") else "en"
        runs.append(
            {
                "run_id": run_id,
                "profile": run["profile"],
                "lang": lang,
                "spend_usd": run["spend_usd"],
            }
        )
    total = sum(run["spend_usd"] for run in runs)
    return {"runs": runs, "total_spend_usd": total}


def smoke_block(sources: Sources) -> dict[str, Any]:
    """Return the A10 FR smoke result, as committed."""
    result: dict[str, Any] = sources.json(SMOKE_RESULT)
    sources.path(SMOKE_GATE)
    subsets = [{"subset": name, **counts} for name, counts in sorted(result["subsets"].items())]
    kept = (
        "verdict",
        "rule",
        "lines",
        "run_id",
        "spend_usd",
        "closed_world_violations",
        "false_not_a_material",
        "decoy_matches",
        "matched_non_material",
        "base_positives_correct",
    )
    return {**{name: result[name] for name in kept}, "subsets": subsets, "gate": SMOKE_GATE}


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def library_block(sources: Sources, relative: str) -> dict[str, Any]:
    """Row, type and usage counts of one library file, with its hash."""
    rows = sources.csv_rows(relative)
    types = {row["material_type"] for row in rows}
    usages = {(row["material_type"], row["material_usage"]) for row in rows}
    return {
        "path": relative,
        "rows": len(rows),
        "types": len(types),
        "usages": len(usages),
        "sha256": file_sha256(sources.path(relative)),
    }


def leaf_key(row: dict[str, str]) -> tuple[str, str, str]:
    """Return a row's (type, usage, subtype) triple."""
    material_type, usage, subtype = (row[field] for field in LEAF_FIELDS)
    return material_type, usage, subtype


def section_of(item: str) -> str:
    """Return the top-level section number of an item id, e.g. ``03.01.0020.`` -> ``3``."""
    return str(int(item.split(ITEM_SEPARATOR, maxsplit=1)[0]))


def heat_counts(labelled: list[dict[str, str]]) -> dict[str, dict[str, int]]:
    """Labelled lines per material type and top-level section."""
    heat: dict[str, Counter[str]] = {}
    for row in labelled:
        heat.setdefault(row["material_type"], Counter())[section_of(row[ITEM_FIELD])] += 1
    return {name: dict(sorted(cells.items())) for name, cells in sorted(heat.items())}


def library_tree_block(sources: Sources) -> dict[str, Any]:
    """Library rows with their labelled-line counts, FR rows, section names and the heat map."""
    labelled = [row for row in sources.csv_rows(REFERENCE) if row["material_type"]]
    usage = Counter(leaf_key(row) for row in labelled)
    sections = {
        row[ITEM_FIELD]: row[SECTION_TITLE_FIELD]
        for row in sources.csv_rows(SECTION_SOURCE)
        if ITEM_SEPARATOR not in row[ITEM_FIELD]
    }
    return {
        "lib_g": [
            [*leaf_key(row), usage[leaf_key(row)]] for row in sources.csv_rows(LIBRARIES["global"])
        ],
        "lib_f": [list(leaf_key(row)) for row in sources.csv_rows(LIBRARIES["fr"])],
        "l0": sections,
        "heat": heat_counts(labelled),
    }


def policy_rows(sources: Sources, libraries: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ``config/policy.yaml`` into one entry per (model, library)."""
    by_hash = {block["sha256"]: name for name, block in libraries.items()}
    config = yaml.safe_load(sources.text(POLICY))
    entries = []
    for model, per_library in sorted(config["policies"].items()):
        for sha, policy in sorted(per_library.items()):
            entries.append(
                {
                    "model": model,
                    "library": by_hash.get(sha, "unknown"),
                    "library_sha256": sha,
                    "threshold": policy["policy_id"],
                    "certified_by": policy["certified_by"],
                    "verifier": bool(policy.get("verifier_adopted", False)),
                    "enrichment": policy.get("enrichment"),
                }
            )
    return entries


def ledger_row(rows: list[dict[str, Any]], run_id: str, lang: str) -> dict[str, Any]:
    """Return the last ledger row with ``run_id`` and ``lang``, reduced to the ladder figures."""
    found = [row for row in rows if row["id"] == run_id and row["lang"] == lang]
    if not found:
        raise ExportError(f"{LEDGER}: no row {run_id!r} for {lang}")
    metrics = found[-1]["metrics"]
    return {
        "id": run_id,
        "correct": metrics["precision"]["correct"],
        "matched": metrics["precision"]["matched"],
        "precision": metrics["precision"]["value"],
        "cp_lower_95": metrics["precision"]["cp_lower_95"],
        "coverage": metrics["coverage_labelled"]["value"],
        "false_not_a_material": metrics["false_not_a_material"],
    }


def ladder_block(sources: Sources) -> dict[str, Any]:
    """Return the README dev-results rungs, with figures read from the experiment ledger."""
    rows = sources.jsonl(LEDGER)
    last = len(DEV_LADDER) - 1
    ladder = [
        {
            "rung": label,
            "shipped": index == last,
            "en": ledger_row(rows, en_id, "en"),
            "fr": ledger_row(rows, fr_id, "fr"),
        }
        for index, (label, en_id, fr_id) in enumerate(DEV_LADDER)
    ]
    return {"source": LEDGER, "note": LADDER_NOTE, "rows": ladder}


def sections(
    lines: list[str], heading: re.Pattern[str]
) -> Iterator[tuple[re.Match[str], list[str]]]:
    """Yield each heading match with the lines up to the next ``## `` heading."""
    current: re.Match[str] | None = None
    body: list[str] = []
    for line in [*lines, "## "]:
        if line.startswith("## "):
            if current is not None:
                yield current, body
            current, body = heading.match(line), []
        elif current is not None:
            body.append(line)


def first_sentence(body: list[str]) -> str:
    """Return the first sentence of a section body, without a list marker or a trailing colon."""
    text = next((line.strip() for line in body if line.strip()), "")
    text = text.removeprefix("- ")
    found = FIRST_SENTENCE.match(text)
    return (found.group(1) if found else text).rstrip(":")


def releases_block(sources: Sources) -> list[dict[str, str]]:
    """Version, date and one-line summary of every CHANGELOG entry, newest first."""
    lines = sources.text(CHANGELOG).splitlines()
    return [
        {"version": match.group(1), "date": match.group(2), "summary": first_sentence(body)}
        for match, body in sections(lines, RELEASE_HEADING)
    ]


def first_match(body: list[str], pattern: re.Pattern[str]) -> re.Match[str]:
    """Return the first line of ``body`` that ``pattern`` matches."""
    for line in body:
        found = pattern.match(line)
        if found:
            return found
    raise ExportError(f"{TRACED_CASES}: no line matches {pattern.pattern!r}")


def case_entry(heading: re.Match[str], body: list[str]) -> dict[str, Any]:
    """One traced case from its heading and section body."""
    decision = first_match(body, CASE_DECISION)
    bullets: dict[str, str] = {
        found.group(1): found.group(2) for line in body if (found := CASE_BULLET.match(line))
    }
    label = next((name for name in TAKEAWAY_LABELS if name in bullets), None)
    return {
        "takeaway_label": label,
        "takeaway": bullets[label] if label else "",
        "number": int(heading.group(1)),
        "demonstrates": heading.group(2),
        "context": heading.group(3),
        "item": heading.group(4),
        "run_id": first_match(body, CASE_RUN).group(1),
        "input": first_match(body, CASE_INPUT).group(2),
        "decision": decision.group(1),
        "reason": decision.group(2),
        "rule": decision.group(3),
    }


def cases_block(sources: Sources) -> list[dict[str, Any]]:
    """Return the four cases of ``docs/traced-cases.md``."""
    lines = sources.text(TRACED_CASES).splitlines()
    return [case_entry(match, body) for match, body in sections(lines, CASE_HEADING)]


def row_class(row: dict[str, str]) -> str:
    """Header, or the row's decision."""
    return CLASS_HEADER if row["reason"] == HEADER_REASON else row["decision"]


def batch_indices(audit: list[dict[str, Any]], field: str, slot: int) -> dict[str, int]:
    """Return an index for each distinct call id at ``audit[field][slot]``, in file order."""
    order: dict[str, int] = {}
    for record in audit:
        calls = record[field]
        if len(calls) > slot and calls[slot] not in order:
            order[calls[slot]] = len(order)
    return order


def batch_of(record: dict[str, Any], field: str, slot: int, order: dict[str, int]) -> int | None:
    """Return the batch number of one audit record's call at ``slot``, or None."""
    calls = record[field]
    return order[calls[slot]] if len(calls) > slot else None


def split_sides(sources: Sources) -> dict[str, str]:
    """Item id -> split side."""
    split = sources.json(SPLIT)
    return {
        **dict.fromkeys(split["item_ids_dev"], "dev"),
        **dict.fromkeys(split["item_ids_lockbox"], "lockbox"),
    }


def pipeline_rows(sources: Sources, audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per output row: class, split side and the batches that carried it."""
    output = sources.csv_rows(OUTPUTS[PIPELINE_LANG])
    if len(output) != len(audit):
        raise ExportError("output and audit row counts differ")
    sides = split_sides(sources)
    first, verifier = (
        batch_indices(audit, "call_ids", 0),
        batch_indices(audit, "verifier_call_ids", 0),
    )
    return [
        {
            "position": record["position"],
            "item": row["Item No."],
            "class": row_class(row),
            "side": sides.get(row["Item No."]),
            "batch": batch_of(record, "call_ids", 0, first),
            "verifier_batch": batch_of(record, "verifier_call_ids", 0, verifier),
        }
        for row, record in zip(output, audit, strict=True)
    ]


def pipeline_block(sources: Sources) -> dict[str, Any]:
    """Per-row classes of the EN lockbox replay output, with batch counts from its run."""
    run_dir = f"{SUBMISSION}/{B3_RUNS[PIPELINE_LANG]}"
    audit = sorted(sources.jsonl(f"{run_dir}/audit.jsonl"), key=lambda record: record["position"])
    run = manifest(sources, B3_RUNS[PIPELINE_LANG])
    rows = pipeline_rows(sources, audit)
    passes = [len(batch_indices(audit, "call_ids", slot)) for slot in range(run["passes_k"])]
    batches = {
        "batch_size": run["settings_effective"]["batch_size"],
        "pass_calls": passes,
        "verifier_calls": len(batch_indices(audit, "verifier_call_ids", 0)),
        "total_calls": run["call_count"],
    }
    counts = Counter(row["class"] for row in rows)
    return {
        "run_id": run["run_id"],
        "lang": PIPELINE_LANG,
        "rows": rows,
        "counts": dict(sorted(counts.items())),
        "batches": batches,
    }


def build_payload(root: Path = REPO_ROOT) -> dict[str, Any]:
    """Build the whole data document from the repository at ``root``."""
    sources = Sources(root)
    libraries = {name: library_block(sources, path) for name, path in LIBRARIES.items()}
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        **scored_blocks(sources),
        "operations": {lang: operations_block(sources, lang) for lang in LANGS},
        "lockbox_session": session_block(sources),
        "smoke_fr": smoke_block(sources),
        "libraries": libraries,
        "library_tree": library_tree_block(sources),
        "policies": policy_rows(sources, libraries),
        "dev_ladder": ladder_block(sources),
        "releases": releases_block(sources),
        "traced_cases": cases_block(sources),
        "pipeline_en": pipeline_block(sources),
    }
    payload["generated_from"] = sorted(sources.used)
    return payload


def render(payload: dict[str, Any]) -> bytes:
    """Serialise the payload deterministically."""
    text = json.dumps(payload, sort_keys=True, indent=JSON_INDENT, ensure_ascii=False)
    return (text + NEWLINE).encode(ENCODING)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    return parser


def check(target: Path, fresh: bytes) -> int:
    """Compare the file on disk with a fresh export."""
    if not target.is_file():
        print(f"error: {target} is missing; run scripts/export_site_data.py", file=sys.stderr)
        return EXIT_STALE
    if target.read_bytes() != fresh:
        print(f"error: {target} is stale; run scripts/export_site_data.py", file=sys.stderr)
        return EXIT_STALE
    print(f"{target} is current")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Export (or check) the project page data; return the exit code."""
    args = build_parser().parse_args(argv)
    try:
        fresh = render(build_payload())
    except (
        ExportError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        yaml.YAMLError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    if args.check:
        return check(args.output, fresh)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(fresh)
    print(f"wrote {args.output}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
