"""A64 flag off: decide() matches outcomes frozen from the pre-A64 decision table.

``fixtures/decision/flag_off_oracle.json`` holds drawn inputs over the A64 test library and,
for each, the decision that ``decide()`` returned at commit 484af8f (before A64; its
``domain/decision.py`` is identical to ``feat/g2-selection``'s). The property test in
``test_drop_conflicting_votes.py`` compares the new code with itself; this pins it to the old.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from oris_matcher.domain.attributes import Attributes
from oris_matcher.domain.decision import (
    DecisionInput,
    LineDecision,
    candidate_thresholds,
    decide,
    make_haystack,
)
from test_drop_conflicting_votes import (
    C25,
    DROP,
    KEEP,
    LIBRARY,
    LINE_ATTRIBUTES,
    _conflicts,
    _line,
    _ok,
)

ORACLE = Path(__file__).parent / "fixtures" / "decision" / "flag_off_oracle.json"
ATTRIBUTES = {
    "line": LINE_ATTRIBUTES,
    "none": Attributes(),
    "c25": Attributes(strength_class=C25),
}
THRESHOLDS = {threshold.threshold_id: threshold for threshold in candidate_thresholds(2)}
MINIMUM_CASES = 500
CASES: list[dict[str, Any]] = json.loads(ORACLE.read_text(encoding="utf-8"))["cases"]


def _partial_conflict(case: dict[str, Any]) -> bool:
    attributes = ATTRIBUTES[case["attributes"]]
    conflicting = [answer for answer in case["answers"] if _conflicts(answer, attributes)]
    return 0 < len(conflicting) < len(case["answers"])


UNTOUCHED = [case for case in CASES if not _partial_conflict(case)]


def _input(case: dict[str, Any]) -> DecisionInput:
    passes = tuple(_ok(f"c{index}", **answer) for index, answer in enumerate(case["answers"]))
    line = _line()
    return DecisionInput(
        line=line,
        haystack=make_haystack(line),
        attributes=ATTRIBUTES[case["attributes"]],
        has_supply_marker=False,
        is_service_unit=False,
        passes=passes,
        threshold=THRESHOLDS[case["threshold"]],
    )


def _outcome(decision: LineDecision) -> dict[str, Any]:
    signals = decision.signals
    return {
        "rule": decision.rule.value,
        "decision": decision.decision.value,
        "reason": decision.reason,
        "row": decision.row.code if decision.row is not None else None,
        "top1": decision.top1,
        "top2": decision.top2,
        "call_ids": list(decision.call_ids),
        "signals": None
        if signals is None
        else [signals.votes, signals.attributes.value, signals.confidence.name],
    }


def test_the_oracle_is_large_and_covers_every_rule_the_drawn_inputs_reach() -> None:
    assert len(CASES) >= MINIMUM_CASES
    rules = {case["expected"]["rule"] for case in CASES}
    assert {"D1b", "D3", "D4", "D5", "D5a", "D6", "D7", "D8", "D9", "D10"} <= rules


@pytest.mark.parametrize("case", CASES, ids=[str(index) for index in range(len(CASES))])
def test_flag_off_decides_as_the_pre_a64_table(case: dict[str, Any]) -> None:
    decision_input = _input(case)
    assert _outcome(decide(decision_input, LIBRARY)) == case["expected"]
    assert _outcome(decide(decision_input, LIBRARY, KEEP)) == case["expected"]


@pytest.mark.parametrize("case", UNTOUCHED, ids=[str(index) for index in range(len(UNTOUCHED))])
def test_flag_on_without_a_partial_conflict_decides_as_the_pre_a64_table(
    case: dict[str, Any],
) -> None:
    assert _outcome(decide(_input(case), LIBRARY, DROP)) == case["expected"]
