import dataclasses
import itertools
from typing import Any

import pytest

from oris_matcher.domain.attributes import Attributes, AttrResult
from oris_matcher.domain.boq import BoqLine, LineKind, SectionHeader, make_line_id
from oris_matcher.domain.decision import (
    NOT_A_MATERIAL_REASONS,
    ConfidenceBucket,
    Decision,
    DecisionInput,
    DecisionProfile,
    LineDecision,
    LLMFailureKind,
    PassOutcome,
    ReasonCode,
    Rule,
    Signals,
    Threshold,
    candidate_thresholds,
    decide,
    decide_b2,
    is_service_unit,
    make_haystack,
    section_path_text,
    strictest_threshold,
)
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.prompts.v1 import render
from oris_matcher.prompts.v1.schema import LineAnswer

SHA = "c" * 64
C30 = (30, 37)
C25 = (25, 30)
BLANK = "T01.U01.S00"
SPECIFIC = "T01.U01.S01"
OTHER_SPECIFIC = "T01.U01.S02"
NEVER = "T01.U02.S00"
STEEL = "T02.U01.S01"


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
    _row(SPECIFIC, "for footings", "C30/37 ", strength_class=C30),
    _row(OTHER_SPECIFIC, "for footings", "C25/30", strength_class=C25),
    _row(NEVER, "Custom material (Carbon impact in kg)", "", strength_class=(40, 50)),
    _row(STEEL, "rebar", "ø12", diameter_mm=12),
)
LIBRARY = Library(
    rows=ROWS,
    sha256=SHA,
    never_match_codes=frozenset({NEVER}),
    mixed_parents={BLANK: (SPECIFIC, OTHER_SPECIFIC)},
    sole_children=frozenset({NEVER, STEEL}),
    confusable_groups=(),
)
STRICTEST = strictest_threshold(2)
LOOSEST = candidate_thresholds(2)[-1]
MATCH_ALL = Threshold("B2", Signals(0, AttrResult.NO_EVIDENCE, ConfidenceBucket.BELOW_70))
PATH = (SectionHeader("02", "Concrete works"),)


def _line(
    unit: str = "m³",
    qty: str = "12,5",
    kind: LineKind = LineKind.ITEM,
    short: str = "Concrete C30/37 for footings",
    item_no: str = "02.01",
) -> BoqLine:
    return BoqLine(
        position=3,
        line_id=make_line_id(3, item_no, short, ""),
        item_no=item_no,
        short=short,
        long="",
        unit=unit,
        qty=qty,
        kind=kind,
        section_path=PATH,
        extra=(),
        raw_row=(item_no, short, "", unit, qty),
    )


def _answer(**overrides: Any) -> LineAnswer:
    values: dict[str, Any] = {
        "id": "L3",
        "evidence": "Concrete C30/37",
        "element_or_application": "footings",
        "material_family": "concrete",
        "kind": "material",
        "nm_category": "",
        "top1": SPECIFIC,
        "top2": OTHER_SPECIFIC,
        "confidence": 95,
        "self_reported_candidate_gap": "clear",
    }
    values.update(overrides)
    return LineAnswer.model_validate(values)


def _ok(call_id: str, **overrides: Any) -> PassOutcome:
    return PassOutcome(call_ids=(call_id,), answer=_answer(**overrides))


def _failed(call_id: str, kind: LLMFailureKind) -> PassOutcome:
    return PassOutcome(call_ids=(call_id,), failure=kind)


def _input(**overrides: Any) -> DecisionInput:
    line = overrides.pop("line", _line())
    values: dict[str, Any] = {
        "line": line,
        "haystack": make_haystack(line),
        "attributes": Attributes(strength_class=C30),
        "has_supply_marker": False,
        "is_service_unit": False,
        "passes": (_ok("c1"), _ok("c2")),
        "threshold": STRICTEST,
    }
    values.update(overrides)
    return DecisionInput(**values)


def _service_input(**overrides: Any) -> DecisionInput:
    nm = {"kind": "non_material", "nm_category": "labour", "top1": "", "top2": ""}
    values: dict[str, Any] = {
        "line": _line(unit="day", short="Labour for formwork"),
        "attributes": Attributes(),
        "is_service_unit": True,
        "passes": (_ok("c1", evidence="Labour", **nm), _ok("c2", evidence="Labour", **nm)),
    }
    values.update(overrides)
    return _input(**values)


def _assert(decision: LineDecision, rule: Rule, reason: str) -> None:
    assert (decision.rule, decision.reason) == (rule, reason)


def test_make_haystack_normalises_short_long_and_path() -> None:
    line = _line(short="Béton  C30/37,  2,5 m")
    assert make_haystack(line) == "béton c30/37, 2.5 m 02 concrete works"


def test_section_path_text_is_the_rendered_path() -> None:
    nested = (SectionHeader("0", "Preliminaries"), SectionHeader("", "Traffic"))
    for path in ((), PATH, nested):
        line = dataclasses.replace(_line(), section_path=path)
        assert section_path_text(line) == render.section_path_text(line)
    nested_line = dataclasses.replace(_line(), section_path=nested)
    assert section_path_text(nested_line) == "0 Preliminaries > Traffic"


def test_d5a_accepts_any_substring_of_the_rendered_path() -> None:
    path = (SectionHeader("0", "Preliminaries and general"), SectionHeader("00.02.", "Traffic"))
    line = dataclasses.replace(_line(), section_path=path)
    rendered = render.section_path_text(line)
    for start, end in ((0, len(rendered)), (2, 20), (len(rendered) - 9, len(rendered))):
        passes = (_ok("c1"), _ok("c2", evidence=rendered[start:end]))
        _assert(decide(_input(line=line, passes=passes), LIBRARY), Rule.D9, "SIGNAL:T1")


def test_candidate_thresholds_for_two_passes() -> None:
    candidates = candidate_thresholds(2)
    assert [threshold.threshold_id for threshold in candidates] == [f"T{n}" for n in range(1, 9)]
    assert candidates[0] == strictest_threshold(2)
    assert candidates[0].minimum == Signals(2, AttrResult.AGREE, ConfidenceBucket.FROM_90)
    assert candidates[-1].minimum == Signals(2, AttrResult.NO_EVIDENCE, ConfidenceBucket.BELOW_70)
    assert all(threshold.minimum.votes == 2 for threshold in candidates)
    ranks = [threshold.minimum.rank() for threshold in candidates]
    assert ranks == sorted(set(ranks), reverse=True)


def test_candidate_thresholds_are_nested_supersets() -> None:
    candidates = candidate_thresholds(3)
    scores = [
        Signals(votes, attributes, bucket)
        for votes in range(4)
        for attributes in (AttrResult.AGREE, AttrResult.NO_EVIDENCE)
        for bucket in ConfidenceBucket
    ]
    for stricter, looser in itertools.pairwise(candidates):
        assert all(looser.meets(score) for score in scores if stricter.meets(score))
        assert any(looser.meets(score) and not stricter.meets(score) for score in scores)
    with pytest.raises(ValueError, match="pass"):
        candidate_thresholds(0)


@pytest.mark.parametrize(
    ("unit", "expected"),
    [("day", True), (" day ", True), ("Ft", True), ("ft", False), ("FT", False), ("m³", False)],
)
def test_is_service_unit_is_exact_after_strip(unit: str, expected: bool) -> None:
    assert is_service_unit(unit, frozenset({"day", "Ft"})) == expected


# D0, D0a, D0b: structural gates, before anything else


def test_d0_header() -> None:
    line = _line(unit="", qty="", kind=LineKind.HEADER, item_no="02")
    decision = decide(_input(line=line), LIBRARY)
    _assert(decision, Rule.D0, ReasonCode.HEADER)
    assert decision.decision == Decision.NOT_A_MATERIAL
    assert (decision.top1, decision.call_ids, decision.signals) == ("", (), None)


@pytest.mark.parametrize(("unit", "qty"), [("m³", ""), ("", "3"), ("m", "1")])
def test_d0_header_kind_with_a_unit_or_qty_is_never_skipped(unit: str, qty: str) -> None:
    line = _line(unit=unit, qty=qty, kind=LineKind.HEADER)
    decision = decide(_input(line=line, passes=()), LIBRARY)
    assert decision.decision != Decision.NOT_A_MATERIAL
    _assert(decision, Rule.D1, ReasonCode.INTERNAL_INVARIANT)


def test_d0a_empty_row() -> None:
    line = _line(unit="", qty="", kind=LineKind.EMPTY_ROW, short="", item_no="")
    decision = decide(_input(line=line), LIBRARY)
    _assert(decision, Rule.D0A, ReasonCode.EMPTY_ROW)
    assert decision.decision == Decision.NOT_A_MATERIAL


def test_d0a_empty_kind_with_text_is_never_skipped() -> None:
    line = _line(unit="", qty="", kind=LineKind.EMPTY_ROW, item_no="")
    decision = decide(_input(line=line, passes=()), LIBRARY)
    _assert(decision, Rule.D0B, ReasonCode.HEADER_UNCONFIRMED)


def test_d0b_is_decided_by_empty_unit_and_qty_whatever_the_kind() -> None:
    line = _line(unit="", qty=" ", kind=LineKind.ITEM)
    _assert(decide(_input(line=line), LIBRARY), Rule.D0B, ReasonCode.HEADER_UNCONFIRMED)


def test_d0b_header_unconfirmed() -> None:
    line = _line(unit="", qty="", kind=LineKind.HEADER_UNCONFIRMED)
    decision = decide(_input(line=line), LIBRARY)
    _assert(decision, Rule.D0B, ReasonCode.HEADER_UNCONFIRMED)
    assert decision.decision == Decision.NEEDS_REVIEW


# D1, D1b: model failures


@pytest.mark.parametrize("failure", [ReasonCode.LLM_UNAVAILABLE, ReasonCode.BUDGET_CAP])
def test_d1_line_failure(failure: ReasonCode) -> None:
    decision = decide(_input(passes=(), line_failure=failure), LIBRARY)
    _assert(decision, Rule.D1, failure)


def test_d1_line_failure_wins_over_answers() -> None:
    decision = decide(_input(line_failure=ReasonCode.BUDGET_CAP), LIBRARY)
    _assert(decision, Rule.D1, ReasonCode.BUDGET_CAP)


def test_d1_no_valid_answer_uses_first_failure_kind() -> None:
    passes = (_failed("c1", LLMFailureKind.TIMEOUT), _failed("c2", LLMFailureKind.MALFORMED))
    decision = decide(_input(passes=passes), LIBRARY)
    _assert(decision, Rule.D1, "LLM_FAILURE:timeout")
    assert decision.call_ids == ("c1", "c2")


@pytest.mark.parametrize("kind", [LLMFailureKind.REPLAY_MISS, LLMFailureKind.DUPLICATE_CONFLICT])
def test_d1_replay_miss_and_conflicting_duplicate_win_over_an_answer(
    kind: LLMFailureKind,
) -> None:
    decision = decide(_input(passes=(_ok("c1"), _failed("c2", kind))), LIBRARY)
    _assert(decision, Rule.D1, f"LLM_FAILURE:{kind.value}")


def test_d1_replay_miss_wins_over_an_earlier_failure() -> None:
    passes = (_failed("c1", LLMFailureKind.TIMEOUT), _failed("c2", LLMFailureKind.REPLAY_MISS))
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D1, "LLM_FAILURE:replay_miss")


def test_d1_routed_line_without_passes_is_an_internal_invariant() -> None:
    _assert(decide(_input(passes=()), LIBRARY), Rule.D1, ReasonCode.INTERNAL_INVARIANT)


def test_d1b_missing_second_pass_is_partial_signal() -> None:
    passes = (_ok("c1"), _failed("c2", LLMFailureKind.TIMEOUT))
    decision = decide(_input(passes=passes), LIBRARY)
    _assert(decision, Rule.D1B, "LLM_FAILURE:partial_signal")
    assert decision.call_ids == ("c1", "c2")
    assert decision.top1 == SPECIFIC


def test_d1b_wins_over_d2() -> None:
    service = _service_input()
    passes = (service.passes[0], _failed("c2", LLMFailureKind.OVERLOADED))
    decision = decide(_service_input(passes=passes), LIBRARY)
    _assert(decision, Rule.D1B, "LLM_FAILURE:partial_signal")


# D2, D3, D4: the model's kind


def test_d2_g2_service() -> None:
    decision = decide(_service_input(), LIBRARY)
    _assert(decision, Rule.D2, ReasonCode.G2_SERVICE)
    assert decision.decision == Decision.NOT_A_MATERIAL
    assert decision.call_ids == ("c1", "c2")


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_service_unit": False, "line": _line(unit="m³", short="Labour for formwork")},
        {"attributes": Attributes(diameter_mm=12)},
        {"attributes": Attributes(recycled_pct=0)},
        {"has_supply_marker": True},
    ],
)
def test_d3_when_any_g2_condition_fails(overrides: dict[str, Any]) -> None:
    _assert(decide(_service_input(**overrides), LIBRARY), Rule.D3, ReasonCode.NM_UNCONFIRMED)


def test_d2_soft_grading_alone_does_not_block_g2() -> None:
    decision = decide(_service_input(attributes=Attributes(grading=frozenset({"0/20"}))), LIBRARY)
    _assert(decision, Rule.D2, ReasonCode.G2_SERVICE)


def test_d3_when_passes_disagree_on_kind() -> None:
    service = _service_input()
    passes = (service.passes[0], _ok("c2", evidence="Labour"))
    _assert(decide(_service_input(passes=passes), LIBRARY), Rule.D3, ReasonCode.NM_UNCONFIRMED)


def test_d3_wins_over_d4() -> None:
    passes = (_ok("c1", kind="non_material"), _ok("c2", kind="no_equivalent"))
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D3, ReasonCode.NM_UNCONFIRMED)


@pytest.mark.parametrize("second_kind", ["no_equivalent", "material"])
def test_d4_no_equivalent(second_kind: str) -> None:
    passes = (_ok("c1", kind="no_equivalent", top1=""), _ok("c2", kind=second_kind))
    decision = decide(_input(passes=passes), LIBRARY)
    _assert(decision, Rule.D4, ReasonCode.NO_LIBRARY_EQUIVALENT)


def test_d4_wins_over_d5() -> None:
    passes = (_ok("c1", kind="no_equivalent", top1="garbage"),) * 2
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D4, ReasonCode.NO_LIBRARY_EQUIVALENT)


# D5, D5a: closed world and evidence


@pytest.mark.parametrize(
    "top1",
    ["", "t01.u01.s01", " T01.U01.S01", "T01.U01.S01 ", "T01.U01", "T01.U09.S01", "T02.U01.S09"],
)
def test_d5_invalid_row_id(top1: str) -> None:
    decision = decide(_input(passes=(_ok("c1", top1=top1), _ok("c2", top1=top1))), LIBRARY)
    _assert(decision, Rule.D5, ReasonCode.INVALID_ROW_ID)
    assert decision.top1 == top1
    assert decision.row is None


def test_d5_wins_over_d5a() -> None:
    passes = (_ok("c1", top1="T09.U01.S01", evidence="not in line"),) * 2
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D5, ReasonCode.INVALID_ROW_ID)


@pytest.mark.parametrize(
    "evidence",
    ["", "   ", "///", "Reinforced steel", "Concrete C30/37 for walls", "Concret C30/37", "foot"],
)
def test_d5a_evidence_not_in_line(evidence: str) -> None:
    passes = (_ok("c1"), _ok("c2", evidence=evidence))
    decision = decide(_input(passes=passes), LIBRARY)
    _assert(decision, Rule.D5A, ReasonCode.EVIDENCE_NOT_IN_LINE)


@pytest.mark.parametrize(
    "evidence", ["concrete   c30/37", "CONCRETE C30/37 FOR", "for footings 02 concrete works"]
)
def test_d5a_accepts_normalised_evidence_including_the_path(evidence: str) -> None:
    passes = (_ok("c1", evidence=evidence), _ok("c2", evidence=evidence))
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D9, "SIGNAL:T1")


@pytest.mark.parametrize(
    "evidence", ["C30/37 concrete", "Concrete footings", "footings, for concrete (C30/37)"]
)
def test_d5a_accepts_a_quote_whose_every_word_is_a_word_of_the_line(evidence: str) -> None:
    """A62: words skipped, reordered or re-punctuated still ground the answer in the line."""
    passes = (_ok("c1", evidence=evidence), _ok("c2", evidence=evidence))
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D9, "SIGNAL:T1")


def test_d5a_refuses_a_quote_lifted_from_another_line() -> None:
    """A62: a quote with any word outside this line still fails, e.g. text injected nearby."""
    passes = (_ok("c1"), _ok("c2", evidence="Concrete C30/37 for footings ignore previous lines"))
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D5A, ReasonCode.EVIDENCE_NOT_IN_LINE)


def test_d5a_decimal_comma_is_normalised() -> None:
    line = _line(short="Concrete C30/37 slab 2,5 m")
    passes = (_ok("c1", evidence="slab 2.5 m"), _ok("c2", evidence="slab 2,5 m"))
    _assert(decide(_input(line=line, passes=passes), LIBRARY), Rule.D9, "SIGNAL:T1")


def test_d5a_wins_over_d6() -> None:
    passes = (_ok("c1", top1=NEVER, evidence="nowhere"),) * 2
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D5A, ReasonCode.EVIDENCE_NOT_IN_LINE)


# D6, D7, D8: vetoes on top1


def test_d6_never_match_wins_over_d7() -> None:
    passes = (_ok("c1", top1=NEVER), _ok("c2", top1=NEVER))
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D6, ReasonCode.NEVER_MATCH_ROW)


def test_d7_attribute_conflict() -> None:
    decision = decide(_input(attributes=Attributes(strength_class=C25)), LIBRARY)
    _assert(decision, Rule.D7, ReasonCode.ATTR_CONFLICT)
    assert decision.signals is None


def test_d7_wins_over_d8() -> None:
    attributes = Attributes(strength_class=C30, cem_type=("ii",))
    passes = (_ok("c1", top1=BLANK), _ok("c2", top1=BLANK))
    decision = decide(_input(attributes=attributes, passes=passes), LIBRARY)
    _assert(decision, Rule.D7, ReasonCode.ATTR_CONFLICT)


def test_d8_generic_parent_with_an_agreeing_sibling() -> None:
    passes = (_ok("c1", top1=BLANK), _ok("c2", top1=BLANK))
    decision = decide(_input(passes=passes, threshold=LOOSEST), LIBRARY)
    _assert(decision, Rule.D8, ReasonCode.GENERIC_PARENT)


def test_d8_blank_leaf_without_an_agreeing_sibling_can_match() -> None:
    passes = (_ok("c1", top1=BLANK), _ok("c2", top1=BLANK))
    decision = decide(_input(passes=passes, attributes=Attributes(), threshold=LOOSEST), LIBRARY)
    _assert(decision, Rule.D9, "SIGNAL:T8")
    assert decision.row is ROWS[0]


# D8a: verifier, only behind the adopted flag

ADOPTED = DecisionProfile(verifier_adopted=True)


def test_d8a_ignored_when_not_adopted() -> None:
    decision = decide(_input(verifier_top1=OTHER_SPECIFIC), LIBRARY)
    _assert(decision, Rule.D9, "SIGNAL:T1")


def test_d8a_verifier_disagrees_when_adopted() -> None:
    decision = decide(_input(verifier_top1=OTHER_SPECIFIC), LIBRARY, ADOPTED)
    _assert(decision, Rule.D8A, ReasonCode.VERIFIER_DISAGREES)


def test_d8a_verifier_agrees_when_adopted() -> None:
    _assert(decide(_input(verifier_top1=SPECIFIC), LIBRARY, ADOPTED), Rule.D9, "SIGNAL:T1")


def test_d8a_missing_verifier_when_adopted_is_partial_signal() -> None:
    decision = decide(_input(), LIBRARY, ADOPTED)
    _assert(decision, Rule.D1B, "LLM_FAILURE:partial_signal")


def test_d8_wins_over_d8a() -> None:
    passes = (_ok("c1", top1=BLANK), _ok("c2", top1=BLANK))
    decision = decide(_input(passes=passes, verifier_top1=SPECIFIC), LIBRARY, ADOPTED)
    _assert(decision, Rule.D8, ReasonCode.GENERIC_PARENT)


# D9, D10: score s against the threshold


def test_d9_matched_returns_the_library_row_verbatim() -> None:
    decision = decide(_input(), LIBRARY)
    _assert(decision, Rule.D9, "SIGNAL:T1")
    assert decision.decision == Decision.MATCHED
    assert decision.row is ROWS[1]
    assert decision.row.material_subtype == "C30/37 "
    assert (decision.top1, decision.top2) == (SPECIFIC, OTHER_SPECIFIC)
    assert decision.signals == Signals(2, AttrResult.AGREE, ConfidenceBucket.FROM_90)
    assert decision.call_ids == ("c1", "c2")


def test_d9_ignores_model_labels() -> None:
    passes = (_ok("c1", material_family="steel"), _ok("c2", element_or_application="rebar"))
    decision = decide(_input(passes=passes), LIBRARY)
    assert decision.row is ROWS[1]


def test_d9_confidence_is_the_weakest_supporting_pass() -> None:
    passes = (_ok("c1", confidence=99), _ok("c2", confidence=85))
    decision = decide(_input(passes=passes), LIBRARY)
    _assert(decision, Rule.D10, "LOW_SIGNAL:confidence")
    assert decision.signals == Signals(2, AttrResult.AGREE, ConfidenceBucket.FROM_80)


def test_d10_split_vote_is_a_tie() -> None:
    passes = (_ok("c1"), _ok("c2", top1=OTHER_SPECIFIC))
    decision = decide(_input(passes=passes, attributes=Attributes()), LIBRARY)
    _assert(decision, Rule.D10, "LOW_SIGNAL:v+b")
    assert decision.top1 == SPECIFIC


def test_d10_tie_never_matches_even_at_a_loose_threshold() -> None:
    passes = (_ok("c1", top1=STEEL), _ok("c2"))
    decision = decide(_input(passes=passes, attributes=Attributes(), threshold=LOOSEST), LIBRARY)
    _assert(decision, Rule.D10, "LOW_SIGNAL:v")
    assert decision.top1 == STEEL


def test_d10_tie_runs_vetoes_on_the_first_pass_leader() -> None:
    passes = (_ok("c1", top1="bad"), _ok("c2"))
    _assert(decide(_input(passes=passes), LIBRARY), Rule.D5, ReasonCode.INVALID_ROW_ID)


def test_d10_no_attribute_evidence_at_strictest() -> None:
    decision = decide(_input(attributes=Attributes()), LIBRARY)
    _assert(decision, Rule.D10, "LOW_SIGNAL:b")


@pytest.mark.parametrize("threshold", [STRICTEST, LOOSEST])
def test_d1b_fewer_passes_than_the_threshold_needs(threshold: Threshold) -> None:
    decision = decide(_input(passes=(_ok("c1"),), threshold=threshold), LIBRARY)
    _assert(decision, Rule.D1B, "LLM_FAILURE:partial_signal")
    assert (decision.top1, decision.call_ids, decision.signals) == (SPECIFIC, ("c1",), None)


def test_three_pass_plurality_two_to_one() -> None:
    passes = (_ok("c1", top1=STEEL), _ok("c2"), _ok("c3"))
    decision = decide(_input(passes=passes, threshold=STRICTEST), LIBRARY)
    _assert(decision, Rule.D9, "SIGNAL:T1")
    assert decision.top1 == SPECIFIC


def test_d5a_and_confidence_read_only_the_supporting_passes() -> None:
    dissenter = _ok("c1", top1=STEEL, evidence="nowhere", confidence=5)
    decision = decide(_input(passes=(dissenter, _ok("c2"), _ok("c3"))), LIBRARY)
    _assert(decision, Rule.D9, "SIGNAL:T1")
    assert decision.signals is not None
    assert decision.signals.confidence == ConfidenceBucket.FROM_90


@pytest.mark.parametrize("top2", ["T99.U99.S99", "t01.u01.s02", "Concrete", " T01.U01.S02"])
def test_top2_outside_the_library_is_blanked(top2: str) -> None:
    passes = (_ok("c1", top2=top2), _ok("c2", top2=top2))
    matched = decide(_input(passes=passes), LIBRARY)
    _assert(matched, Rule.D9, "SIGNAL:T1")
    assert matched.top2 == ""
    reviewed = decide(_input(passes=passes, attributes=Attributes()), LIBRARY)
    _assert(reviewed, Rule.D10, "LOW_SIGNAL:b")
    assert reviewed.top2 == ""
    b2 = decide_b2(_input(passes=passes[:1], threshold=MATCH_ALL), LIBRARY)
    assert (b2.decision, b2.top2) == (Decision.MATCHED, "")


def test_valid_top2_is_kept() -> None:
    assert decide(_input(), LIBRARY).top2 == OTHER_SPECIFIC


def test_line_decision_carries_the_line_id() -> None:
    header = _input(line=_line(unit="", qty="", kind=LineKind.HEADER, item_no="02"))
    for decision_input in (_input(), header, _input(passes=())):
        for decider in (decide, decide_b2):
            decision = decider(decision_input, LIBRARY)
            assert decision.line_id == decision_input.line.line_id


# B2 profile (DESIGN.md §10.5): gates D0/D0a/D0b, D1/D1b, D5 kept; every other valid answer
# matched


def _b2_input(*passes: PassOutcome, **overrides: Any) -> DecisionInput:
    return _input(passes=passes or (_ok("c1"),), threshold=MATCH_ALL, **overrides)


def test_b2_matches_where_b3_would_veto_or_abstain() -> None:
    vetoed = [
        _b2_input(attributes=Attributes(strength_class=C25)),
        _b2_input(_ok("c1", top1=NEVER)),
        _b2_input(_ok("c1", evidence="not in line")),
        _b2_input(_ok("c1", top1=BLANK)),
        _b2_input(_ok("c1", confidence=10)),
        _b2_input(_ok("c1", kind="non_material", top1=STEEL)),
        _b2_input(_ok("c1", kind="no_equivalent", top1=STEEL)),
    ]
    for decision_input in vetoed:
        decision = decide_b2(decision_input, LIBRARY)
        assert decision.decision == Decision.MATCHED
        assert decision.reason == f"SIGNAL:{decision_input.threshold.threshold_id}"
        assert decision.row is LIBRARY.by_code[decision.top1]


def test_b2_keeps_the_gates() -> None:
    header = _line(unit="", qty="", kind=LineKind.HEADER, item_no="02")
    _assert(decide_b2(_input(line=header), LIBRARY), Rule.D0, ReasonCode.HEADER)
    unconfirmed = _line(unit="", qty="", kind=LineKind.HEADER_UNCONFIRMED)
    _assert(decide_b2(_input(line=unconfirmed), LIBRARY), Rule.D0B, ReasonCode.HEADER_UNCONFIRMED)
    timeout = _b2_input(_failed("c1", LLMFailureKind.TIMEOUT))
    _assert(decide_b2(timeout, LIBRARY), Rule.D1, "LLM_FAILURE:timeout")
    invalid = _b2_input(_ok("c1", top1="T01.U01.s01"))
    _assert(decide_b2(invalid, LIBRARY), Rule.D5, ReasonCode.INVALID_ROW_ID)


def test_b2_never_skips_a_service_line() -> None:
    service = _service_input(passes=_service_input().passes[:1], threshold=MATCH_ALL)
    _assert(decide_b2(service, LIBRARY), Rule.D3, ReasonCode.NM_UNCONFIRMED)


@pytest.mark.parametrize("top1", ["", "T09.U01.S01"])
@pytest.mark.parametrize(
    ("kind", "rule", "reason"),
    [
        ("non_material", Rule.D3, ReasonCode.NM_UNCONFIRMED),
        ("no_equivalent", Rule.D4, ReasonCode.NO_LIBRARY_EQUIVALENT),
        ("material", Rule.D5, ReasonCode.INVALID_ROW_ID),
    ],
)
def test_b2_abstention_without_a_valid_code(
    top1: str, kind: str, rule: Rule, reason: ReasonCode
) -> None:
    decision = decide_b2(_b2_input(_ok("c1", kind=kind, top1=top1)), LIBRARY)
    _assert(decision, rule, reason)
    assert decision.decision == Decision.NEEDS_REVIEW


def test_b2_with_more_than_one_pass_is_an_internal_invariant() -> None:
    passes = (_ok("c1", top1=STEEL), _ok("c2"))
    decision = decide_b2(_input(passes=passes, threshold=MATCH_ALL), LIBRARY)
    _assert(decision, Rule.D1, ReasonCode.INTERNAL_INVARIANT)
    assert decision.call_ids == ("c1", "c2")


def test_rq5_provenance_over_the_table() -> None:
    inputs = [
        _input(line=_line(unit="", qty="", kind=LineKind.HEADER, item_no="02")),
        _input(line=_line(unit="", qty="", kind=LineKind.EMPTY_ROW, short="", item_no="")),
        _service_input(),
        _service_input(has_supply_marker=True),
        _input(),
    ]
    for decision_input in inputs:
        for decision in (decide(decision_input, LIBRARY), decide_b2(decision_input, LIBRARY)):
            if decision.decision == Decision.NOT_A_MATERIAL:
                assert decision.reason in NOT_A_MATERIAL_REASONS
                assert decision.rule in {Rule.D0, Rule.D0A, Rule.D2}
