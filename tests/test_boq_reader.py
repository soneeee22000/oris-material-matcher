import csv
import io
from collections.abc import Callable
from pathlib import Path

import pytest

from oris_matcher.domain.boq import (
    BoqFile,
    BoqLine,
    Column,
    LineKind,
    PathMode,
    SectionHeader,
    make_line_id,
)
from oris_matcher.io.boq_reader import (
    FIELD_CAPS,
    BoqFormatError,
    EncodingFallbackWarning,
    RowCells,
    capped_for_prompt,
    classify_rows,
    derive_section_paths,
    oversized_fields,
    read_boq,
)

ROOT = Path(__file__).resolve().parents[1]
INPUT_EN = ROOT / "input" / "boq_dataset_input_en.csv"
INPUT_FR = ROOT / "input" / "boq_dataset_input_fr.csv"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "boq"
REAL_HEADER_COUNT = 37
REAL_ROW_COUNT = 319
CP1252 = "cp1252"
HEADER_ROW = "Item No.,Short,Long,Unit,Qty\r\n"


def read_rows(*rows: str) -> BoqFile:
    return read_boq((HEADER_ROW + "".join(f"{row}\r\n" for row in rows)).encode("utf-8"))


def fixture_kinds(name: str) -> list[LineKind]:
    return kinds(read_boq(FIXTURES / name))


def kinds(boq: BoqFile) -> list[LineKind]:
    return [line.kind for line in boq.lines]


def path_texts(boq: BoqFile) -> list[tuple[str, ...]]:
    return [tuple(header.text for header in line.section_path) for line in boq.lines]


def rewrite_codes(source: Path, recode: Callable[[list[str]], list[str]]) -> bytes:
    with source.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    codes = recode([row[0] for row in rows[1:]])
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(rows[0])
    writer.writerows([code, *row[1:]] for code, row in zip(codes, rows[1:], strict=True))
    return buffer.getvalue().encode("utf-8")


def renumber(codes: list[str]) -> list[str]:
    counters = [0, 0, 0]
    renumbered = []
    for code in codes:
        depth = 0 if "." not in code else code.rstrip(".").count(".")
        counters[depth] += 1
        counters[depth + 1 :] = [0] * (len(counters) - depth - 1)
        renumbered.append(".".join(str(count) for count in counters[: depth + 1]))
    return renumbered


@pytest.mark.parametrize("source", [INPUT_EN, INPUT_FR])
def test_real_inputs_have_exactly_37_headers(source: Path) -> None:
    boq = read_boq(source)
    assert len(boq.lines) == REAL_ROW_COUNT
    assert kinds(boq).count(LineKind.HEADER) == REAL_HEADER_COUNT
    assert LineKind.HEADER_UNCONFIRMED not in kinds(boq)
    assert LineKind.EMPTY_ROW not in kinds(boq)
    assert boq.encoding == "utf-8"
    assert boq.delimiter == ","
    assert boq.path_mode is PathMode.DERIVED


@pytest.mark.parametrize("source", [INPUT_EN, INPUT_FR])
def test_real_dotless_and_dotted_header_rows(source: Path) -> None:
    boq = read_boq(source)
    by_code = {line.item_no: line for line in boq.lines}
    assert by_code["0"].kind is LineKind.HEADER
    assert by_code["0"].section_path == ()
    assert by_code["00.01."].kind is LineKind.HEADER
    assert [h.item_no for h in by_code["00.01."].section_path] == ["0"]
    assert by_code["00.01.0010."].kind is LineKind.ITEM
    assert [h.item_no for h in by_code["00.01.0010."].section_path] == ["0", "00.01."]
    assert [h.item_no for h in by_code["01.01.0010."].section_path] == ["1", "01.01."]
    assert [h.item_no for h in by_code["09.01.0010."].section_path] == ["9", "09.01."]


@pytest.mark.parametrize("source", [INPUT_EN, INPUT_FR])
def test_every_real_item_has_l0_and_l1_path(source: Path) -> None:
    boq = read_boq(source)
    items = [line for line in boq.lines if line.kind is LineKind.ITEM]
    assert all(len(line.section_path) == 2 for line in items)


def test_real_cells_match_csv_module_exactly() -> None:
    with INPUT_FR.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    boq = read_boq(INPUT_FR)
    assert boq.header == tuple(rows[0])
    for line, row in zip(boq.lines, rows[1:], strict=True):
        assert line.raw_row == tuple(row)
        assert (line.item_no, line.short, line.long, line.unit, line.qty) == tuple(row)
        assert line.line_id == make_line_id(line.position, line.item_no, line.short, line.long)


def test_bytes_and_path_give_same_result() -> None:
    assert read_boq(INPUT_EN.read_bytes()) == read_boq(INPUT_EN)


def test_str_path_is_accepted() -> None:
    assert read_boq(str(INPUT_EN)) == read_boq(INPUT_EN)


def test_renumbered_fr_gives_same_decisions_and_paths() -> None:
    original = read_boq(INPUT_FR)
    renumbered = read_boq(rewrite_codes(INPUT_FR, renumber))
    assert renumbered.lines[0].item_no == "1"
    assert renumbered.lines[1].item_no == "1.1"
    assert renumbered.lines[2].item_no == "1.1.1"
    assert kinds(renumbered) == kinds(original)
    assert path_texts(renumbered) == path_texts(original)
    assert renumbered.path_mode is PathMode.DERIVED


def test_codeless_fr_gives_same_paths_and_never_auto_skips() -> None:
    original = read_boq(INPUT_FR)
    codeless = read_boq(rewrite_codes(INPUT_FR, lambda codes: [""] * len(codes)))
    assert all(line.item_no == "" for line in codeless.lines)
    expected = [
        LineKind.HEADER_UNCONFIRMED if kind is LineKind.HEADER else kind for kind in kinds(original)
    ]
    assert kinds(codeless) == expected
    assert LineKind.HEADER not in kinds(codeless)
    assert path_texts(codeless) == path_texts(original)
    assert codeless.path_mode is PathMode.DERIVED


def test_codeless_material_lines_are_never_confirmed_headers() -> None:
    boq = read_rows(",Concrete C30/37 slab,,,", ",Rebar B500,,,", ",Formwork,,m2,10")
    assert kinds(boq) == [LineKind.HEADER_UNCONFIRMED, LineKind.HEADER_UNCONFIRMED, LineKind.ITEM]


def test_codeless_line_kind_does_not_depend_on_following_lines() -> None:
    alone = read_rows(",Concrete C30/37 slab,,,")
    followed = read_rows(",Concrete C30/37 slab,,,", ",Formwork,,m2,10")
    assert alone.lines[0].kind is LineKind.HEADER_UNCONFIRMED
    assert followed.lines[0].kind is LineKind.HEADER_UNCONFIRMED


def test_codeless_two_level_run_gives_two_level_path() -> None:
    boq = read_rows(",L0,,,", ",L1,,,", ",item,,m,1")
    assert path_texts(boq)[2] == ("L0", "L1")
    assert boq.path_mode is PathMode.DERIVED


def test_codeless_trailing_title_does_not_deepen_paths() -> None:
    boq = read_rows(
        ",L0,,,", ",L1,,,", ",i1,,m,1", ",L1b,,,", ",i2,,m,1", ",T1,,,", ",T2,,,", ",T3,,,"
    )
    assert path_texts(boq)[4] == ("L0", "L1b")
    assert boq.lines[7].kind is LineKind.HEADER_UNCONFIRMED


def test_codeless_row_with_long_text_is_not_context() -> None:
    boq = read_rows(",L0,,,", ",Mat,long text,,", ",item,,m,1")
    assert boq.lines[1].kind is LineKind.HEADER_UNCONFIRMED
    assert path_texts(boq)[2] == ("L0",)


def test_trailing_spaces_and_blank_cells_preserved() -> None:
    boq = read_boq(FIXTURES / "trailing_spaces.csv")
    line = boq.lines[2]
    assert line.short == "bitumen for coating "
    assert line.long == "  long text  "
    assert line.unit == "m2 "
    assert line.qty == "5 "
    assert boq.lines[3].qty == ""
    assert boq.lines[3].kind is LineKind.ITEM


def test_empty_row_and_unconfirmed_header() -> None:
    boq = read_boq(FIXTURES / "trailing_spaces.csv")
    assert boq.lines[4].kind is LineKind.EMPTY_ROW
    assert boq.lines[4].raw_row == ("", "", "", "", "")
    assert boq.lines[5].kind is LineKind.HEADER_UNCONFIRMED


def test_code_that_is_strict_prefix_of_next_code_is_header() -> None:
    data = "Item No.,Short,Long,Unit,Qty\r\nA,Section A,,,\r\nA-1,Sub A1,,,\r\nA-1-1,Item,,m,1\r\n"
    boq = read_boq(data.encode("utf-8"))
    assert kinds(boq) == [LineKind.HEADER, LineKind.HEADER, LineKind.ITEM]
    assert [h.text for h in boq.lines[2].section_path] == ["Section A", "Sub A1"]


@pytest.mark.parametrize(("sibling", "follower"), [("1.1", "1.10"), ("A1", "A10"), ("A1", "A10.1")])
def test_squashed_prefix_without_segment_prefix_is_not_header(sibling: str, follower: str) -> None:
    boq = read_rows(f"{sibling},Note,,,", f"{follower},Item,,m,1")
    assert kinds(boq) == [LineKind.HEADER_UNCONFIRMED, LineKind.ITEM]
    assert boq.lines[1].section_path == ()


def test_separator_only_code_is_not_header() -> None:
    boq = read_rows(".,Note,,,", "1,Item,,m,1")
    assert boq.lines[0].kind is LineKind.HEADER_UNCONFIRMED


def test_equal_code_header_is_not_on_its_own_path() -> None:
    boq = read_rows("0,G,,,", "00.01.,A,,,", "00.01.,B,,,", "00.01.0010.,x,,m,1")
    assert kinds(boq)[:3] == [LineKind.HEADER] * 3
    assert path_texts(boq)[2] == ("G",)
    assert path_texts(boq)[3] == ("G", "B")


def test_whitespace_only_row_is_empty_row_and_kept_verbatim() -> None:
    boq = read_rows("0,G,,,", "  , ,\t,,", "00.01.0010.,x,,m,1")
    assert boq.lines[1].kind is LineKind.EMPTY_ROW
    assert boq.lines[1].raw_row == ("  ", " ", "\t", "", "")


def test_whitespace_only_unit_counts_as_blank() -> None:
    boq = read_rows("0,General,, ,", "00.01.0010.,x,,m,1")
    assert boq.lines[0].kind is LineKind.HEADER
    assert boq.lines[0].unit == " "


def test_filled_extra_column_prevents_empty_row() -> None:
    header = "Pos;N° article;Désignation;Description longue;Unité;Quantité;Remarque\r\n"
    boq = read_boq((header + ";;;;;;note\r\n").encode("utf-8"))
    assert boq.lines[0].kind is LineKind.HEADER_UNCONFIRMED


def test_lf_line_endings_match_crlf() -> None:
    crlf = (FIXTURES / "trailing_spaces.csv").read_bytes()
    assert b"\r\n" in crlf
    assert read_boq(crlf.replace(b"\r\n", b"\n")) == read_boq(crlf)


def _cells(item_no: str, short: str, unit: str = "", qty: str = "") -> RowCells:
    blank = not (item_no + short + unit + qty).strip()
    return RowCells(item_no=item_no, short=short, long="", unit=unit, qty=qty, all_blank=blank)


def test_classify_rows_and_derive_section_paths_directly() -> None:
    rows = [_cells("1", "S1"), _cells("1.1", "S11"), _cells("1.1.1", "x", "m", "1"), _cells("", "")]
    row_kinds = classify_rows(rows)
    assert row_kinds == [LineKind.HEADER, LineKind.HEADER, LineKind.ITEM, LineKind.EMPTY_ROW]
    paths = derive_section_paths(rows, row_kinds)
    assert paths[2] == (SectionHeader("1", "S1"), SectionHeader("1.1", "S11"))
    assert paths[0] == ()
    assert paths[3] == ()


def test_custom_header_pattern() -> None:
    data = "Item No.,Short,Long,Unit,Qty\r\nX9,Odd header,,,\r\nZZ.1,Item,,m,1\r\n"
    assert read_boq(data.encode("utf-8")).lines[0].kind is LineKind.HEADER_UNCONFIRMED
    custom = read_boq(data.encode("utf-8"), header_patterns=(r"^X\d$",))
    assert custom.lines[0].kind is LineKind.HEADER


def test_header_with_equal_next_code_is_not_header() -> None:
    data = "Item No.,Short,Long,Unit,Qty\r\nAB,Note,,,\r\nA.B,Item,,m,1\r\n"
    assert read_boq(data.encode("utf-8")).lines[0].kind is LineKind.HEADER_UNCONFIRMED


def test_no_derivable_path_gives_path_mode_none() -> None:
    data = "Item No.,Short,Long,Unit,Qty\r\nA1,Item one,,m,1\r\nA2,Item two,,m,2\r\n"
    boq = read_boq(data.encode("utf-8"))
    assert boq.path_mode is PathMode.NONE
    assert all(line.section_path == () for line in boq.lines)


def test_semicolon_delimiter_and_utf8_bom() -> None:
    raw = (FIXTURES / "semicolon.csv").read_bytes()
    boq = read_boq(b"\xef\xbb\xbf" + raw)
    assert boq.delimiter == ";"
    assert boq.encoding == "utf-8"
    assert boq.header[0] == "Item No."
    assert boq.lines[2].short == "Béton C30/37, dalle"
    assert boq.lines[2].qty == "12,5"
    assert kinds(boq) == [LineKind.HEADER, LineKind.HEADER, LineKind.ITEM]


def test_aliased_columns_and_extras_pass_through() -> None:
    boq = read_boq(FIXTURES / "aliased_fr.csv")
    assert boq.column(Column.ITEM_NO) == "n° Article "
    assert boq.column(Column.SHORT) == "DÉSIGNATION"
    assert boq.column(Column.LONG) == "Description longue"
    assert boq.column(Column.UNIT) == "Unité"
    assert boq.column(Column.QTY) == " QUANTITÉ "
    line = boq.lines[2]
    assert (line.item_no, line.unit, line.qty) == ("00.01.0010.", "m³", "150")
    assert line.extra == (("Pos", "C"), ("Remarque", "note c"))
    assert line.extra_map == {"Pos": "C", "Remarque": "note c"}
    assert kinds(boq) == [LineKind.HEADER, LineKind.HEADER, LineKind.ITEM]


@pytest.mark.parametrize(
    "item_header", ["Item No.", "Item No", "item_no", "N° article", "ITEM  NO."]
)
def test_item_no_aliases(item_header: str) -> None:
    data = f"{item_header},Short Description,Long Description,Unit,Qty\r\n0,General,,,\r\n"
    assert read_boq(data.encode("utf-8")).column(Column.ITEM_NO) == item_header


def test_missing_required_column_raises_clear_error() -> None:
    with pytest.raises(BoqFormatError, match="qty"):
        read_boq(FIXTURES / "missing_qty.csv")


def test_ambiguous_column_raises() -> None:
    data = "Item No.,Item No,Short,Long,Unit,Qty\r\n"
    with pytest.raises(BoqFormatError, match="item_no"):
        read_boq(data.encode("utf-8"))


def test_empty_input_raises() -> None:
    with pytest.raises(BoqFormatError):
        read_boq(b"")


def test_row_longer_than_header_raises() -> None:
    data = "Item No.,Short,Long,Unit,Qty\r\n1,a,b,m,1,surplus\r\n"
    with pytest.raises(BoqFormatError, match="row 1"):
        read_boq(data.encode("utf-8"))


def test_trailing_blank_delimiters_are_accepted() -> None:
    boq = read_rows("1,a,b,m,1,", "2,c,d,m,2, ,")
    assert [line.raw_row for line in boq.lines] == [
        ("1", "a", "b", "m", "1"),
        ("2", "c", "d", "m", "2"),
    ]
    assert fixture_kinds("trailing_comma.csv") == [LineKind.HEADER, LineKind.HEADER, LineKind.ITEM]


def test_stray_quote_does_not_reject_the_file() -> None:
    boq = read_rows('1,"Pipe" steel,,m,1', "2,Valve,,u,3")
    assert len(boq.lines) == 2
    assert boq.lines[0].short.startswith("Pipe")
    assert boq.lines[1].short == "Valve"
    assert fixture_kinds("stray_quote.csv") == [LineKind.HEADER, LineKind.HEADER, LineKind.ITEM]


def test_blank_physical_line_keeps_no_invented_cells() -> None:
    boq = read_boq((HEADER_ROW + "0,G,,,\r\n\r\n00.01.0010.,x,,m,1\r\n").encode("utf-8"))
    assert boq.lines[1].kind is LineKind.EMPTY_ROW
    assert boq.lines[1].raw_row == ()
    assert boq.lines[2].kind is LineKind.ITEM


def test_extra_line_breaks_at_end_add_no_row() -> None:
    body = "0,G,,,\r\n00.01.0010.,x,,m,1\r\n"
    once = read_boq((HEADER_ROW + body).encode("utf-8"))
    twice = read_boq((HEADER_ROW + body + "\r\n\r\n").encode("utf-8"))
    assert twice == once
    assert len(twice.lines) == len(["0", "00.01.0010."])


def test_semicolon_header_with_quoted_newline_is_sniffed() -> None:
    data = '"Item\nNo.";Short;Long;Unit;Qty\r\n0;G;;;\r\n00.01.0010.;x, y;;m;1,5\r\n'
    boq = read_boq(data.encode("utf-8"))
    assert boq.delimiter == ";"
    assert boq.lines[1].short == "x, y"


def test_short_row_is_padded_with_empty_cells() -> None:
    data = "Item No.,Short,Long,Unit,Qty\r\n1,a,b\r\n"
    line = read_boq(data.encode("utf-8")).lines[0]
    assert line.raw_row == ("1", "a", "b", "", "")
    assert line.unit == ""


def test_duplicate_texts_get_distinct_ids() -> None:
    boq = read_boq(FIXTURES / "duplicates.csv")
    first, second = boq.lines[2], boq.lines[3]
    assert (first.short, first.long) == (second.short, second.long)
    assert first.line_id != second.line_id
    assert len({line.line_id for line in read_boq(INPUT_EN).lines}) == REAL_ROW_COUNT


def test_utf8_accented_file_is_never_decoded_as_cp1252() -> None:
    boq = read_boq(INPUT_FR)
    assert boq.encoding == "utf-8"
    assert boq.lines[0].short == "Installations de chantier et prestations générales"


def test_cp1252_fallback_round_trip_with_warning() -> None:
    utf8 = read_boq(INPUT_FR)
    text = INPUT_FR.read_text(encoding="utf-8")
    kept = [row for row in text.splitlines() if _encodable(row)]
    payload = "\r\n".join(kept).encode(CP1252)
    with pytest.warns(EncodingFallbackWarning):
        boq = read_boq(payload)
    assert boq.encoding == CP1252
    expected = {line.raw_row for line in utf8.lines}
    assert {line.raw_row for line in boq.lines} <= expected
    assert any("é" in line.short for line in boq.lines)
    assert len(boq.lines) == len(kept) - 1


def _encodable(row: str) -> bool:
    try:
        row.encode(CP1252)
    except UnicodeEncodeError:
        return False
    return True


def test_undecodable_input_raises() -> None:
    with pytest.raises(BoqFormatError, match="decode"):
        read_boq(b"Item No.,Short,Long,Unit,Qty\r\n1,\x81\xff\x81,,m,1\r\n")


def _line_with(item_no: str, short: str, long: str) -> BoqLine:
    data = f"Item No.,Short,Long,Unit,Qty\r\n{item_no},{short},{long},m,1\r\n"
    return read_boq(data.encode("utf-8")).lines[0]


def test_field_caps_truncate_for_prompt_only() -> None:
    long_text = "x" * (FIELD_CAPS[Column.LONG] + 5)
    line = _line_with("1", "short ", long_text)
    capped = capped_for_prompt(line)
    assert capped.truncated is True
    assert capped.long == long_text[: FIELD_CAPS[Column.LONG]]
    assert capped.short == "short "
    assert line.long == long_text
    assert oversized_fields(line) == (Column.LONG,)


def test_field_caps_within_limits_not_flagged() -> None:
    line = _line_with("1" * FIELD_CAPS[Column.ITEM_NO], "s" * FIELD_CAPS[Column.SHORT], "")
    capped = capped_for_prompt(line)
    assert capped.truncated is False
    assert (capped.item_no, capped.short, capped.long) == (line.item_no, line.short, line.long)
    assert oversized_fields(line) == ()


def test_field_cap_values() -> None:
    assert FIELD_CAPS == {Column.ITEM_NO: 64, Column.SHORT: 1000, Column.LONG: 4000}
