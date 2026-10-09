"""A64: with ``drop_conflicting_votes`` on, a pass whose top1 conflicts casts no vote (§9.5 D7)."""

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from oris_matcher.domain.attributes import Attributes, AttrResult, compare
from oris_matcher.domain.boq import BoqLine, LineKind, SectionHeader, make_line_id
from oris_matcher.domain.decision import (
    DEFAULT_PROFILE,
    DecisionInput,
    DecisionProfile,
    LineDecision,
    PassOutcome,
    ReasonCode,
    Rule,
    Threshold,
    candidate_thresholds,
    decide,
    make_haystack,
    strictest_threshold,
)
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.domain.validator import is_valid_code
from oris_matcher.prompts.v1.schema import LineAnswer

SHA = "e" * 64
C30 = (30, 37)
C25 = (25, 30)
BLANK = "T01.U01.S00"
AGREEING = "T01.U01.S01"
CONFLICTING = "T01.U01.S02"
NEVER = "T01.U02.S00"
STEEL = "T02.U01.S01"
INVALID = "T99.U99.S99"
PATH = (SectionHeader("02", "Concrete works"),)
DROP = DecisionProfile(drop_conflicting_votes=True)
KEEP = DecisionProfile(drop_conflicting_votes=False)


def _row(code: str, usage: str, subtype: str, **attributes: Any) -> LibraryRow:
    return LibraryRow(
        row_id=f"id-{code}",
        code=code,
        material_type="Concrete" if code.startswith("T01") else "Steel",
        material_usage=usage,
        material_subtype=subtype,
        normalized_text=f"{usage} {subtype}".strip().lower(),
        attributes=Attributes(**attributes),
    )


ROWS = (
    _row(BLANK, "for footings", "", cem_type=("i",)),
    _row(AGREEING, "for footings", "C30/37", strength_class=C30),
    _row(CONFLICTING, "for footings", "C25/30", strength_class=C25),
    _row(NEVER, "Custom material (Carbon impact in kg)", "", strength_class=(40, 50)),
    _row(STEEL, "rebar", "ø12", diameter_mm=12),
)
LIBRARY = Library(
    rows=ROWS,
    sha256=SHA,
    never_match_codes=frozenset({NEVER}),
    mixed_parents={BLANK: (AGREEING, CONFLICTING)},
    sole_children=frozenset({NEVER, STEEL}),
    confusable_groups=(),
)
STRICTEST = strictest_threshold(2)
LOOSEST = candidate_thresholds(2)[-1]
LINE_ATTRIBUTES = Attributes(strength_class=C30)


def _line() -> BoqLine:
    short = "Concrete C30/37 for footings"
    return BoqLine(
        position=3,
        line_id=make_line_id(3, "02.01", short, ""),
        item_no="02.01",
        short=short,
        long="",
        unit="m³",
        qty="12,5",
        kind=LineKind.ITEM,
        section_path=PATH,
        extra=(),
        raw_row=("02.01", short, "", "m³", "12,5"),
    )


def _ok(call_id: str, **overrides: Any) -> PassOutcome:
    values: dict[str, Any] = {
        "id": "L3",
        "evidence": "Concrete C30/37",
        "element_or_application": "footings",
        "material_family": "concrete",
        "kind": "material",
        "nm_category": "",
        "top1": AGREEING,
        "top2": CONFLICTING,
        "confidence": 95,
        "self_reported_candidate_gap": "clear",
    }
    values.update(overrides)
    return PassOutcome(call_ids=(call_id,), answer=LineAnswer.model_validate(values))


def _input(*passes: PassOutcome, threshold: Threshold = STRICTEST) -> DecisionInput:
    line = _line()
    return DecisionInput(
        line=line,
        haystack=make_haystack(line),
        attributes=LINE_ATTRIBUTES,
        has_supply_marker=False,
        is_service_unit=False,
        passes=passes,
        threshold=threshold,
    )


def _both(decision_input: DecisionInput) -> tuple[LineDecision, LineDecision]:
    return decide(decision_input, LIBRARY, KEEP), decide(decision_input, LIBRARY, DROP)


def test_profile_flag_defaults_off() -> None:
    assert DecisionProfile().drop_conflicting_votes is False
    assert DEFAULT_PROFILE.drop_conflicting_votes is False


def test_one_conflicting_pass_leaves_the_correct_leader_at_low_signal_v() -> None:
    decision_input = _input(_ok("c1", top1=CONFLICTING), _ok("c2"))
    today, dropped = _both(decision_input)
    assert (today.rule, today.reason, today.top1) == (
        Rule.D7,
        ReasonCode.ATTR_CONFLICT,
        CONFLICTING,
    )
    assert (dropped.rule, dropped.reason, dropped.top1) == (Rule.D10, "LOW_SIGNAL:v", AGREEING)
    assert dropped.top2 == CONFLICTING
    assert dropped.signals is not None
    assert dropped.signals.votes == 1
    assert dropped.signals.attributes == AttrResult.AGREE
    assert dropped.call_ids == ("c1", "c2")


def test_a_dropped_vote_never_lets_a_line_match_at_the_loosest_threshold() -> None:
    decision_input = _input(_ok("c1", top1=CONFLICTING), _ok("c2"), threshold=LOOSEST)
    dropped = decide(decision_input, LIBRARY, DROP)
    assert (dropped.rule, dropped.reason, dropped.top1) == (Rule.D10, "LOW_SIGNAL:v", AGREEING)


def test_the_conflicting_pass_second_is_unchanged_low_signal_v() -> None:
    today, dropped = _both(_input(_ok("c1"), _ok("c2", top1=CONFLICTING)))
    assert today == dropped
    assert (dropped.rule, dropped.reason, dropped.top1) == (Rule.D10, "LOW_SIGNAL:v", AGREEING)


def test_both_conflicting_is_d7_on_the_original_tally() -> None:
    passes = (_ok("c1", top1=CONFLICTING, top2=AGREEING), _ok("c2", top1=CONFLICTING))
    today, dropped = _both(_input(*passes))
    assert today == dropped
    assert (dropped.rule, dropped.reason, dropped.top1) == (
        Rule.D7,
        ReasonCode.ATTR_CONFLICT,
        CONFLICTING,
    )
    assert dropped.top2 == AGREEING
    assert dropped.signals is None


def test_both_conflicting_on_a_never_match_row_is_decided_exactly_as_today() -> None:
    today, dropped = _both(_input(_ok("c1", top1=NEVER), _ok("c2", top1=NEVER)))
    assert today == dropped
    assert dropped.rule == Rule.D6


@pytest.mark.parametrize(
    "passes",
    [
        pytest.param((_ok("c1"), _ok("c2")), id="match"),
        pytest.param((_ok("c1", top1=STEEL), _ok("c2")), id="tie-no-evidence"),
        pytest.param((_ok("c1", top1=BLANK), _ok("c2", top1=BLANK)), id="generic-parent"),
        pytest.param((_ok("c1", confidence=75), _ok("c2")), id="low-confidence"),
    ],
)
def test_no_conflicting_pass_is_unchanged(passes: tuple[PassOutcome, ...]) -> None:
    today, dropped = _both(_input(*passes))
    assert today == dropped


@pytest.mark.parametrize(
    ("other", "rule"),
    [
        pytest.param({"kind": "non_material", "nm_category": "labour"}, Rule.D3, id="nm"),
        pytest.param({"kind": "no_equivalent", "top1": "", "top2": ""}, Rule.D4, id="no-eq"),
        pytest.param({"kind": "no_equivalent", "top1": CONFLICTING}, Rule.D4, id="no-eq-top1"),
    ],
)
def test_non_material_and_no_equivalent_answers_are_unaffected(
    other: dict[str, Any], rule: Rule
) -> None:
    today, dropped = _both(_input(_ok("c1", top1=CONFLICTING), _ok("c2", **other)))
    assert today == dropped
    assert dropped.rule == rule


def test_an_invalid_top1_is_never_dropped() -> None:
    today, dropped = _both(_input(_ok("c1", top1=CONFLICTING), _ok("c2", top1=INVALID)))
    assert today.rule == Rule.D7
    assert (dropped.rule, dropped.reason, dropped.top1) == (
        Rule.D5,
        ReasonCode.INVALID_ROW_ID,
        INVALID,
    )


def test_a_three_way_tie_retallies_into_a_two_way_tie() -> None:
    passes = (_ok("c1", top1=CONFLICTING), _ok("c2"), _ok("c3", top1=STEEL))
    today, dropped = _both(_input(*passes))
    assert today.rule == Rule.D7
    assert (dropped.rule, dropped.reason, dropped.top1) == (Rule.D10, "LOW_SIGNAL:v", AGREEING)
    assert dropped.call_ids == ("c1", "c2", "c3")


def test_three_passes_with_one_conflicting_keep_a_two_vote_majority() -> None:
    passes = (_ok("c1", top1=CONFLICTING), _ok("c2"), _ok("c3"))
    today, dropped = _both(_input(*passes))
    assert today == dropped
    assert (dropped.rule, dropped.reason) == (Rule.D9, "SIGNAL:T1")


def test_the_d5a_check_reads_only_the_remaining_supporters() -> None:
    passes = (_ok("c1", top1=CONFLICTING), _ok("c2", evidence="nowhere"))
    dropped = decide(_input(*passes), LIBRARY, DROP)
    assert (dropped.rule, dropped.reason) == (Rule.D5A, ReasonCode.EVIDENCE_NOT_IN_LINE)


CODES = (BLANK, AGREEING, CONFLICTING, NEVER, STEEL, INVALID, "")
KINDS = ("material", "material", "material", "non_material", "no_equivalent")
ATTRIBUTE_CHOICES = (LINE_ATTRIBUTES, Attributes(), Attributes(strength_class=C25))
answers = st.builds(
    lambda top1, kind, confidence, evidence: {
        "top1": top1,
        "kind": kind,
        "nm_category": "labour" if kind == "non_material" else "",
        "confidence": confidence,
        "evidence": evidence,
    },
    st.sampled_from(CODES),
    st.sampled_from(KINDS),
    st.sampled_from((50, 75, 85, 95)),
    st.sampled_from(("Concrete C30/37", "nowhere")),
)


def _conflicts(answer: dict[str, Any], attributes: Attributes) -> bool:
    if answer["kind"] != "material" or not is_valid_code(answer["top1"], LIBRARY):
        return False
    row = LIBRARY.by_code[answer["top1"]]
    return compare(attributes, row.attributes) == AttrResult.CONFLICT


@settings(max_examples=400, deadline=None)
@given(
    st.lists(answers, min_size=1, max_size=3),
    st.sampled_from(ATTRIBUTE_CHOICES),
    st.sampled_from(candidate_thresholds(2)),
)
def test_truth_table_property(
    drawn: list[dict[str, Any]], attributes: Attributes, threshold: Threshold
) -> None:
    passes = tuple(_ok(f"c{index}", **answer) for index, answer in enumerate(drawn))
    line = _line()
    decision_input = DecisionInput(
        line=line,
        haystack=make_haystack(line),
        attributes=attributes,
        has_supply_marker=False,
        is_service_unit=False,
        passes=passes,
        threshold=threshold,
    )
    default = decide(decision_input, LIBRARY)
    assert decide(decision_input, LIBRARY, KEEP) == default
    dropped = decide(decision_input, LIBRARY, DROP)
    conflicting = [answer for answer in drawn if _conflicts(answer, attributes)]
    if not conflicting or len(conflicting) == len(drawn):
        assert dropped == default
    reaches_the_vote = len(drawn) >= threshold.minimum.votes
    all_material = {answer["kind"] for answer in drawn} == {"material"}
    some_remain = 0 < len(conflicting) < len(drawn)
    if reaches_the_vote and all_material and some_remain:
        assert dropped.top1 not in {answer["top1"] for answer in conflicting}
        assert dropped.rule != Rule.D7
    assert dropped.call_ids == default.call_ids
    if dropped.rule == Rule.D9:
        assert dropped.signals is not None
        assert dropped.signals.votes >= threshold.minimum.votes
