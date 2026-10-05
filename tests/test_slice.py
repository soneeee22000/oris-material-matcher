import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAKE_SLICE_PATH = ROOT / "eval" / "make_slice.py"
SLICE_PATH = ROOT / "eval" / "slice_v1.json"
SPLIT_PATH = ROOT / "eval" / "split_v1.json"
GT_PATH = ROOT / "data" / "boq_dataset_matched_GT.csv"
CLASSES_PATH = ROOT / "eval" / "annotations" / "blank_line_classes.csv"
SLICE_SIZE = 40


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("oris_eval_make_slice", MAKE_SLICE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make_slice = _load()


@pytest.fixture(scope="module")
def frozen() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(SLICE_PATH.read_text(encoding="utf-8"))
    return payload


@pytest.fixture(scope="module")
def split() -> dict[str, list[str]]:
    payload: dict[str, list[str]] = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    return payload


def test_slice_has_forty_distinct_dev_ids(
    frozen: dict[str, Any], split: dict[str, list[str]]
) -> None:
    ids = frozen["item_ids"]
    assert len(ids) == SLICE_SIZE
    assert len(set(ids)) == SLICE_SIZE
    assert set(ids) <= set(split["item_ids_dev"])
    assert not set(ids) & set(split["item_ids_lockbox"])


def test_slice_sha_matches_the_canonical_id_list(frozen: dict[str, Any]) -> None:
    canonical = "\n".join(sorted(frozen["item_ids"])) + "\n"
    assert frozen["item_ids"] == sorted(frozen["item_ids"])
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == frozen["ids_sha256"]


def test_slice_records_the_split_it_was_drawn_from(frozen: dict[str, Any]) -> None:
    assert frozen["split_sha256"] == hashlib.sha256(SPLIT_PATH.read_bytes()).hexdigest()
    assert frozen["salt"]
    assert frozen["rule"]


def test_slice_is_deterministic_and_matches_the_file() -> None:
    first = make_slice.render(make_slice.build_slice(GT_PATH, CLASSES_PATH, SPLIT_PATH))
    second = make_slice.render(make_slice.build_slice(GT_PATH, CLASSES_PATH, SPLIT_PATH))
    assert first == second
    assert first == SLICE_PATH.read_text(encoding="utf-8")


def test_allocation_is_proportional_largest_remainder(frozen: dict[str, Any]) -> None:
    strata = frozen["strata"]
    population = sum(stratum["population"] for stratum in strata)
    assert population == frozen["population"] == 162
    assert sum(stratum["allocated"] for stratum in strata) == SLICE_SIZE
    for stratum in strata:
        exact = SLICE_SIZE * stratum["population"] / population
        assert math.floor(exact) <= stratum["allocated"] <= math.ceil(exact)


def test_strata_counts_match_the_ids(frozen: dict[str, Any]) -> None:
    by_stratum = {(s["section"], s["class"]): s["allocated"] for s in frozen["strata"]}
    counted: dict[tuple[str, str], int] = {}
    for entry in frozen["items"]:
        key = (entry["section"], entry["class"])
        counted[key] = counted.get(key, 0) + 1
    assert counted == {key: value for key, value in by_stratum.items() if value}
    assert sorted(entry["item_no"] for entry in frozen["items"]) == frozen["item_ids"]


def test_largest_remainder_breaks_ties_deterministically() -> None:
    allocation = make_slice.allocate({("a", "x"): 1, ("b", "x"): 1, ("c", "x"): 1}, 2)
    assert allocation == {("a", "x"): 1, ("b", "x"): 1, ("c", "x"): 0}


def test_rank_is_salted_sha256() -> None:
    expected = hashlib.sha256((make_slice.SALT + "01.01.0010.").encode("utf-8")).hexdigest()
    assert make_slice.rank_key("01.01.0010.") == expected
