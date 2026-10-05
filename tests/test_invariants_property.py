import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from oris_matcher.domain.attributes import Attributes
from oris_matcher.domain.batching import Batch, plan_batches
from oris_matcher.domain.boq import BoqLine, LineKind, SectionHeader, make_line_id
from oris_matcher.domain.decision import (
    NOT_A_MATERIAL_REASONS,
    Decision,
    DecisionInput,
    LineDecision,
    LLMFailureKind,
    PassOutcome,
    ReasonCode,
    Rule,
    candidate_thresholds,
    decide,
    decide_b2,
    is_frozen_reason,
    is_service_unit,
    make_haystack,
)
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.prompts.v1.schema import LineAnswer
from oris_matcher.settings import load_service_units, load_unit_aliases

CONFIG = Path(__file__).resolve().parents[1] / "config"
SERVICE_UNITS = load_service_units(CONFIG / "service_units.yaml").units
UNIT_ALIASES = load_unit_aliases(CONFIG / "unit_aliases.yaml").aliases
MEASURED_UNITS = sorted(
    unit
    for canonical, aliases in UNIT_ALIASES.items()
    if canonical not in SERVICE_UNITS
    for unit in (canonical, *aliases)
)
SHA = "d" * 64


def _row(code: str, material_type: str, subtype: str, **attributes: Any) -> LibraryRow:
    return LibraryRow(
        row_id=f"id-{code}",
        code=code,
        material_type=material_type,
        material_usage="usage ",
        material_subtype=subtype,
        normalized_text=f"{material_type} usage {subtype}".strip().lower(),
        attributes=Attributes(**attributes),
    )


ROWS = (
    _row("T01.U01.S00", "Concrete", ""),
    _row("T01.U01.S01", "Concrete", "C30/37 ", strength_class=(30, 37)),
    _row("T01.U01.S02", "Concrete", "C25/30", strength_class=(25, 30)),
    _row("T01.U02.S00", "Concrete", "", strength_class=(40, 50)),
    _row("T02.U01.S01", "Steel", "ø12", diameter_mm=12),
)
LIBRARY = Library(
    rows=ROWS,
    sha256=SHA,
    never_match_codes=frozenset({"T01.U02.S00"}),
    mixed_parents={"T01.U01.S00": ("T01.U01.S01", "T01.U01.S02")},
    sole_children=frozenset({"T01.U02.S00", "T02.U01.S01"}),
    confusable_groups=(),
)
CODES = [row.code for row in ROWS]
CORRUPT_CODES = ["", "t01.u01.s01", "T01.U01.S01 ", "T09.U01.S01", "T01.U01.S09", "Concrete"]
THRESHOLDS = list(candidate_thresholds(2))
PATHS = [(), (SectionHeader("1", "Concrete works"),), (SectionHeader("2", "Steel"),)]
SHORTS = ["Concrete C30/37 footings", "Labour gang", "Rebar ø12", "Hire of crane", ""]
ATTRIBUTES = [
    Attributes(),
    Attributes(strength_class=(30, 37)),
    Attributes(strength_class=(25, 30)),
    Attributes(diameter_mm=12),
    Attributes(grading=frozenset({"0/20"})),
]
EVIDENCE = ["", "concrete", "concrete", "C30/37", "labour", "ø12", "not in this line", "crane"]
ANY_UNITS = [*MEASURED_UNITS, *sorted(SERVICE_UNITS), "", " "]


def _answer_strategy(ids: list[str]) -> st.SearchStrategy[LineAnswer]:
    def build(values: dict[str, Any]) -> LineAnswer:
        return LineAnswer.model_validate(
            {"element_or_application": "", "material_family": "", **values}
        )

    return st.fixed_dictionaries(
        {
            "id": st.sampled_from(ids),
            "evidence": st.sampled_from(EVIDENCE),
            "kind": st.sampled_from(["material", "material", "non_material", "no_equivalent"]),
            "nm_category": st.sampled_from(["", "labour", "hire"]),
            "top1": st.sampled_from([*CODES, *CODES, *CORRUPT_CODES]),
            "top2": st.sampled_from(["", *CODES, *CORRUPT_CODES]),
            "confidence": st.integers(min_value=0, max_value=100),
            "self_reported_candidate_gap": st.sampled_from(["decisive", "tossup"]),
        }
    ).map(build)


def _line(position: int, kind: LineKind, unit: str, short: str, path_index: int) -> BoqLine:
    item_no = f"{position:02d}"
    return BoqLine(
        position=position,
        line_id=make_line_id(position, item_no, short, ""),
        item_no=item_no,
        short=short,
        long="",
        unit=unit,
        qty="1" if unit.strip() else "",
        kind=kind,
        section_path=PATHS[path_index],
        extra=(),
        raw_row=(item_no, short, "", unit, ""),
    )


LINE_SPECS = st.tuples(
    st.sampled_from([*LineKind, *[LineKind.ITEM] * 8]),
    st.sampled_from([*ANY_UNITS, "m³", "m³", "day"]),
    st.sampled_from(SHORTS),
    st.integers(min_value=0, max_value=len(PATHS) - 1),
)


def _attribute(batch: Batch, answers: list[LineAnswer], call_id: str) -> list[PassOutcome]:
    """Attribute one pass's batch response to its lines via ``Batch.line_for`` (§11.3)."""
    mine: dict[str, set[LineAnswer]] = {line.line_id: set() for line in batch.lines}
    for answer in answers:
        line = batch.line_for(answer.id)
        if line is not None:
            mine[line.line_id].add(answer)
    return [_outcome(mine[line.line_id], call_id) for line in batch.lines]


def _outcome(answers: set[LineAnswer], call_id: str) -> PassOutcome:
    if not answers:
        return PassOutcome(call_ids=(call_id,), failure=LLMFailureKind.MISSING_ITEM)
    if len(answers) > 1:
        return PassOutcome(call_ids=(call_id,), failure=LLMFailureKind.DUPLICATE_CONFLICT)
    return PassOutcome(call_ids=(call_id,), answer=next(iter(answers)))


def _input(line: BoqLine, passes: tuple[PassOutcome, ...], draw: Any) -> DecisionInput:
    return DecisionInput(
        line=line,
        haystack=make_haystack(line),
        attributes=draw(st.sampled_from(ATTRIBUTES)),
        has_supply_marker=draw(st.booleans()),
        is_service_unit=is_service_unit(line.unit, SERVICE_UNITS),
        passes=passes,
        threshold=draw(st.sampled_from(THRESHOLDS)),
        line_failure=draw(
            st.sampled_from([*[None] * 18, ReasonCode.LLM_UNAVAILABLE, ReasonCode.BUDGET_CAP])
        ),
    )


CORRUPTIONS = ["clean", "clean", "clean", "drop", "duplicate", "swap", "unknown", "fail"]


def _corrupt(answers: list[LineAnswer], mode: str, draw: Any) -> list[LineAnswer]:
    """Corrupt one pass's batch response the way FakeLLM's fault modes do."""
    if mode == "drop":
        return answers[1:]
    if mode == "duplicate":
        return [*answers, answers[0].model_copy(update={"confidence": 1})]
    if mode == "swap" and len(answers) > 1:
        first, second = answers[0], answers[1]
        return [first.model_copy(update={"id": second.id}), second, *answers[2:]]
    if mode == "unknown":
        return [*answers, draw(_answer_strategy(["L9999"]))]
    return answers


def _pass_outcomes(batch: Batch, call_id: str, draw: Any) -> list[PassOutcome]:
    mode = draw(st.sampled_from(CORRUPTIONS))
    if mode == "fail":
        failure = draw(st.sampled_from(list(LLMFailureKind)))
        return [PassOutcome(call_ids=(call_id,), failure=failure) for _ in batch.lines]
    answers = [draw(_answer_strategy([transport])) for transport in batch.transport_ids]
    return _attribute(batch, _corrupt(answers, mode, draw), call_id)


def _batch_passes(batch: Batch, index: int, draw: Any) -> dict[str, tuple[PassOutcome, ...]]:
    pass_count = draw(st.integers(min_value=1, max_value=3))
    first = _pass_outcomes(batch, f"b{index}p0", draw)
    columns = [first]
    for pass_no in range(1, pass_count):
        call_id = f"b{index}p{pass_no}"
        if draw(st.booleans()):
            columns.append([dataclasses.replace(o, call_ids=(call_id,)) for o in first])
        else:
            columns.append(_pass_outcomes(batch, call_id, draw))
    return {
        line.line_id: tuple(column[i] for column in columns) for i, line in enumerate(batch.lines)
    }


@st.composite
def run_inputs(draw: Any) -> list[DecisionInput]:
    specs = draw(st.lists(LINE_SPECS, max_size=25))
    lines = [_line(position, *spec) for position, spec in enumerate(specs)]
    by_line: dict[str, tuple[PassOutcome, ...]] = {}
    for index, batch in enumerate(plan_batches(lines, size=draw(st.integers(1, 10)))):
        by_line.update(_batch_passes(batch, index, draw))
    return [_input(line, by_line.get(line.line_id, ()), draw) for line in lines]


def _check_decision(decision: LineDecision) -> None:
    assert is_frozen_reason(decision.reason)
    if decision.decision == Decision.NOT_A_MATERIAL:
        assert decision.reason in NOT_A_MATERIAL_REASONS
        assert decision.rule in {Rule.D0, Rule.D0A, Rule.D2}
    if decision.decision != Decision.MATCHED:
        assert decision.row is None
        return
    assert decision.row is not None
    assert any(decision.row is row for row in LIBRARY.rows)
    assert decision.row is LIBRARY.by_code[decision.top1]


DECIDERS: list[Callable[[DecisionInput, Library], LineDecision]] = [decide, decide_b2]


@settings(max_examples=150, deadline=None)
@given(run_inputs())
def test_run_invariants_under_corrupted_answers(inputs: list[DecisionInput]) -> None:
    for decider in DECIDERS:
        decisions = [decider(decision_input, LIBRARY) for decision_input in inputs]
        output_ids = [decision.line_id for decision in decisions]
        assert output_ids == [decision_input.line.line_id for decision_input in inputs]
        assert len(set(output_ids)) == len(output_ids)
        for decision_input, decision in zip(inputs, decisions, strict=True):
            _check_decision(decision)
            assert decision == decider(decision_input, LIBRARY)


@settings(max_examples=150, deadline=None)
@given(run_inputs())
def test_unanswered_lines_never_match(inputs: list[DecisionInput]) -> None:
    for decision_input in inputs:
        answered = [outcome for outcome in decision_input.passes if outcome.answer is not None]
        decision = decide(decision_input, LIBRARY)
        if not answered or decision_input.line_failure is not None:
            assert decision.decision != Decision.MATCHED


@st.composite
def measured_unit_inputs(draw: Any) -> DecisionInput:
    unit = draw(st.sampled_from(MEASURED_UNITS))
    line = _line(0, draw(st.sampled_from(list(LineKind))), unit, "Labour gang", 1)
    line = dataclasses.replace(line, qty=draw(st.sampled_from(["", "0", "12,5"])))
    answers = draw(st.lists(_answer_strategy(["L0"]), max_size=3))
    return DecisionInput(
        line=line,
        haystack=make_haystack(line),
        attributes=draw(st.sampled_from(ATTRIBUTES)),
        has_supply_marker=draw(st.booleans()),
        is_service_unit=is_service_unit(unit, SERVICE_UNITS),
        passes=tuple(PassOutcome(call_ids=(f"c{n}",), answer=a) for n, a in enumerate(answers)),
        threshold=draw(st.sampled_from(THRESHOLDS)),
    )


@settings(max_examples=300, deadline=None)
@given(measured_unit_inputs())
def test_measured_unit_never_reaches_not_a_material(decision_input: DecisionInput) -> None:
    assert not decision_input.is_service_unit
    for decider in DECIDERS:
        assert decider(decision_input, LIBRARY).decision != Decision.NOT_A_MATERIAL


def test_measured_units_cover_the_configured_canonicals() -> None:
    assert {"m", "m²", "m³", "t", "kg", "l", "pcs"} <= set(MEASURED_UNITS)
    assert not set(MEASURED_UNITS) & SERVICE_UNITS
