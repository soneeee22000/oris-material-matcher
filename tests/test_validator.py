from typing import Any

import pytest

from oris_matcher.domain.attributes import Attributes
from oris_matcher.domain.library import Library, LibraryRow
from oris_matcher.domain.validator import (
    AnswerCheck,
    CodeStatus,
    check_answer,
    check_code,
    is_valid_code,
    resolve_row,
)
from oris_matcher.prompts.v1.schema import LineAnswer

SHA = "b" * 64


def _row(code: str, subtype: str) -> LibraryRow:
    return LibraryRow(
        row_id=f"id-{code}",
        code=code,
        material_type="Concrete ",
        material_usage="for footings",
        material_subtype=subtype,
        normalized_text=f"concrete for footings {subtype}".strip(),
        attributes=Attributes(),
    )


ROWS = (
    _row("T01.U01.S00", ""),
    _row("T01.U01.S01", "C30/37 "),
    _row("T02.U01.S01", "Rebar"),
)
LIBRARY = Library(
    rows=ROWS,
    sha256=SHA,
    never_match_codes=frozenset(),
    mixed_parents={"T01.U01.S00": ("T01.U01.S01",)},
    sole_children=frozenset({"T02.U01.S01"}),
    confusable_groups=(),
)


def _answer(**overrides: Any) -> LineAnswer:
    values: dict[str, Any] = {
        "id": "L0",
        "evidence": "concrete",
        "element_or_application": "footings",
        "material_family": "concrete",
        "kind": "material",
        "nm_category": "",
        "top1": "T01.U01.S01",
        "top2": "",
        "confidence": 90,
        "self_reported_candidate_gap": "clear",
    }
    values.update(overrides)
    return LineAnswer.model_validate(values)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("T01.U01.S01", CodeStatus.VALID),
        ("T01.U01.S00", CodeStatus.VALID),
        ("", CodeStatus.EMPTY),
        ("t01.u01.s01", CodeStatus.MALFORMED),
        (" T01.U01.S01", CodeStatus.MALFORMED),
        ("T01.U01.S01 ", CodeStatus.MALFORMED),
        ("T01.U01", CodeStatus.MALFORMED),
        ("T1.U01.S01", CodeStatus.MALFORMED),
        ("Concrete for footings", CodeStatus.MALFORMED),
        ("T01.U09.S01", CodeStatus.UNKNOWN_PARENT),
        ("T09.U01.S00", CodeStatus.UNKNOWN_PARENT),
        ("T02.U01.S00", CodeStatus.UNKNOWN_CODE),
        ("T01.U01.S07", CodeStatus.UNKNOWN_CODE),
    ],
)
def test_check_code(code: str, expected: CodeStatus) -> None:
    assert check_code(code, LIBRARY) == expected
    assert is_valid_code(code, LIBRARY) == (expected == CodeStatus.VALID)


def test_resolve_row_returns_the_library_row_itself() -> None:
    row = resolve_row("T01.U01.S01", LIBRARY)
    assert row is ROWS[1]
    assert row.material_type == "Concrete "
    assert row.material_subtype == "C30/37 "


@pytest.mark.parametrize("code", ["", "t01.u01.s01", "T01.U09.S01", "T02.U01.S00"])
def test_resolve_row_refuses_codes_outside_the_library(code: str) -> None:
    with pytest.raises(KeyError):
        resolve_row(code, LIBRARY)


def test_check_answer_valid_with_and_without_top2() -> None:
    assert check_answer(_answer(), LIBRARY) == AnswerCheck(CodeStatus.VALID, CodeStatus.EMPTY)
    assert check_answer(_answer(), LIBRARY).is_closed_world
    with_top2 = check_answer(_answer(top2="T02.U01.S01"), LIBRARY)
    assert with_top2 == AnswerCheck(CodeStatus.VALID, CodeStatus.VALID)
    assert with_top2.is_closed_world


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"top1": ""}, AnswerCheck(CodeStatus.EMPTY, CodeStatus.EMPTY)),
        ({"top1": "T01.U01.s01"}, AnswerCheck(CodeStatus.MALFORMED, CodeStatus.EMPTY)),
        ({"top2": "T07.U01.S01"}, AnswerCheck(CodeStatus.VALID, CodeStatus.UNKNOWN_PARENT)),
        ({"top2": "x"}, AnswerCheck(CodeStatus.VALID, CodeStatus.MALFORMED)),
    ],
)
def test_check_answer_flags_closed_world_violations(
    overrides: dict[str, Any], expected: AnswerCheck
) -> None:
    result = check_answer(_answer(**overrides), LIBRARY)
    assert result == expected
    assert not result.is_closed_world


def test_model_labels_are_never_read() -> None:
    answer = _answer(material_family="Steel", element_or_application="Rebar")
    assert resolve_row(answer.top1, LIBRARY).material_type == "Concrete "
