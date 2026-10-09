import csv
import hashlib
import io
import logging
import random
import re
from collections import Counter
from pathlib import Path

import pytest

from oris_matcher.domain import library as library_module
from oris_matcher.domain.attributes import Attributes
from oris_matcher.domain.library import (
    CHARS_PER_TOKEN_ESTIMATE,
    LIBRARY_HARD_MAX_TOKENS,
    WHOLE_LIBRARY_MAX_TOKENS,
    Library,
    LibraryError,
    LibraryTooLargeError,
    load_library,
    make_row_id,
    no_attributes,
)
from oris_matcher.domain.normalize import normalize
from oris_matcher.settings import NEVER_MATCH_FILE, NeverMatchPattern, load_never_match

REPO_ROOT = Path(__file__).resolve().parents[1]
GLOBAL_LIBRARY = REPO_ROOT / "data" / "oris_materials_global.csv"
FR_LIBRARY = REPO_ROOT / "data" / "oris_materials_fr.csv"
REAL_LIBRARIES = (GLOBAL_LIBRARY, FR_LIBRARY)
EXPECTED_ROWS = {GLOBAL_LIBRARY: 342, FR_LIBRARY: 70}
PLACEHOLDER_ROWS = {GLOBAL_LIBRARY: 6, FR_LIBRARY: 2}
STRUCTURE_COUNTS = {GLOBAL_LIBRARY: (21, 53, 45, 70), FR_LIBRARY: (0, 26, 0, 7)}
SHUFFLE_SEEDS = (1, 2, 3)
HEADER = '"material_type","material_usage","material_subtype"'
MIN_GREP_LENGTH = 10
SRC_DIR = REPO_ROOT / "src"
NEVER_MATCH = load_never_match(REPO_ROOT / "config" / NEVER_MATCH_FILE).patterns


def _load(path: Path, **kwargs: object) -> Library:
    return load_library(path.read_bytes(), NEVER_MATCH, **kwargs)  # type: ignore[arg-type]


def _csv_bytes(rows: list[tuple[str, str, str]], header: str = HEADER) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return (header + "\n" + buffer.getvalue()).encode("utf-8")


def _real_rows(path: Path) -> tuple[str, list[str]]:
    text = path.read_bytes().decode("utf-8-sig")
    header, *body = text.splitlines(keepends=True)
    return header, body


def _shuffled(path: Path, seed: int) -> bytes:
    header, body = _real_rows(path)
    body = [line if line.endswith("\n") else line + "\n" for line in body]
    random.Random(seed).shuffle(body)
    return (header + "".join(body)).encode("utf-8")


def _id_to_code(library: Library) -> dict[str, str]:
    return {row.row_id: row.code for row in library.rows}


SMALL = [
    ("Concrete", "for footings", ""),
    ("Concrete", "for footings", "C30/37"),
    ("Concrete", "for footings", "C25/30"),
    ("Concrete", "for walls", "C30/37"),
    ("Concrete", "for slabs", ""),
    ("Steel", "Rebar", "B500B"),
    ("Steel", "Custom material (Carbon impact in tonne)", ""),
]


@pytest.mark.parametrize("path", REAL_LIBRARIES)
def test_real_library_loads_with_file_hash_and_no_collisions(path: Path) -> None:
    library = _load(path)
    assert len(library.rows) == EXPECTED_ROWS[path]
    assert library.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len({row.row_id for row in library.rows}) == len(library.rows)
    assert [row.code for row in library.rows] == sorted(row.code for row in library.rows)


@pytest.mark.parametrize("path", REAL_LIBRARIES)
@pytest.mark.parametrize("seed", SHUFFLE_SEEDS)
def test_reordering_the_file_never_changes_ids_or_codes(path: Path, seed: int) -> None:
    original = _load(path)
    shuffled = load_library(_shuffled(path, seed), NEVER_MATCH)
    assert shuffled.sha256 != original.sha256
    assert _id_to_code(shuffled) == _id_to_code(original)
    assert shuffled.rows == original.rows
    assert shuffled.mixed_parents == original.mixed_parents
    assert shuffled.sole_children == original.sole_children
    assert shuffled.confusable_groups == original.confusable_groups
    assert shuffled.never_match_codes == original.never_match_codes


def test_row_id_is_over_raw_strings() -> None:
    assert make_row_id("x", "u", "s") != make_row_id("x ", "u", "s")
    library = load_library(_csv_bytes([("x", "u", ""), ("x ", "u", "")]))
    assert len({row.row_id for row in library.rows}) == 2
    assert len({row.code for row in library.rows}) == 2
    assert {row.material_type for row in library.rows} == {"x", "x "}


def test_row_id_is_short_sha256_of_the_three_raw_strings() -> None:
    joined = "\x1f".join(("Concrete", "for footings", "C30/37"))
    expected = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    assert make_row_id("Concrete", "for footings", "C30/37") == expected


def test_codes_on_sorted_normalised_strings_and_blank_leaf_s00() -> None:
    library = load_library(_csv_bytes(SMALL), NEVER_MATCH)
    codes = {
        (row.material_type, row.material_usage, row.material_subtype): row.code
        for row in library.rows
    }
    assert codes[("Concrete", "for footings", "")] == "T01.U01.S00"
    assert codes[("Concrete", "for footings", "C25/30")] == "T01.U01.S01"
    assert codes[("Concrete", "for footings", "C30/37")] == "T01.U01.S02"
    assert codes[("Concrete", "for slabs", "")] == "T01.U02.S00"
    assert codes[("Concrete", "for walls", "C30/37")] == "T01.U03.S01"
    assert codes[("Steel", "Custom material (Carbon impact in tonne)", "")] == "T02.U01.S00"
    assert codes[("Steel", "Rebar", "B500B")] == "T02.U02.S01"
    assert library.by_code["T01.U01.S00"].is_blank_leaf


def test_codes_ignore_case_and_spacing_for_order() -> None:
    library = load_library(_csv_bytes([("beta", "u", "s"), ("ALPHA", "u", "s")]))
    assert library.by_code["T01.U01.S01"].material_type == "ALPHA"


def test_derived_structure_on_a_small_library() -> None:
    library = load_library(_csv_bytes(SMALL), NEVER_MATCH)
    assert dict(library.mixed_parents) == {"T01.U01.S00": ("T01.U01.S01", "T01.U01.S02")}
    assert library.sole_children == frozenset(
        {"T01.U02.S00", "T01.U03.S01", "T02.U01.S00", "T02.U02.S01"}
    )
    assert library.confusable_groups == (("T01.U01.S02", "T01.U03.S01"),)
    assert library.never_match_codes == frozenset({"T02.U01.S00"})


def test_no_never_match_without_patterns() -> None:
    assert load_library(_csv_bytes(SMALL)).never_match_codes == frozenset()


def test_never_match_pattern_searches_its_own_field() -> None:
    pattern = NeverMatchPattern(field="material_subtype", regex="C30")
    library = load_library(_csv_bytes(SMALL), [pattern])
    assert library.never_match_codes == frozenset({"T01.U01.S02", "T01.U03.S01"})


@pytest.mark.parametrize("path", REAL_LIBRARIES)
def test_derived_structure_on_real_libraries(path: Path) -> None:
    library = _load(path)
    children = Counter(row.parent_code for row in library.rows)
    for blank, siblings in library.mixed_parents.items():
        assert library.by_code[blank].is_blank_leaf
        assert siblings
        parent = library.by_code[blank].parent_code
        assert all(library.by_code[code].parent_code == parent for code in siblings)
        assert not any(library.by_code[code].is_blank_leaf for code in siblings)
    blank_with_siblings = {
        row.code for row in library.rows if row.is_blank_leaf and children[row.parent_code] > 1
    }
    assert set(library.mixed_parents) == blank_with_siblings
    assert library.sole_children == {
        row.code for row in library.rows if children[row.parent_code] == 1
    }
    for group in library.confusable_groups:
        group_rows = [library.by_code[code] for code in group]
        assert len(group) >= 2
        assert len({code.split(".")[0] for code in group}) == 1
        assert len({normalize(row.material_subtype) for row in group_rows}) == 1
        assert normalize(group_rows[0].material_subtype)


@pytest.mark.parametrize("path", REAL_LIBRARIES)
def test_real_library_never_match_rows_are_the_placeholders(path: Path) -> None:
    library = _load(path)
    assert len(library.never_match_codes) == PLACEHOLDER_ROWS[path]
    for code in library.never_match_codes:
        assert library.by_code[code].material_usage.startswith("Custom material (")


@pytest.mark.parametrize("path", REAL_LIBRARIES)
def test_real_library_structure_counts_are_pinned(path: Path) -> None:
    library = _load(path)
    counts = (
        len(library.mixed_parents),
        len(library.sole_children),
        len(library.confusable_groups),
        sum(row.is_blank_leaf for row in library.rows),
    )
    assert counts == STRUCTURE_COUNTS[path]


def test_bom_quoted_headers_and_extra_columns() -> None:
    data = (
        b'\xef\xbb\xbf"material_subtype","note","material_type","material_usage"\n"s","n","t","u"\n'
    )
    library = load_library(data)
    row = library.rows[0]
    assert (row.material_type, row.material_usage, row.material_subtype) == ("t", "u", "s")


def test_semicolon_delimiter() -> None:
    data = "material_type;material_usage;material_subtype\nBéton;pour semelles;C30/37\n"
    library = load_library(data.encode("utf-8"))
    assert library.rows[0].material_type == "Béton"


def test_fully_empty_rows_are_skipped() -> None:
    data = (HEADER + "\nt,u,s\n,,\n\nt,u,\n").encode("utf-8")
    assert len(load_library(data).rows) == 2


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b'"material_type","material_usage"\n"t","u"\n', "material_subtype"),
        (b"", "header"),
        ((HEADER + "\n").encode("utf-8"), "no rows"),
        ((HEADER + "\n\n,,\n").encode("utf-8"), "no rows"),
        ((HEADER + ',"material_type"\nt,u,s,x\n').encode("utf-8"), "more than once"),
        (
            b"material_type,MATERIAL_TYPE ,material_usage,material_subtype\nt,t,u,s\n",
            "more than once",
        ),
        ((HEADER + "\n,u,s\n").encode("utf-8"), "material_type"),
        ((HEADER + "\nt,,s\n").encode("utf-8"), "material_usage"),
        ((HEADER + "\nt,u,\nt,u, \n").encode("utf-8"), "blank"),
        ((HEADER + "\nt,u,s,extra\n").encode("utf-8"), "fields"),
        ((HEADER + "\nt,u\n").encode("utf-8"), "fields"),
        ("material_type,material_usage,material_subtype\nB\xe9ton,u,s\n".encode("cp1252"), "UTF-8"),
    ],
)
def test_invalid_libraries_raise(data: bytes, message: str) -> None:
    with pytest.raises(LibraryError, match=message):
        load_library(data)


def test_row_id_collision_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_module, "ROW_ID_LENGTH", 1)
    rows = [("t", "u", f"s{index}") for index in range(20)]
    with pytest.raises(LibraryError, match="collision"):
        load_library(_csv_bytes(rows))


def test_adding_a_row_keeps_existing_ids() -> None:
    header, body = _real_rows(GLOBAL_LIBRARY)
    extended = (header + "".join(body).rstrip("\n") + "\nAaa new type,new usage,\n").encode()
    original = load_library(GLOBAL_LIBRARY.read_bytes())
    grown = load_library(extended)
    assert {row.row_id for row in original.rows} < {row.row_id for row in grown.rows}
    assert len(grown.rows) == len(original.rows) + 1


def test_normalized_text_and_injected_extractor() -> None:
    seen: list[str] = []

    def extract(text: str) -> Attributes:
        seen.append(text)
        return Attributes(diameter_mm=100) if "ø100" in text else Attributes()

    library = load_library(
        _csv_bytes([("Pipe", "Drainage", "Ø100"), ("Pipe", "Drainage", "")]), extract=extract
    )
    row = library.by_code["T01.U01.S01"]
    assert row.normalized_text == "pipe drainage ø100"
    assert row.attributes == Attributes(diameter_mm=100)
    assert library.by_code["T01.U01.S00"].normalized_text == "pipe drainage"
    assert sorted(seen) == ["pipe drainage", "pipe drainage ø100"]


def test_no_op_extractor_is_an_explicit_opt_in() -> None:
    assert no_attributes("béton c30/37") == Attributes()
    library = load_library(_csv_bytes(SMALL), extract=no_attributes)
    assert all(row.attributes == Attributes() for row in library.rows)


def test_default_extractor_is_the_line_extractor() -> None:
    library = load_library(GLOBAL_LIBRARY.read_bytes())
    assert sum(row.attributes != Attributes() for row in library.rows) > len(library.rows) // 2
    concrete = [row for row in library.rows if row.material_subtype == "C30/37"]
    assert concrete
    assert all(row.attributes.strength_class == (30, 37) for row in concrete)
    small = load_library(_csv_bytes(SMALL))
    assert small.by_code["T01.U01.S02"].attributes.strength_class == (30, 37)


SIBLINGS = [
    ("Asphalt", "binder course", ""),
    ("Asphalt", "binder course", "AC - HMA"),
    ("Asphalt", "binder course", "AC - HMA 30% RAP"),
    ("Asphalt", "binder course", "AC - HMA virgin"),
    ("Asphalt", "wearing course", "AC - HMA"),
]


def test_implicit_zero_applies_only_on_a_checked_catalogue() -> None:
    plain = load_library(_csv_bytes(SIBLINGS))
    checked = load_library(_csv_bytes(SIBLINGS), catalogue="global")
    unchecked = load_library(_csv_bytes(SIBLINGS), catalogue="fr")
    assert plain == unchecked
    assert plain.by_code["T01.U01.S01"].attributes.recycled_pct is None
    assert checked.by_code["T01.U01.S00"].attributes.recycled_pct == 0
    assert checked.by_code["T01.U01.S01"].attributes.recycled_pct == 0
    assert checked.by_code["T01.U01.S02"].attributes.recycled_pct == 30
    assert checked.by_code["T01.U01.S03"].attributes.recycled_pct == 0
    assert checked.by_code["T01.U02.S01"].attributes.recycled_pct is None
    hma = checked.by_code["T01.U01.S01"].attributes
    assert hma.process == plain.by_code["T01.U01.S01"].attributes.process


def test_implicit_zero_on_the_global_library() -> None:
    library = _load(GLOBAL_LIBRARY, catalogue="global")
    hma = library.by_code["T04.U01.S01"]
    assert hma.material_subtype == "Asphalt Concrete (AC) - HMA"
    assert hma.attributes.recycled_pct == 0
    assert library.by_code["T04.U01.S00"].attributes.recycled_pct == 0
    assert library.by_code["T04.U01.S02"].attributes.recycled_pct == 30
    assert _load(GLOBAL_LIBRARY).by_code["T04.U01.S01"].attributes.recycled_pct is None


def test_blank_leaf_survives_a_three_digit_subtype_width() -> None:
    rows = [("T", "U", "")] + [("T", "U", f"s{index:03d}") for index in range(100)]
    library = load_library(_csv_bytes(rows))
    blank = library.rows[0]
    assert blank.code == "T01.U01.S000"
    assert blank.is_blank_leaf
    assert not library.by_code["T01.U01.S100"].is_blank_leaf
    assert sum(row.is_blank_leaf for row in library.rows) == 1
    specific = tuple(row.code for row in library.rows[1:])
    assert dict(library.mixed_parents) == {"T01.U01.S000": specific}


def test_exact_duplicate_rows_are_dropped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        library = load_library((HEADER + "\nt,u,s\nt,u,s\nt,u,\n").encode("utf-8"))
    deduplicated = load_library((HEADER + "\nt,u,s\nt,u,\n").encode("utf-8"))
    assert library.rows == deduplicated.rows
    assert any("duplicate" in record.getMessage() for record in caplog.records)


def test_trailing_blank_cells_are_tolerated() -> None:
    data = (HEADER + "\nt,u,s,,\nt,u,\n").encode("utf-8")
    assert len(load_library(data).rows) == 2


def test_measure_hook_records_rendered_tokens() -> None:
    measured: list[Library] = []

    def measure(library: Library) -> int:
        measured.append(library)
        return 1234

    library = load_library(_csv_bytes(SMALL), measure=measure)
    assert library.rendered_tokens == 1234
    assert not library.rendered_tokens_estimated
    assert len(measured) == 1


def test_measure_hook_records_one_count_per_rendering() -> None:
    counts = {"v1:canonical": 900, "v1:reverse": 901, "b2:canonical": 700}
    library = load_library(_csv_bytes(SMALL), measure=lambda _: counts)
    assert dict(library.rendered_tokens_by_variant) == counts
    assert library.rendered_tokens == 901
    assert not library.rendered_tokens_estimated


def test_without_measure_the_tiers_use_a_conservative_estimate() -> None:
    library = load_library(_csv_bytes(SMALL))
    assert library.rendered_tokens_estimated
    assert library.rendered_tokens is not None
    raw_chars = sum(
        len(row.code) + len(row.material_type) + len(row.material_usage) + len(row.material_subtype)
        for row in library.rows
    )
    assert library.rendered_tokens * CHARS_PER_TOKEN_ESTIMATE >= raw_chars
    assert dict(library.rendered_tokens_by_variant) == {}


@pytest.mark.parametrize("path", REAL_LIBRARIES)
def test_real_libraries_estimate_inside_the_whole_library_tier(path: Path) -> None:
    library = _load(path)
    assert library.rendered_tokens is not None
    assert library.rendered_tokens <= WHOLE_LIBRARY_MAX_TOKENS


def test_estimate_refuses_an_oversized_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_module, "LIBRARY_HARD_MAX_TOKENS", 1)
    with pytest.raises(LibraryTooLargeError, match="estimated"):
        load_library(_csv_bytes(SMALL))


def test_whole_library_tier_boundary_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        library = load_library(_csv_bytes(SMALL), measure=lambda _: WHOLE_LIBRARY_MAX_TOKENS)
    assert library.rendered_tokens == WHOLE_LIBRARY_MAX_TOKENS
    assert not caplog.records


def test_measure_hook_warns_over_the_whole_library_tier(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        library = load_library(_csv_bytes(SMALL), measure=lambda _: WHOLE_LIBRARY_MAX_TOKENS + 1)
    assert library.rendered_tokens == WHOLE_LIBRARY_MAX_TOKENS + 1
    assert any("cache" in record.getMessage() for record in caplog.records)


def test_hard_tier_boundary_warns_but_loads(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        library = load_library(_csv_bytes(SMALL), measure=lambda _: LIBRARY_HARD_MAX_TOKENS)
    assert library.rendered_tokens == LIBRARY_HARD_MAX_TOKENS
    assert any("cache" in record.getMessage() for record in caplog.records)


def test_measure_hook_refuses_an_oversized_library() -> None:
    with pytest.raises(LibraryTooLargeError, match="HybridRetriever"):
        load_library(_csv_bytes(SMALL), measure=lambda _: LIBRARY_HARD_MAX_TOKENS + 1)


def test_library_module_does_no_file_io() -> None:
    source = Path(library_module.__file__).read_text(encoding="utf-8")
    for call in ("read_bytes", "read_text", "open("):
        assert call not in source


def _global_strings() -> set[str]:
    with GLOBAL_LIBRARY.open(encoding="utf-8-sig", newline="") as handle:
        values = {value.strip() for row in csv.DictReader(handle) for value in row.values()}
    return {value.casefold() for value in values if len(value) >= MIN_GREP_LENGTH}


def _src_texts() -> list[tuple[Path, str]]:
    texts: list[tuple[Path, str]] = []
    for path in sorted(SRC_DIR.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            texts.append((path, path.read_bytes().decode("utf-8")))
        except UnicodeDecodeError:
            continue
    return texts


def test_no_global_library_strings_in_src() -> None:
    strings = _global_strings()
    assert strings
    texts = _src_texts()
    assert any(path.suffix == ".txt" for path, _ in texts)
    offenders: list[tuple[str, str]] = []
    for path, text in texts:
        flat = re.sub(r"\s+", " ", text).casefold()
        offenders.extend((path.name, value) for value in strings if value in flat)
    assert offenders == []
