r"""Build the stratified 40-item dev slice for the first live B2 runs (``eval/slice_v1.json``).

The same item ids are used for EN and FR, so twins stay together. Ids come from
``item_ids_dev`` of the frozen split only; no lockbox id can be drawn. There is no RNG: strata
are L1 section x reference class, the 40 places are allocated proportionally with the largest
remainder method, and within a stratum items are ranked by ``sha256(SALT + item_no)``.

Run once, before the first B2 call::

    python eval/make_slice.py --gt data/boq_dataset_matched_GT.csv \\
        --classes eval/annotations/blank_line_classes.csv --split eval/split_v1.json \\
        --out eval/slice_v1.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

ENCODING = "utf-8"
SLICE_SIZE = 40
SALT = "oris-material-matcher/slice_v1"
SECTION_LENGTH = 5
KEY_COLUMN = "Item No."
TYPE_COLUMN = "material_type"
SUBTYPE_COLUMN = "material_subtype"
CLASS_LABELLED_SUBTYPE = "labelled_subtype"
CLASS_LABELLED_BLANK_SUBTYPE = "labelled_blank_subtype"
CLASS_SERVICE = "blank_gt_service"
CLASS_NO_EQUIVALENT = "no_equivalent_material"
CLASS_AMBIGUOUS = "blank_gt_ambiguous"
ANNOTATION_CLASSES = {
    "service": CLASS_SERVICE,
    "material_no_equivalent": CLASS_NO_EQUIVALENT,
    "ambiguous": CLASS_AMBIGUOUS,
}
RULE = (
    f"{SLICE_SIZE} dev items (item_ids_dev of eval/split_v1.json only), the same ids for EN and "
    "FR. Strata = L1 section (first 5 characters of Item No.) x reference class: "
    f"{CLASS_LABELLED_SUBTYPE} (GT triple with a subtype), {CLASS_LABELLED_BLANK_SUBTYPE} "
    f"(GT triple with a blank subtype), {CLASS_SERVICE} and {CLASS_NO_EQUIVALENT} (blank GT, "
    "class from eval/annotations/blank_line_classes.csv). Places are allocated proportionally "
    "to stratum size by the largest remainder method (ties: larger stratum first, then "
    "section and class in ascending order). Within a stratum, items are ranked by "
    "sha256(salt + item_no) ascending and the first `allocated` are taken. ids_sha256 is the "
    "SHA-256 of the sorted ids joined by '\\n' with a trailing '\\n', UTF-8."
)

Stratum = tuple[str, str]


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV into dicts of raw strings."""
    with path.open(encoding=ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def reference_class(row: dict[str, str], annotations: dict[str, str]) -> str:
    """Return the reference class of one dev item."""
    if row[TYPE_COLUMN]:
        return CLASS_LABELLED_SUBTYPE if row[SUBTYPE_COLUMN] else CLASS_LABELLED_BLANK_SUBTYPE
    item_no = row[KEY_COLUMN]
    if item_no not in annotations:
        raise ValueError(f"blank-GT dev item {item_no} has no class in the annotations")
    return ANNOTATION_CLASSES[annotations[item_no]]


def dev_strata(gt: Path, classes: Path, split: Path) -> dict[Stratum, list[str]]:
    """Group the split's dev items by (L1 section, reference class)."""
    dev_ids = set(json.loads(split.read_text(encoding=ENCODING))["item_ids_dev"])
    annotations = {row["item_no"]: row["class"] for row in read_csv(classes)}
    strata: dict[Stratum, list[str]] = {}
    for row in read_csv(gt):
        item_no = row[KEY_COLUMN]
        if item_no not in dev_ids:
            continue
        stratum = (item_no[:SECTION_LENGTH], reference_class(row, annotations))
        strata.setdefault(stratum, []).append(item_no)
    return strata


def allocate(populations: dict[Stratum, int], size: int) -> dict[Stratum, int]:
    """Largest-remainder proportional allocation of ``size`` places, deterministic ties."""
    total = sum(populations.values())
    exact = {stratum: Fraction(size * count, total) for stratum, count in populations.items()}
    allocation = {stratum: int(share) for stratum, share in exact.items()}
    order = sorted(
        populations,
        key=lambda stratum: (
            -(exact[stratum] - allocation[stratum]),
            -populations[stratum],
            stratum,
        ),
    )
    for stratum in order[: size - sum(allocation.values())]:
        allocation[stratum] += 1
    return allocation


def rank_key(item_no: str) -> str:
    """Rank of an item within its stratum: ``sha256(SALT + item_no)`` hex digest."""
    return hashlib.sha256((SALT + item_no).encode(ENCODING)).hexdigest()


def ids_sha256(item_ids: list[str]) -> str:
    """Return the SHA-256 of the canonical id list (sorted, newline-joined, trailing newline)."""
    canonical = "\n".join(sorted(item_ids)) + "\n"
    return hashlib.sha256(canonical.encode(ENCODING)).hexdigest()


def draw(strata: dict[Stratum, list[str]], allocation: dict[Stratum, int]) -> list[dict[str, str]]:
    """Take the first ``allocated`` items of each stratum by rank."""
    items: list[dict[str, str]] = []
    for stratum in sorted(strata):
        chosen = sorted(strata[stratum], key=rank_key)[: allocation[stratum]]
        items += [{"item_no": item, "section": stratum[0], "class": stratum[1]} for item in chosen]
    return sorted(items, key=lambda entry: entry["item_no"])


def build_slice(gt: Path, classes: Path, split: Path) -> dict[str, Any]:
    """Build the slice payload."""
    strata = dev_strata(gt, classes, split)
    allocation = allocate({stratum: len(ids) for stratum, ids in strata.items()}, SLICE_SIZE)
    items = draw(strata, allocation)
    item_ids = [entry["item_no"] for entry in items]
    return {
        "version": "slice_v1",
        "rule": RULE,
        "salt": SALT,
        "split_sha256": hashlib.sha256(split.read_bytes()).hexdigest(),
        "population": sum(len(ids) for ids in strata.values()),
        "size": SLICE_SIZE,
        "strata": [
            {
                "section": s[0],
                "class": s[1],
                "population": len(strata[s]),
                "allocated": allocation[s],
            }
            for s in sorted(strata)
        ],
        "items": items,
        "item_ids": item_ids,
        "ids_sha256": ids_sha256(item_ids),
    }


def render(payload: dict[str, Any]) -> str:
    """Serialise the payload exactly as it is written to disk."""
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def composition(payload: dict[str, Any]) -> str:
    """Format a plain-text composition table of the slice."""
    lines = [f"{'section':<8} {'class':<24} {'dev':>4} {'slice':>5}"]
    lines += [
        f"{s['section']:<8} {s['class']:<24} {s['population']:>4} {s['allocated']:>5}"
        for s in payload["strata"]
    ]
    lines.append(f"{'total':<33} {payload['population']:>4} {payload['size']:>5}")
    return "\n".join(lines)


def main() -> None:
    """Write the slice file and print its composition and id SHA-256."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gt", type=Path, required=True)
    parser.add_argument("--classes", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = build_slice(args.gt, args.classes, args.split)
    args.out.write_text(render(payload), encoding=ENCODING, newline="\n")
    print(composition(payload))
    print(f"ids_sha256={payload['ids_sha256']}")


if __name__ == "__main__":
    main()
