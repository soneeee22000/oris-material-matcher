r"""The A10 smoke verdict for a library without labels (DESIGN.md §6, A10, A70).

A smoke set is a small builder-labelled BoQ frozen under ``<smoke>/freeze.json`` (SHA-256 of
``input.csv``, ``labels.csv`` and the library). Given a recorded run of that input on that
library, this script applies the pre-registered rule mechanically:

- 0 closed-world violations (a matched triple that is not a library row);
- 0 false ``not_a_material`` (a material line sent there);
- 0 decoy matches (a material line labelled ``no_match`` that was matched);
- at least 9 of the 18 base positive lines (``base_exact`` + ``base_fr_only``) matched to their
  labelled row.

A decoy is a material line labelled ``no_match`` (§6: "no-match decoys"); a matched non-material
line is reported apart and is not part of the rule.

It refuses (exit 4) a run of another input, library or configuration (``run_configuration`` in
``freeze.json``: manifest values by dotted path), labels whose bytes changed after the freeze, a
duplicate item number, an output line without a label (or the reverse), and a label set without
exactly 18 base positives. ``output.csv`` itself is not hashed by the manifest, so the run folder
is trusted as written; commit it with the result. It reads only the smoke folder, the library and
the run folder; it never calls a model.

Usage::

    python eval/smoke_a10.py --smoke eval/smoke/fr_v1 --library data/oris_materials_fr.csv \\
        --run runs/<run_id> --output eval/smoke_fr_v1_result

Exit codes: 0 written (pass or fail is in the file), 2 bad input, 4 refused.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ENCODING = "utf-8"
READ_ENCODING = "utf-8-sig"
LABELS_FILE = "labels.csv"
INPUT_FILE = "input.csv"
FREEZE_FILE = "freeze.json"
OUTPUT_FILE = "output.csv"
AUDIT_FILE = "audit.jsonl"
MANIFEST_FILE = "manifest.json"
ITEM_COLUMN = "Item No."
LABEL_COLUMNS = ("material_type", "material_usage", "material_subtype")
MATCHED = "matched"
NOT_A_MATERIAL = "not_a_material"
EXPECT_MATCH = "match"
BASE_SUBSETS = ("base_exact", "base_fr_only")
BASE_POSITIVES_REQUIRED = 9
BASE_POSITIVES_EXPECTED = 18
TRUE = "true"
AGREE = "agree"
PASS = "pass"
FAIL = "fail"
JSON_INDENT = 2
CONCRETE_KEY = "signal_b_agree_on_a_concrete_positive"
CONFIGURATION_KEY = "run_configuration"
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_REFUSED = 4

Triple = tuple[str, str, str]


class RefusedError(Exception):
    """The run or the labels are not the frozen smoke set."""


def sha256(path: Path) -> str:
    """Return a file's SHA-256 hex digest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV as dicts, BOM tolerated."""
    with path.open(encoding=READ_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def triple(row: Mapping[str, str]) -> Triple:
    """Return a row's (type, usage, subtype)."""
    return (row["material_type"], row["material_usage"], row["material_subtype"])


def check_freeze(smoke: Path, library: Path, manifest: Mapping[str, Any]) -> None:
    """Refuse labels edited after the freeze, and a run of another input or library.

    Args:
        smoke: The smoke folder.
        library: The library file.
        manifest: The run's manifest.

    Raises:
        RefusedError: A hash differs from ``freeze.json``.

    """
    frozen = json.loads((smoke / FREEZE_FILE).read_text(encoding=ENCODING))
    actual = {
        "labels_sha256": sha256(smoke / LABELS_FILE),
        "input_sha256": sha256(smoke / INPUT_FILE),
        "library_sha256": sha256(library),
    }
    for key, value in actual.items():
        if frozen[key] != value:
            raise RefusedError(f"{key.removesuffix('_sha256')} differs from {FREEZE_FILE}")
    for key in ("input_sha256", "library_sha256"):
        if manifest.get(key) != frozen[key]:
            raise RefusedError(f"the run's {key.removesuffix('_sha256')} is not the frozen one")
    for path, expected in frozen[CONFIGURATION_KEY].items():
        if manifest_value(manifest, path) != expected:
            raise RefusedError(f"the run's {path} is not the certified {expected!r}")


def manifest_value(manifest: Mapping[str, Any], path: str) -> Any:
    """Return a manifest value by dotted path, or ``None`` when any part is missing."""
    value: Any = manifest
    for part in path.split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
    return value


def unique_items(items: Sequence[str], where: str) -> set[str]:
    """Return the item numbers as a set, refusing any duplicate."""
    seen: set[str] = set()
    for item in items:
        if item in seen:
            raise RefusedError(f"duplicate item {item!r} in {where}")
        seen.add(item)
    return seen


def check_coverage(
    labels: Sequence[Mapping[str, str]], output_rows: Sequence[Mapping[str, str]]
) -> None:
    """Refuse unless each output line has exactly one label and there are 18 base positives."""
    labelled = unique_items([row["item_no"] for row in labels], LABELS_FILE)
    produced = unique_items([row[ITEM_COLUMN] for row in output_rows], OUTPUT_FILE)
    if labelled != produced:
        missing = sorted(produced - labelled) or sorted(labelled - produced)
        raise RefusedError(f"output and labels differ: {missing[:5]} not labelled or not produced")
    base_n = sum(1 for r in labels if r["subset"] in BASE_SUBSETS and r["expected"] == EXPECT_MATCH)
    if base_n != BASE_POSITIVES_EXPECTED:
        raise RefusedError(
            f"labels hold {base_n} base positives, the rule needs {BASE_POSITIVES_EXPECTED}"
        )


def concrete_agrees(labels: Sequence[Mapping[str, str]], run: Path) -> bool:
    """Tell whether signal b is ``agree`` on at least one concrete expected-match line (A35)."""
    concrete = {
        row["item_no"]
        for row in labels
        if row["is_concrete"] == TRUE and row["expected"] == EXPECT_MATCH
    }
    text = (run / AUDIT_FILE).read_text(encoding=ENCODING)
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    return any(
        r.get("item_no") in concrete and (r.get("signals") or {}).get("b") == AGREE for r in records
    )


def counts(
    labels: Sequence[Mapping[str, str]], output: Mapping[str, Mapping[str, str]], rows: set[Triple]
) -> dict[str, Any]:
    """Count the four conditions and the per-subset k/n."""
    violations = false_nm = decoys = base_ok = matched_nm = 0
    subsets: dict[str, dict[str, int]] = {}
    for label in labels:
        line = output[label["item_no"]]
        decision, got = line["decision"], triple(line)
        material = label["is_material"] == TRUE
        positive = label["expected"] == EXPECT_MATCH
        right = decision == MATCHED and got == triple(label)
        violations += decision == MATCHED and got not in rows
        false_nm += material and decision == NOT_A_MATERIAL
        decoys += material and not positive and decision == MATCHED
        matched_nm += not material and decision == MATCHED
        base_ok += label["subset"] in BASE_SUBSETS and positive and right
        tally = subsets.setdefault(label["subset"], dict.fromkeys(_TALLY_KEYS, 0))
        tally["positive"] += positive
        tally["correct"] += positive and right
        tally["matched"] += decision == MATCHED
        tally["wrong"] += decision == MATCHED and not right
        tally["false_skip"] += material and decision == NOT_A_MATERIAL
    return {
        "closed_world_violations": violations,
        "false_not_a_material": false_nm,
        "decoy_matches": decoys,
        "matched_non_material": matched_nm,
        "base_correct": base_ok,
        "subsets": subsets,
    }


_TALLY_KEYS = ("positive", "correct", "matched", "wrong", "false_skip")


def evaluate(smoke: Path, library: Path, run: Path) -> dict[str, Any]:
    """Score a recorded run of the frozen smoke set and apply the A10 rule.

    Args:
        smoke: The smoke folder (``input.csv``, ``labels.csv``, ``freeze.json``).
        library: The library the run used.
        run: The run folder (``output.csv``, ``audit.jsonl``, ``manifest.json``).

    Returns:
        The result, with ``verdict`` ``pass`` or ``fail``.

    Raises:
        RefusedError: The run or the labels are not the frozen ones.

    """
    manifest = json.loads((run / MANIFEST_FILE).read_text(encoding=ENCODING))
    check_freeze(smoke, library, manifest)
    labels = read_rows(smoke / LABELS_FILE)
    output_rows = read_rows(run / OUTPUT_FILE)
    check_coverage(labels, output_rows)
    output = {row[ITEM_COLUMN]: row for row in output_rows}
    rows = {triple(row) for row in read_rows(library)}
    found = counts(labels, output, rows)
    passed = (
        found["closed_world_violations"] == 0
        and found["false_not_a_material"] == 0
        and found["decoy_matches"] == 0
        and found["base_correct"] >= BASE_POSITIVES_REQUIRED
    )
    return {
        "run_id": manifest.get("run_id"),
        "spend_usd": manifest.get("spend_usd"),
        "lines": len(labels),
        "closed_world_violations": found["closed_world_violations"],
        "false_not_a_material": found["false_not_a_material"],
        "decoy_matches": found["decoy_matches"],
        "matched_non_material": found["matched_non_material"],
        "base_positives_correct": {"k": found["base_correct"], "n": BASE_POSITIVES_EXPECTED},
        "base_positives_expected": BASE_POSITIVES_EXPECTED,
        "subsets": found["subsets"],
        CONCRETE_KEY: concrete_agrees(labels, run),
        "rule": "0 closed-world, 0 false not_a_material, 0 decoy matches, >= 9/18 base positives",
        "verdict": PASS if passed else FAIL,
    }


def render(result: Mapping[str, Any]) -> str:
    """Render the result as a short Markdown report."""
    base = result["base_positives_correct"]
    lines = [
        "# A10 smoke result",
        "",
        f"Run `{result['run_id']}`, {result['lines']} lines, spend ${result['spend_usd']}.",
        "",
        f"- verdict: **{result['verdict']}** ({result['rule']})",
        f"- closed-world violations: {result['closed_world_violations']}",
        f"- false not_a_material: {result['false_not_a_material']}",
        f"- decoy matches: {result['decoy_matches']}",
        f"- matched non-material (reported, not in the rule): {result['matched_non_material']}",
        f"- base positives correct: {base['k']}/{base['n']}",
        f"- signal b agrees on a concrete positive: {result[CONCRETE_KEY]}",
        "",
        "| subset | correct / positive | wrong / matched | false skips |",
        "|---|---|---|---|",
    ]
    for name, tally in sorted(result["subsets"].items()):
        lines.append(
            f"| {name} | {tally['correct']}/{tally['positive']} | "
            f"{tally['wrong']}/{tally['matched']} | {tally['false_skip']} |"
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Score the run, write ``<output>.json`` and ``<output>.md``, and return the exit code."""
    args = build_parser().parse_args(argv)
    try:
        result = evaluate(args.smoke, args.library, args.run)
    except RefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (OSError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    text = json.dumps(result, sort_keys=True, indent=JSON_INDENT, ensure_ascii=False) + "\n"
    args.output.with_suffix(".json").write_bytes(text.encode(ENCODING))
    args.output.with_suffix(".md").write_bytes(render(result).encode(ENCODING))
    print(f"verdict: {result['verdict']}; wrote {args.output.with_suffix('.json')}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
