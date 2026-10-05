import dataclasses
from collections.abc import Sequence
from typing import Any

import pytest

from oris_matcher.candidates.base import CandidateProvider
from oris_matcher.domain.attributes import (
    Attributes,
    AttrResult,
    Process,
    compare,
    has_hard_attribute,
)
from oris_matcher.domain.boq import BoqLine, LineKind, make_line_id
from oris_matcher.domain.decision import (
    RULE_DECISIONS,
    ConfidenceBucket,
    Decision,
    DecisionInput,
    LineDecision,
    LLMFailureKind,
    PassOutcome,
    ReasonCode,
    Rule,
    SignalName,
    Signals,
    Threshold,
    confidence_bucket,
)
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.prompts.v1.schema import LineAnswer

SHA = "a" * 64
STRENGTH = (30, 37)


def _row(code: str, subtype: str = "", **attributes: Any) -> LibraryRow:
    return LibraryRow(
        row_id=f"id-{code}",
        code=code,
        material_type="Concrete",
        material_usage="for footings",
        material_subtype=subtype,
        normalized_text=f"concrete for footings {subtype}".strip(),
        attributes=Attributes(**attributes),
    )


ROWS = (
    _row("T01.U01.S00"),
    _row("T01.U01.S01", "C30/37", strength_class=STRENGTH),
    _row("T01.U02.S00"),
    _row("T02.U01.S01", "C30/37"),
)


def _library(**overrides: Any) -> Library:
    values: dict[str, Any] = {
        "rows": ROWS,
        "sha256": SHA,
        "never_match_codes": frozenset({"T01.U02.S00"}),
        "mixed_parents": {"T01.U01.S00": ("T01.U01.S01",)},
        "sole_children": frozenset({"T01.U02.S00", "T02.U01.S01"}),
        "confusable_groups": (("T01.U01.S01", "T02.U01.S01"),),
    }
    values.update(overrides)
    return Library(**values)


def test_attributes_default_is_empty_and_hashable() -> None:
    empty = Attributes()
    assert not has_hard_attribute(empty)
    assert hash(empty) == hash(Attributes())
    with pytest.raises(dataclasses.FrozenInstanceError):
        empty.diameter_mm = 3  # type: ignore[misc]
    assert {member.value for member in Process} == {"wma", "hma"}


@pytest.mark.parametrize(
    "attributes",
    [
        {"strength_class": STRENGTH},
        {"exposure_classes": frozenset({"xc4"})},
        {"cem_type": ("ii",)},
        {"recycled_pct": 0},
        {"process": Process.WMA},
        {"ewc_code": "1705"},
        {"diameter_mm": 100},
        {"areal_mass_g_m2": 200},
    ],
)
def test_has_hard_attribute_per_family(attributes: dict[str, Any]) -> None:
    assert has_hard_attribute(Attributes(**attributes))


def test_grading_is_soft() -> None:
    assert not has_hard_attribute(Attributes(grading=frozenset({"0/20"})))
    line = Attributes(grading=frozenset({"50/70"}))
    row = Attributes(grading=frozenset({"35/50"}))
    assert compare(line, row) == AttrResult.NO_EVIDENCE


@pytest.mark.parametrize(
    ("line", "row", "expected"),
    [
        ({}, {}, AttrResult.NO_EVIDENCE),
        ({"strength_class": STRENGTH}, {}, AttrResult.NO_EVIDENCE),
        ({}, {"strength_class": STRENGTH}, AttrResult.NO_EVIDENCE),
        ({"strength_class": STRENGTH}, {"strength_class": STRENGTH}, AttrResult.AGREE),
        ({"strength_class": STRENGTH}, {"strength_class": (25, 30)}, AttrResult.CONFLICT),
        (
            {"exposure_classes": frozenset({"xc4", "xa1"})},
            {"exposure_classes": frozenset({"xc3", "xc4", "xa1"})},
            AttrResult.AGREE,
        ),
        (
            {"exposure_classes": frozenset({"xc4", "xs3"})},
            {"exposure_classes": frozenset({"xc4"})},
            AttrResult.CONFLICT,
        ),
        ({"cem_type": ("ii",)}, {"cem_type": ("ii", "a")}, AttrResult.AGREE),
        ({"cem_type": ("ii", "a")}, {"cem_type": ("ii",)}, AttrResult.AGREE),
        ({"cem_type": ("ii", "a")}, {"cem_type": ("ii", "b")}, AttrResult.CONFLICT),
        ({"cem_type": ("i",)}, {"cem_type": ("ii",)}, AttrResult.CONFLICT),
        ({"recycled_pct": 0}, {"recycled_pct": 0}, AttrResult.AGREE),
        ({"recycled_pct": 20}, {"recycled_pct": 40}, AttrResult.CONFLICT),
        ({"process": Process.WMA}, {"process": Process.HMA}, AttrResult.CONFLICT),
        ({"ewc_code": "1705"}, {"ewc_code": "170504"}, AttrResult.AGREE),
        ({"ewc_code": "170504"}, {"ewc_code": "1705"}, AttrResult.AGREE),
        ({"ewc_code": "1705"}, {"ewc_code": "1701"}, AttrResult.CONFLICT),
        ({"diameter_mm": 100}, {"diameter_mm": 160}, AttrResult.CONFLICT),
        ({"areal_mass_g_m2": 200}, {"areal_mass_g_m2": 200}, AttrResult.AGREE),
        (
            {"strength_class": STRENGTH, "process": Process.WMA},
            {"strength_class": STRENGTH, "process": Process.HMA},
            AttrResult.CONFLICT,
        ),
        (
            {"strength_class": STRENGTH},
            {"strength_class": STRENGTH, "diameter_mm": 100},
            AttrResult.AGREE,
        ),
    ],
)
def test_compare_from_the_line_side(
    line: dict[str, Any], row: dict[str, Any], expected: AttrResult
) -> None:
    assert compare(Attributes(**line), Attributes(**row)) == expected


def test_compare_absence_rule_on_spec_bundled_row() -> None:
    line = Attributes(strength_class=STRENGTH, exposure_classes=frozenset({"xc4", "xa1"}))
    row = Attributes(
        strength_class=STRENGTH,
        exposure_classes=frozenset({"xc3", "xc4", "xd1", "xs1", "xa1"}),
        cem_type=("i",),
    )
    assert compare(line, row) == AttrResult.AGREE


def test_library_row_code_shape() -> None:
    row = _row("T01.U01.S01", "C30/37")
    assert row.parent_code == "T01.U01"
    assert not row.is_blank_leaf
    assert _row("T01.U01.S00").is_blank_leaf
    assert hash(row) == hash(_row("T01.U01.S01", "C30/37"))
    for bad_code in ("T1.U01.S00", "T01.U01", "t01.u01.s00", "T01.U01.S00 "):
        with pytest.raises(ValueError, match="code"):
            _row(bad_code)


def test_library_lookup_and_structure() -> None:
    library = _library()
    assert library.by_code["T01.U01.S01"] is ROWS[1]
    assert list(library.by_code) == [row.code for row in ROWS]
    assert library.is_never_match("T01.U02.S00")
    assert not library.is_never_match("T01.U01.S01")
    assert library.mixed_parents["T01.U01.S00"] == ("T01.U01.S01",)
    assert isinstance(hash(library), int)


def test_library_mappings_are_read_only_and_isolated() -> None:
    parents = {"T01.U01.S00": ("T01.U01.S01",)}
    library = _library(mixed_parents=parents)
    parents["T02.U01.S01"] = ()
    assert "T02.U01.S01" not in library.mixed_parents
    with pytest.raises(TypeError):
        library.by_code["x"] = ROWS[0]  # type: ignore[index]
    with pytest.raises(TypeError):
        library.mixed_parents["x"] = ()  # type: ignore[index]


@pytest.mark.parametrize(
    "overrides",
    [
        {"rows": (*ROWS, _row("T01.U01.S01"))},
        {"rows": (*ROWS, dataclasses.replace(ROWS[0], code="T09.U01.S00"))},
        {"sha256": "abc"},
        {"sha256": "A" * 64},
        {"never_match_codes": frozenset({"T09.U09.S09"})},
        {"mixed_parents": {"T09.U09.S00": ("T01.U01.S01",)}},
        {"mixed_parents": {"T01.U01.S00": ("T09.U09.S09",)}},
        {"sole_children": frozenset({"T09.U09.S09"})},
        {"confusable_groups": (("T01.U01.S01", "T09.U09.S09"),)},
    ],
)
def test_library_rejects_inconsistent_contents(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="library"):
        _library(**overrides)


@pytest.mark.parametrize(
    ("confidence", "bucket"),
    [
        (100, ConfidenceBucket.FROM_90),
        (90, ConfidenceBucket.FROM_90),
        (89, ConfidenceBucket.FROM_80),
        (80, ConfidenceBucket.FROM_80),
        (79, ConfidenceBucket.FROM_70),
        (70, ConfidenceBucket.FROM_70),
        (69, ConfidenceBucket.BELOW_70),
        (0, ConfidenceBucket.BELOW_70),
    ],
)
def test_confidence_bucket(confidence: int, bucket: ConfidenceBucket) -> None:
    assert confidence_bucket(confidence) == bucket


@pytest.mark.parametrize("confidence", [-1, 101])
def test_confidence_bucket_rejects_out_of_range(confidence: int) -> None:
    with pytest.raises(ValueError, match="confidence"):
        confidence_bucket(confidence)


def test_confidence_buckets_are_ordered() -> None:
    assert (
        ConfidenceBucket.BELOW_70
        < ConfidenceBucket.FROM_70
        < ConfidenceBucket.FROM_80
        < ConfidenceBucket.FROM_90
    )


def _signals(
    votes: int = 2,
    attributes: AttrResult = AttrResult.AGREE,
    confidence: ConfidenceBucket = ConfidenceBucket.FROM_90,
) -> Signals:
    return Signals(votes=votes, attributes=attributes, confidence=confidence)


def test_signals_reject_conflict_and_negative_votes() -> None:
    with pytest.raises(ValueError, match="conflict"):
        _signals(attributes=AttrResult.CONFLICT)
    with pytest.raises(ValueError, match="votes"):
        _signals(votes=-1)


def test_strictest_threshold() -> None:
    strictest = Threshold(threshold_id="T1", minimum=_signals())
    assert strictest.meets(_signals())
    assert strictest.failed_signals(_signals()) == ()
    weaker = _signals(attributes=AttrResult.NO_EVIDENCE, confidence=ConfidenceBucket.FROM_80)
    assert not strictest.meets(weaker)
    assert strictest.failed_signals(weaker) == (SignalName.ATTRIBUTES, SignalName.CONFIDENCE)
    assert strictest.failed_signals(_signals(votes=1)) == (SignalName.VOTES,)


def test_threshold_is_lexicographic() -> None:
    loose = Threshold(
        threshold_id="T5",
        minimum=_signals(attributes=AttrResult.NO_EVIDENCE, confidence=ConfidenceBucket.FROM_90),
    )
    agree_low_confidence = _signals(confidence=ConfidenceBucket.BELOW_70)
    assert loose.meets(agree_low_confidence)
    assert loose.failed_signals(agree_low_confidence) == ()
    assert not loose.meets(_signals(votes=1))


def test_threshold_id_is_validated() -> None:
    with pytest.raises(ValueError, match="threshold"):
        Threshold(threshold_id="T 1", minimum=_signals())


def _answer() -> LineAnswer:
    return LineAnswer.model_validate(
        {
            "id": "L3",
            "evidence": "concrete c30/37",
            "element_or_application": "footings",
            "material_family": "concrete",
            "kind": "material",
            "nm_category": "",
            "top1": "T01.U01.S01",
            "top2": "",
            "confidence": 92,
            "self_reported_candidate_gap": "clear",
        }
    )


def test_pass_outcome_holds_an_answer_or_a_failure() -> None:
    answered = PassOutcome(call_ids=("c1",), answer=_answer())
    failed = PassOutcome(call_ids=("c2", "c3"), failure=LLMFailureKind.TIMEOUT)
    assert answered.failure is None
    assert failed.answer is None
    assert isinstance(hash(answered), int)
    with pytest.raises(ValueError, match="exactly one"):
        PassOutcome(call_ids=("c1",))
    with pytest.raises(ValueError, match="exactly one"):
        PassOutcome(call_ids=("c1",), answer=_answer(), failure=LLMFailureKind.TIMEOUT)


def _boq_line() -> BoqLine:
    return BoqLine(
        position=3,
        line_id=make_line_id(3, "01.02", "Concrete C30/37", ""),
        item_no="01.02",
        short="Concrete C30/37",
        long="",
        unit="m³",
        qty="12,5",
        kind=LineKind.ITEM,
        section_path=(),
        extra=(),
        raw_row=("01.02", "Concrete C30/37", "", "m³", "12,5"),
    )


def _decision_input(**overrides: Any) -> DecisionInput:
    values: dict[str, Any] = {
        "line": _boq_line(),
        "haystack": "concrete c30/37",
        "attributes": Attributes(strength_class=STRENGTH),
        "has_supply_marker": False,
        "is_service_unit": False,
        "passes": (PassOutcome(call_ids=("c1",), answer=_answer()),),
        "threshold": Threshold(threshold_id="T1", minimum=_signals()),
    }
    values.update(overrides)
    return DecisionInput(**values)


def test_decision_input() -> None:
    decision_input = _decision_input()
    assert decision_input.line_failure is None
    assert isinstance(hash(decision_input), int)
    for reason in (ReasonCode.LLM_UNAVAILABLE, ReasonCode.BUDGET_CAP):
        assert _decision_input(line_failure=reason).line_failure == reason
    with pytest.raises(ValueError, match="line_failure"):
        _decision_input(line_failure=ReasonCode.HEADER)


def test_rule_table() -> None:
    assert [member.value for member in Rule] == [
        "D0",
        "D0a",
        "D0b",
        "D1",
        "D1b",
        "D2",
        "D3",
        "D4",
        "D5",
        "D5a",
        "D6",
        "D7",
        "D8",
        "D8a",
        "D9",
        "D10",
    ]
    assert set(RULE_DECISIONS) == set(Rule)
    skip_rules = {rule for rule, decision in RULE_DECISIONS.items() if decision == "not_a_material"}
    assert skip_rules == {Rule.D0, Rule.D0A, Rule.D2}
    assert {rule for rule, decision in RULE_DECISIONS.items() if decision == "matched"} == {Rule.D9}


def test_line_decision_matched() -> None:
    decision = LineDecision(
        rule=Rule.D9,
        decision=Decision.MATCHED,
        reason="SIGNAL:T1",
        row=ROWS[1],
        top1="T01.U01.S01",
        call_ids=("c1", "c2"),
        signals=_signals(),
    )
    assert decision.top2 == ""
    assert isinstance(hash(decision), int)
    degraded = dataclasses.replace(decision, reason="ENSEMBLE_DEGRADED:SIGNAL:T1")
    assert degraded.decision == Decision.MATCHED


def test_line_decision_rule_decided_defaults() -> None:
    decision = LineDecision(rule=Rule.D0, decision=Decision.NOT_A_MATERIAL, reason="HEADER")
    assert (decision.row, decision.top1, decision.top2, decision.call_ids) == (None, "", "", ())
    assert decision.signals is None


@pytest.mark.parametrize(
    "values",
    [
        {"rule": Rule.D9, "decision": Decision.MATCHED, "reason": "SIGNAL:T1"},
        {"rule": Rule.D9, "decision": Decision.MATCHED, "reason": "LOW_SIGNAL:v", "row": ROWS[1]},
        {
            "rule": Rule.D10,
            "decision": Decision.NEEDS_REVIEW,
            "reason": "LOW_SIGNAL:v",
            "row": ROWS[1],
        },
        {"rule": Rule.D3, "decision": Decision.NOT_A_MATERIAL, "reason": "G2_SERVICE"},
        {"rule": Rule.D2, "decision": Decision.NOT_A_MATERIAL, "reason": "NM_UNCONFIRMED"},
        {"rule": Rule.D2, "decision": Decision.NEEDS_REVIEW, "reason": "G2_SERVICE"},
        {"rule": Rule.D1, "decision": Decision.NEEDS_REVIEW, "reason": "LLM_FAILURE:bogus"},
        {"rule": Rule.D1, "decision": Decision.NEEDS_REVIEW, "reason": "TRUNCATED"},
    ],
)
def test_line_decision_rejects_inconsistent_values(values: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="decision"):
        LineDecision(**values)


class _FirstRowProvider:
    def __init__(self, library: Library) -> None:
        self._library = library

    def candidates(self, lines: Sequence[BoqLine]) -> tuple[LibraryRow, ...]:
        return self._library.rows[:1] if lines else ()


def test_candidate_provider_protocol() -> None:
    provider: CandidateProvider = _FirstRowProvider(_library())
    assert provider.candidates([_boq_line()]) == (ROWS[0],)
    assert provider.candidates([]) == ()
