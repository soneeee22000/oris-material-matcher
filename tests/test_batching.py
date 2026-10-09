import pytest
from hypothesis import given
from hypothesis import strategies as st

from oris_matcher.domain.batching import (
    DEFAULT_BATCH_SIZE,
    Batch,
    plan_batches,
    transport_id,
)
from oris_matcher.domain.boq import BoqLine, LineKind, SectionHeader, make_line_id
from oris_matcher.prompts.v1 import render

SECTION_A = (SectionHeader("1", "Earthworks"),)
SECTION_B = (SectionHeader("2", "Concrete"),)
SECTION_B1 = (SectionHeader("2", "Concrete"), SectionHeader("2.1", "Footings"))


def _line(
    position: int,
    kind: LineKind = LineKind.ITEM,
    path: tuple[SectionHeader, ...] = SECTION_A,
) -> BoqLine:
    unit = "" if kind != LineKind.ITEM else "m³"
    return BoqLine(
        position=position,
        line_id=make_line_id(position, str(position), f"text {position}", ""),
        item_no=str(position),
        short=f"text {position}",
        long="",
        unit=unit,
        qty="",
        kind=kind,
        section_path=path,
        extra=(),
        raw_row=(str(position), f"text {position}", "", unit, ""),
    )


def _positions(batches: list[Batch]) -> list[list[int]]:
    return [[line.position for line in batch.lines] for batch in batches]


def test_transport_id_is_position_based() -> None:
    assert transport_id(_line(118)) == "L118"
    assert DEFAULT_BATCH_SIZE == 10


@pytest.mark.parametrize("position", [0, 7, 118, 4096])
def test_transport_id_matches_the_id_sent_to_the_model(position: int) -> None:
    line = _line(position)
    assert render.transport_id(line) == transport_id(line)
    assert all(
        render.line_record(line, profile)["id"] == transport_id(line) for profile in render.Profile
    )


def test_empty_input_gives_no_batches() -> None:
    assert plan_batches([]) == []


def test_contiguous_lines_split_by_size() -> None:
    lines = [_line(position) for position in range(23)]
    batches = plan_batches(lines)
    assert _positions(batches) == [list(range(10)), list(range(10, 20)), list(range(20, 23))]
    assert batches[0].transport_ids == tuple(f"L{position}" for position in range(10))


def test_custom_size() -> None:
    batches = plan_batches([_line(position) for position in range(5)], size=2)
    assert _positions(batches) == [[0, 1], [2, 3], [4]]


@pytest.mark.parametrize("size", [0, -1])
def test_size_must_be_positive(size: int) -> None:
    with pytest.raises(ValueError, match="size"):
        plan_batches([_line(0)], size=size)


def test_never_crosses_a_header_boundary() -> None:
    lines = [
        _line(0, LineKind.HEADER, ()),
        _line(1),
        _line(2),
        _line(3, LineKind.HEADER, ()),
        _line(4, path=SECTION_B),
        _line(5, path=SECTION_B),
    ]
    assert _positions(plan_batches(lines)) == [[1, 2], [4, 5]]


def test_nearest_header_change_splits_even_without_a_header_row() -> None:
    lines = [_line(0, path=SECTION_B), _line(1, path=SECTION_B1), _line(2, path=SECTION_B1)]
    assert _positions(plan_batches(lines)) == [[0], [1, 2]]


@pytest.mark.parametrize("kind", [LineKind.EMPTY_ROW, LineKind.HEADER_UNCONFIRMED])
def test_non_routed_lines_are_skipped_and_break_contiguity(kind: LineKind) -> None:
    lines = [_line(0), _line(1, kind), _line(2)]
    assert _positions(plan_batches(lines)) == [[0], [2]]


def test_position_gap_breaks_contiguity_when_given_routed_lines_only() -> None:
    assert _positions(plan_batches([_line(0), _line(1), _line(4)])) == [[0, 1], [4]]


def test_batch_maps_transport_ids_back_to_lines() -> None:
    lines = [_line(7), _line(8)]
    batch = plan_batches(lines)[0]
    assert batch.line_for("L8") is lines[1]
    assert batch.line_for("L9") is None
    assert batch.line_for("l8") is None


def test_batch_rejects_inconsistent_construction() -> None:
    with pytest.raises(ValueError, match="empty"):
        Batch(lines=())
    with pytest.raises(ValueError, match="routed"):
        Batch(lines=(_line(0, LineKind.HEADER, ()),))


KINDS = st.sampled_from(list(LineKind))
PATHS = st.sampled_from([(), SECTION_A, SECTION_B, SECTION_B1])


@given(
    st.lists(st.tuples(KINDS, PATHS), max_size=60),
    st.integers(min_value=1, max_value=12),
)
def test_batches_partition_routed_lines_in_order(
    specs: list[tuple[LineKind, tuple[SectionHeader, ...]]], size: int
) -> None:
    lines = [_line(position, kind, path) for position, (kind, path) in enumerate(specs)]
    batches = plan_batches(lines, size=size)
    flat = [line for batch in batches for line in batch.lines]
    assert flat == [line for line in lines if line.kind == LineKind.ITEM]
    for batch in batches:
        assert 1 <= len(batch.lines) <= size
        assert len({line.section_path for line in batch.lines}) == 1
        positions = [line.position for line in batch.lines]
        assert positions == list(range(positions[0], positions[0] + len(positions)))
        assert len(set(batch.transport_ids)) == len(batch.lines)
