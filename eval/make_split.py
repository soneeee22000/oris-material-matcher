"""Build the frozen, section-based dev/lockbox split (``eval/split_v1.json``).

The split is keyed by ``Item No.`` so the English and French twins of an item
always land on the same side. Whole L1 sections are held out, except section
03.03 (all asphalt lines), which is split by alternating labelled lines so the
rule-heavy asphalt family exists on both sides.

Run once, before any model call:

    python eval/make_split.py --gt data/boq_dataset_matched_GT.csv --out eval/split_v1.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

ITEM_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{4}\.$")
LOCKBOX_SECTIONS = (
    "01.03",
    "03.01",
    "04.02",
    "04.04",
    "05.02",
    "06.01",
    "08.01",
    "08.03",
    "09.01",
)
ALTERNATING_SECTION = "03.03"
RULE = (
    "Lockbox = all L2 items in L1 sections "
    + ", ".join(LOCKBOX_SECTIONS)
    + f", plus odd-indexed (0-based) labelled items of {ALTERNATING_SECTION}; "
    "everything else is dev. Unlabelled items of the alternating section stay in dev. "
    "Header rows are rule-decided and belong to neither side."
)


def load_items(gt_path: Path) -> list[dict[str, str]]:
    """Return the L2 item rows of the reference file, in file order."""
    with gt_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [row for row in rows if ITEM_PATTERN.match(row["Item No."])]


def assign(items: list[dict[str, str]]) -> tuple[list[str], list[str]]:
    """Split item numbers into (dev, lockbox) according to ``RULE``."""
    dev: list[str] = []
    lockbox: list[str] = []
    alternating_index = 0
    for row in items:
        item_no = row["Item No."]
        section = item_no[:5]
        labelled = row["material_type"] != ""
        if section in LOCKBOX_SECTIONS:
            lockbox.append(item_no)
        elif section == ALTERNATING_SECTION and labelled:
            target = lockbox if alternating_index % 2 == 1 else dev
            target.append(item_no)
            alternating_index += 1
        else:
            dev.append(item_no)
    return dev, lockbox


def main() -> None:
    """Write the split file and print its SHA-256."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gt", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    dev, lockbox = assign(load_items(args.gt))
    payload = {
        "version": "split_v1",
        "rule": RULE,
        "item_ids_dev": dev,
        "item_ids_lockbox": lockbox,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    args.out.write_text(text, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    print(f"dev={len(dev)} lockbox={len(lockbox)} sha256={digest}")


if __name__ == "__main__":
    main()
