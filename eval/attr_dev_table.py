"""Recompute the A2 attribute dev table under the §9.3 / A35 definitions (eval side only).

For every labelled dev line (``item_ids_dev`` of the frozen split), in English and in French,
the ground-truth global-library row is compared with the line from the line side and classified
``agree`` / ``no_evidence`` / ``conflict``. The script also counts, per line, how many library
rows are not in conflict with it.

Pre-registration: the ground-truth file is read row by row and only dev rows are kept; lockbox
rows are parsed but discarded and their labels are never used. No model is called.

Default paths are anchored to the repository, so the script runs from any directory:

    uv run python eval/attr_dev_table.py
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from oris_matcher.domain.attributes import (
    HARD_FAMILIES,
    Attributes,
    AttrResult,
    apply_implicit_zero,
    compare,
    extract,
)

ENCODING = "utf-8"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GT = REPO_ROOT / "data" / "boq_dataset_matched_GT.csv"
DEFAULT_SPLIT = REPO_ROOT / "eval" / "split_v1.json"
DEFAULT_LIBRARY = REPO_ROOT / "data" / "oris_materials_global.csv"
DEFAULT_INPUTS = {
    "EN": REPO_ROOT / "input" / "boq_dataset_input_en.csv",
    "FR": REPO_ROOT / "input" / "boq_dataset_input_fr.csv",
}
LIBRARY_ID = "global"
LIBRARY_FIELDS = ("material_type", "material_usage", "material_subtype")
ITEM_COLUMN = "Item No."
SHORT_COLUMN = "Short Description"
LONG_COLUMN = "Long Description"
RESULT_ORDER = (AttrResult.AGREE, AttrResult.NO_EVIDENCE, AttrResult.CONFLICT)

Triple = tuple[str, str, str]


@dataclass(frozen=True)
class LanguageResult:
    """The dev table row of one language.

    Attributes:
        counts: Number of labelled dev lines per comparison result for the GT row.
        candidates: Per line, the number of library rows not in conflict with it.
        conflicts: ``(item_no, families)`` for every GT row in conflict.
        missing: Item numbers whose GT triple is not a library row.

    """

    counts: Counter[AttrResult]
    candidates: list[int]
    conflicts: list[tuple[str, tuple[str, ...]]]
    missing: list[str]


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into dict rows."""
    with path.open(encoding=ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def load_dev_ids(split_path: Path) -> list[str]:
    """Return the dev item ids, checking that none of them is also a lockbox id."""
    with split_path.open(encoding=ENCODING) as handle:
        split = json.load(handle)
    dev: list[str] = split["item_ids_dev"]
    overlap = set(dev) & set(split["item_ids_lockbox"])
    if overlap:
        raise ValueError(f"split has items on both sides: {sorted(overlap)}")
    return dev


def _missing_error(kind: str, missing: set[str]) -> ValueError:
    """Build the error raised when dev items are absent from an input file."""
    return ValueError(f"{len(missing)} dev item(s) have no {kind}: {sorted(missing)}")


def load_dev_labels(gt_path: Path, dev_ids: list[str]) -> dict[str, Triple]:
    """Return item -> GT triple for the labelled dev items; lockbox rows are discarded.

    Raises:
        ValueError: When a dev id has no row in the ground-truth file.

    """
    wanted = set(dev_ids)
    dev_rows = [row for row in read_rows(gt_path) if row[ITEM_COLUMN] in wanted]
    missing = wanted - {row[ITEM_COLUMN] for row in dev_rows}
    if missing:
        raise _missing_error("ground-truth row", missing)
    return {
        row[ITEM_COLUMN]: (row["material_type"], row["material_usage"], row["material_subtype"])
        for row in dev_rows
        if row["material_type"]
    }


def load_library(library_path: Path) -> dict[Triple, Attributes]:
    """Extract every library row and apply the implicit-zero post-pass per type+usage."""
    groups: dict[tuple[str, str], list[Triple]] = defaultdict(list)
    for row in read_rows(library_path):
        triple = (row["material_type"], row["material_usage"], row["material_subtype"])
        groups[triple[:2]].append(triple)
    extracted = {
        parent: [extract(" ".join(triple)) for triple in triples]
        for parent, triples in groups.items()
    }
    library: dict[Triple, Attributes] = {}
    for parent, attributes in apply_implicit_zero(extracted, library=LIBRARY_ID).items():
        library.update(zip(groups[parent], attributes, strict=True))
    return library


def load_line_texts(input_path: Path, items: set[str]) -> dict[str, str]:
    """Return item -> ``short + " " + long`` for the requested items.

    Raises:
        ValueError: When a requested item has no line in the input file.

    """
    texts = {
        row[ITEM_COLUMN]: f"{row[SHORT_COLUMN]} {row[LONG_COLUMN]}"
        for row in read_rows(input_path)
        if row[ITEM_COLUMN] in items
    }
    missing = items - set(texts)
    if missing:
        raise _missing_error(f"line text in {input_path.name}", missing)
    return texts


def is_stated(value: object) -> bool:
    """Tell whether a family value is present: not None and not an empty collection."""
    return value is not None and value not in ((), frozenset())


def conflicting_families(line: Attributes, row: Attributes) -> tuple[str, ...]:
    """Name every hard family stated on both sides with incompatible values."""
    names: list[str] = []
    for family in HARD_FAMILIES:
        line_value = getattr(line, family.field)
        row_value = getattr(row, family.field)
        both = is_stated(line_value) and is_stated(row_value)
        if both and not family.compatible(line_value, row_value):
            names.append(family.field)
    return tuple(names)


def evaluate_language(
    texts: dict[str, str], labels: dict[str, Triple], library: dict[Triple, Attributes]
) -> LanguageResult:
    """Classify each labelled dev line's GT row and count its non-conflicting rows."""
    result = LanguageResult(Counter(), [], [], [])
    for item_no in sorted(labels):
        line = extract(texts[item_no])
        gt_row = library.get(labels[item_no])
        if gt_row is None:
            result.missing.append(item_no)
            continue
        verdict = compare(line, gt_row)
        result.counts[verdict] += 1
        if verdict is AttrResult.CONFLICT:
            result.conflicts.append((item_no, conflicting_families(line, gt_row)))
        result.candidates.append(
            sum(compare(line, row) is not AttrResult.CONFLICT for row in library.values())
        )
    return result


def render_table(results: dict[str, LanguageResult], library_size: int) -> str:
    """Render the dev table as markdown."""
    lines = [
        "| Language | lines | agree | no_evidence | conflict | median non-conflicting rows "
        f"(of {library_size}) | min non-conflicting rows |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for language, result in results.items():
        counts = [str(result.counts[verdict]) for verdict in RESULT_ORDER]
        lines.append(
            f"| {language} | {sum(result.counts.values())} | {' | '.join(counts)} | "
            f"{statistics.median(result.candidates):g} | {min(result.candidates)} |"
        )
    return "\n".join(lines)


def render_findings(results: dict[str, LanguageResult]) -> str:
    """Render the GT conflicts and missing GT rows, or say there are none."""
    findings = [
        f"- {language} {item_no}: conflict on {', '.join(families)}"
        for language, result in results.items()
        for item_no, families in result.conflicts
    ]
    findings += [
        f"- {language} {item_no}: GT triple not found in the library"
        for language, result in results.items()
        for item_no in result.missing
    ]
    return "\n".join(findings) if findings else "No GT row is in conflict."


def main() -> None:
    """Print the recomputed A2 dev table and any GT conflicts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gt", type=Path, default=DEFAULT_GT)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    args = parser.parse_args()

    labels = load_dev_labels(args.gt, load_dev_ids(args.split))
    library = load_library(args.library)
    results = {
        language: evaluate_language(load_line_texts(path, set(labels)), labels, library)
        for language, path in DEFAULT_INPUTS.items()
    }
    print(render_table(results, len(library)))
    print()
    print(render_findings(results))


if __name__ == "__main__":
    main()
