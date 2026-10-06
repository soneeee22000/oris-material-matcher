"""E-08 in the decision table: which lines the verifier must check, and D8a / D1b (A65.2).

A flagged line is one whose plurality top1 passes D0-D8 and meets the threshold, i.e. one the
table matches (D9) with the verifier off. Only such a line needs a verifier answer: a missing
or invalid one is D1b ``partial_signal``, a disagreeing one (``NONE`` included) is D8a. Every
other line is decided exactly as with the verifier off, whatever ``verifier_top1`` holds.
"""

import dataclasses
from typing import Any

import pytest

from oris_matcher.domain.attributes import Attributes
from oris_matcher.domain.decision import (
    VERIFIER_NONE,
    DecisionInput,
    DecisionProfile,
    LLMFailureKind,
    ReasonCode,
    Rule,
    decide,
    is_flagged,
)
from test_decision_table import (
    BLANK,
    LIBRARY,
    LOOSEST,
    NEVER,
    OTHER_SPECIFIC,
    SPECIFIC,
    STEEL,
    _failed,
    _input,
    _line,
    _ok,
    _service_input,
)

ADOPTED = DecisionProfile(verifier_adopted=True)
OFF = DecisionProfile()
PARTIAL = "LLM_FAILURE:partial_signal"
LOW_CONFIDENCE = 50


def _cases() -> dict[str, DecisionInput]:
    """One input per row of the table a B3 line can reach after D0."""
    split = (_ok("c1"), _ok("c2", top1=OTHER_SPECIFIC))
    return {
        "d9": _input(),
        "d9_loosest": _input(threshold=LOOSEST),
        "d10_confidence": _input(passes=(_ok("c1"), _ok("c2", confidence=LOW_CONFIDENCE))),
        "d10_tie": _input(passes=split, threshold=LOOSEST),
        "d1b_failed_pass": _input(passes=(_ok("c1"), _failed("c2", LLMFailureKind.TIMEOUT))),
        "d1_replay_miss": _input(passes=(_failed("c1", LLMFailureKind.REPLAY_MISS),)),
        "d1_budget": _input(passes=(), line_failure=ReasonCode.BUDGET_CAP),
        "d2_service": _service_input(),
        "d4_no_equivalent": _input(
            passes=(_ok("c1"), _ok("c2", kind="no_equivalent", top1="", top2=""))
        ),
        "d5_invalid": _input(passes=(_ok("c1", top1="T99.U01.S01"), _ok("c2", top1="T99.U01.S01"))),
        "d5a_evidence": _input(passes=(_ok("c1", evidence="granite"), _ok("c2"))),
        "d6_never": _input(passes=(_ok("c1", top1=NEVER), _ok("c2", top1=NEVER))),
        "d7_conflict": _input(
            passes=(_ok("c1", top1=OTHER_SPECIFIC), _ok("c2", top1=OTHER_SPECIFIC))
        ),
        "d8_generic": _input(
            passes=(_ok("c1", top1=BLANK), _ok("c2", top1=BLANK)), threshold=LOOSEST
        ),
        "d9_steel": _input(
            line=_line(short="Rebar"),
            attributes=Attributes(),
            passes=(
                _ok("c1", top1=STEEL, evidence="Rebar"),
                _ok("c2", top1=STEEL, evidence="Rebar"),
            ),
            threshold=LOOSEST,
        ),
    }


CASES = _cases()


def _with_verifier(decision_input: DecisionInput, verifier_top1: str | None) -> DecisionInput:
    return dataclasses.replace(decision_input, verifier_top1=verifier_top1)


@pytest.mark.parametrize("name", sorted(CASES))
def test_flagged_is_exactly_would_be_d9_with_the_verifier_off(name: str) -> None:
    decision_input = CASES[name]
    would_match = decide(decision_input, LIBRARY, OFF).rule == Rule.D9
    assert is_flagged(decision_input, LIBRARY, ADOPTED) is would_match
    assert is_flagged(decision_input, LIBRARY, OFF) is would_match


def test_the_cases_cover_both_flagged_and_unflagged_lines() -> None:
    flagged = {name for name, item in CASES.items() if is_flagged(item, LIBRARY, ADOPTED)}
    assert flagged == {"d9", "d9_loosest", "d9_steel"}


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("verifier_top1", [None, SPECIFIC, OTHER_SPECIFIC, VERIFIER_NONE])
def test_an_unflagged_line_is_decided_as_with_the_verifier_off(
    name: str, verifier_top1: str | None
) -> None:
    decision_input = _with_verifier(CASES[name], verifier_top1)
    if is_flagged(decision_input, LIBRARY, ADOPTED):
        pytest.skip("flagged lines are covered below")
    assert decide(decision_input, LIBRARY, ADOPTED) == decide(decision_input, LIBRARY, OFF)


def test_a_low_signal_line_without_a_verifier_answer_stays_low_signal() -> None:
    decision_input = CASES["d10_confidence"]
    decision = decide(decision_input, LIBRARY, ADOPTED)
    assert (decision.rule, decision.reason) == (Rule.D10, "LOW_SIGNAL:confidence")


def test_a_service_line_without_a_verifier_answer_is_still_g2() -> None:
    decision = decide(CASES["d2_service"], LIBRARY, ADOPTED)
    assert (decision.rule, decision.reason) == (Rule.D2, ReasonCode.G2_SERVICE)


@pytest.mark.parametrize(
    ("verifier_top1", "rule", "reason"),
    [
        (SPECIFIC, Rule.D9, "SIGNAL:T1"),
        (OTHER_SPECIFIC, Rule.D8A, ReasonCode.VERIFIER_DISAGREES),
        (VERIFIER_NONE, Rule.D8A, ReasonCode.VERIFIER_DISAGREES),
        (None, Rule.D1B, PARTIAL),
    ],
)
def test_a_flagged_line_is_decided_by_the_verifier(
    verifier_top1: str | None, rule: Rule, reason: str
) -> None:
    decision = decide(_with_verifier(CASES["d9"], verifier_top1), LIBRARY, ADOPTED)
    assert (decision.rule, decision.reason) == (rule, reason)
    assert (decision.top1, decision.top2) == (SPECIFIC, OTHER_SPECIFIC)
    assert decision.call_ids == ("c1", "c2")


def test_an_agreeing_verifier_leaves_the_match_unchanged() -> None:
    decision_input = _with_verifier(CASES["d9_loosest"], SPECIFIC)
    assert decide(decision_input, LIBRARY, ADOPTED) == decide(decision_input, LIBRARY, OFF)


def test_a_lower_case_copy_of_top1_disagrees() -> None:
    decision = decide(_with_verifier(CASES["d9"], SPECIFIC.lower()), LIBRARY, ADOPTED)
    assert decision.rule == Rule.D8A


def test_the_verifier_is_read_only_when_adopted() -> None:
    for verifier_top1 in (None, VERIFIER_NONE, OTHER_SPECIFIC):
        decision = decide(_with_verifier(CASES["d9"], verifier_top1), LIBRARY, OFF)
        assert decision.rule == Rule.D9


def test_verifier_none_is_not_a_library_code() -> None:
    assert VERIFIER_NONE == "NONE"
    assert VERIFIER_NONE not in LIBRARY.by_code


def _all_flags(**overrides: Any) -> DecisionProfile:
    return DecisionProfile(verifier_adopted=True, drop_conflicting_votes=True, **overrides)


def test_flagging_reads_the_other_flags_of_the_profile() -> None:
    passes = (_ok("c1", top1=OTHER_SPECIFIC), _ok("c2"))
    decision_input = _input(passes=passes, threshold=LOOSEST)
    off_flagged = is_flagged(decision_input, LIBRARY, DecisionProfile(verifier_adopted=True))
    drop_flagged = is_flagged(decision_input, LIBRARY, _all_flags())
    assert off_flagged is False
    assert drop_flagged is False
    drop_off = decide(decision_input, LIBRARY, DecisionProfile(drop_conflicting_votes=True))
    assert is_flagged(decision_input, LIBRARY, _all_flags()) is (drop_off.rule == Rule.D9)
