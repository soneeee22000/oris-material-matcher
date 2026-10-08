"""The §10.6 selection rule on hand-built tables (DESIGN.md §10.6, A59.3, A60.3)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
TABLES = json.loads(
    (ROOT / "tests" / "fixtures" / "select" / "tables.json").read_text(encoding="utf-8")
)
CP_TOLERANCE = 1e-9


def _load(name: str, path: Path) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rule = _load("oris_eval_selection_rule", ROOT / "eval" / "selection_rule.py")
SCORE = _load("oris_eval_score", ROOT / "eval" / "score.py")


def _table(case: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            entry["id"],
            {lang: rule.language_stats(*entry[lang]) for lang in ("en", "fr")},
        )
        for entry in TABLES[case]["thresholds"]
    ]


def _check(case: str) -> Any:
    table = _table(case)
    expected = TABLES[case]["expected"]
    selection = rule.apply_rule(table)
    assert selection.threshold == expected["threshold"]
    assert selection.loosest_qualifying == expected["loosest"]
    assert selection.status == expected["status"]
    if "qualifies" in expected:
        qualifying = [key for key, stats in table if rule.qualifies(stats)]
        assert qualifying == expected["qualifies"]
    return selection


def test_loosest_qualifier_is_chosen() -> None:
    selection = _check("loosest_qualifier")
    assert selection.per_language["en"].matched == 60
    assert selection.candidates == 8


def test_within_three_picks_the_stricter_at_exactly_three() -> None:
    _check("within_three_at_exactly_three")


def test_within_three_does_not_pick_the_stricter_at_four() -> None:
    _check("within_three_not_at_four")


def test_a_stricter_threshold_within_three_below_the_floor_is_skipped() -> None:
    _check("stricter_below_the_floor")


def test_qualifiers_need_not_be_contiguous() -> None:
    _check("non_contiguous")


def test_empty_q_takes_the_best_minimum_cp_bound_and_ties_go_stricter() -> None:
    selection = _check("empty_with_zero_and_tie")
    table = dict(_table("empty_with_zero_and_tie"))
    assert table["T1"]["en"].cp_lower_95 == 0.0
    assert table["T1"]["en"].precision is None
    tied = [min(stats.cp_lower_95 for stats in table[key].values()) for key in ("T2", "T3")]
    assert tied[0] == tied[1]
    assert selection.status == rule.STATUS_BELOW_BAR


def test_target_changes_only_the_sensitivity_row() -> None:
    table = _table("loosest_qualifier")
    plain = rule.run_rule(table)
    targeted = rule.run_rule(table, target=0.85)

    assert plain.target is None
    assert targeted.selected == plain.selected
    assert targeted.per_language == plain.per_language
    assert targeted.selected.threshold == "T6"
    assert targeted.target is not None
    assert targeted.target.threshold == "T8"
    assert targeted.target.status == rule.STATUS_MEETS_BAR


def test_per_language_rows_apply_the_rule_to_one_language_alone() -> None:
    table = _table("stricter_below_the_floor")
    outcome = rule.run_rule(table)
    assert outcome.selected.threshold == "T3"
    assert outcome.per_language["en"].threshold == "T3"
    assert outcome.per_language["fr"].threshold == "T2"
    assert set(outcome.per_language["fr"].per_language) == {"fr"}


@pytest.mark.parametrize(("correct", "total"), [tuple(case) for case in TABLES["cp_cases"]])
def test_cp_lower_agrees_with_the_scorer(correct: int, total: int) -> None:
    ours = rule.cp_lower(correct, total)
    theirs = SCORE.cp_lower_bound(correct, total)
    assert ours == pytest.approx(theirs, abs=CP_TOLERANCE)


def test_cp_lower_is_zero_without_a_match() -> None:
    assert rule.cp_lower(0, 0) == 0.0
    assert rule.language_stats(0, 0).cp_lower_95 == 0.0
