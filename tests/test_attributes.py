"""Tests for the §9.3 attribute extractors, the line-side comparison and the A2 post-pass."""

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from oris_matcher.domain.attributes import (
    EXTRACTORS,
    Attributes,
    AttrResult,
    Process,
    apply_implicit_zero,
    compare,
    extract,
    has_hard_attribute,
    implicit_zero_applies,
    implicit_zero_siblings,
)
from oris_matcher.domain.normalize import normalize

REPO_ROOT = Path(__file__).resolve().parent.parent
FR_LIBRARY = REPO_ROOT / "data" / "oris_materials_fr.csv"
EVAL_SCRIPT = REPO_ROOT / "eval" / "attr_dev_table.py"
AMBIGUITY_FIELD = "ambiguous_families"
EN_DASH = chr(0x2013)
SEMELLES_LINE = f"Semelles sur pieux P1{EN_DASH}P8, C30/37 XC4/XA1"
FR_ROW_SUBTYPE = "Béton XC3-XC4-XS1-XS2-XD1-XD2-XF1-XA1 C30/37 330kg CEM I"
EWC_1709_ROW = (
    "17 09 - Other wastes 17 09 04 - Mixed wastes other than those mentioned in "
    "17 09 01, 17 09 02 and 17 09 03"
)
GT_HEADER = ["Item No.", "material_type", "material_usage", "material_subtype"]
INPUT_HEADER = ["Item No.", "Short Description", "Long Description"]


def _field(text: str, field: str) -> Any:
    """Extract one field from the normalised text."""
    return getattr(extract(normalize(text)), field)


def _fr_row_text(subtype: str) -> str:
    """Return the joined type, usage and subtype of the FR library row with this subtype."""
    with FR_LIBRARY.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["material_subtype"] == subtype:
                return " ".join(
                    (row["material_type"], row["material_usage"], row["material_subtype"])
                )
    raise AssertionError(f"FR row {subtype!r} not found")


def test_registry_covers_every_attribute_field_once() -> None:
    """Every value field has exactly one extractor; the ambiguity marker is derived."""
    fields = [extractor.field for extractor in EXTRACTORS]
    expected = set(Attributes.__dataclass_fields__) - {AMBIGUITY_FIELD}
    assert sorted(fields) == sorted(expected)


def test_extract_empty_text_is_empty() -> None:
    """Empty text extracts nothing."""
    assert extract("") == Attributes()


EXTRACTION_CASES: list[tuple[str, str, Any]] = [
    ("strength_class", "Concrete C30/37 for footings", (30, 37)),
    ("strength_class", "Béton C8/10", (8, 10)),
    ("strength_class", "Bitumen 50/70", None),
    ("strength_class", "C25/30 blinding under C30/37 footing", None),
    ("exposure_classes", "C30/37 XC4/XA1", frozenset({"xc4", "xa1"})),
    ("exposure_classes", "Béton XS3-XD3-XA2", frozenset({"xs3", "xd3", "xa2"})),
    ("exposure_classes", "Béton X0 C16/20", frozenset({"x0"})),
    ("exposure_classes", "xc5 xf9 xyz", frozenset()),
    ("cem_type", "330kg CEM I", ("i",)),
    ("cem_type", "CEM III/A 42.5", ("iii", "a")),
    ("cem_type", "cem ii / b-ll", ("ii", "b")),
    ("cem_type", "CEM II/A or CEM II/B", ("ii",)),
    ("cem_type", "CEM I or CEM III", ()),
    ("recycled_pct", "AC 32, RA 50 %", 50),
    ("recycled_pct", "GB 3 0/20 tiède, 50 % AE", 50),
    ("recycled_pct", "EME2 - HMA 30% RAP", 30),
    ("recycled_pct", "with 20 % reclaimed asphalt", 20),
    ("recycled_pct", "10 % d'agrégats d'enrobés", 10),
    ("recycled_pct", "surface layer, virgin", 0),
    ("recycled_pct", "no RA, junction", 0),
    ("recycled_pct", "sans AE", 0),
    ("recycled_pct", "matériaux neufs", 0),
    ("recycled_pct", "virgin materials only (RA 0 %)", 0),
    ("recycled_pct", "5 % ratio", None),
    ("recycled_pct", "virgin, RA 30 %", None),
    ("recycled_pct", "GB 3, 25 % de AE", 25),
    ("recycled_pct", "30 % de RAP", 30),
    ("recycled_pct", "12,5 % RAP asphalt", None),
    ("recycled_pct", "17,5 % RAP", None),
    ("recycled_pct", "AC 12.5% RA", None),
    ("recycled_pct", "RA 12,5 %", None),
    ("process", "AC 22, reduced temperature", Process.WMA),
    ("process", "Surfactant-based warm-mix additive", Process.WMA),
    ("process", "GB 4 0/14 tiède", Process.WMA),
    ("process", "enrobé à température abaissée", Process.WMA),
    ("process", "Additive (WMA)", Process.WMA),
    ("process", "Hot Rolled Asphalt (HRA) - HMA", Process.HMA),
    ("process", "BBMa (enrobé chaud)", Process.HMA),
    ("process", "photograph", None),
    ("process", "HMA or WMA", None),
    ("ewc_code", "surplus soil, EWC 17 05 04", "170504"),
    ("ewc_code", "17 05 - Soil", "1705"),
    ("ewc_code", "17 03 02 - Bituminous mixtures other than those mentioned in 17 03 01", "170302"),
    ("ewc_code", EWC_1709_ROW, "170904"),
    ("ewc_code", "2017 report", None),
    ("ewc_code", "EWC 17 01 01 and 17 01 07 mixed", None),
    ("ewc_code", "EWC 17 05 04 or 17 05 06", None),
    ("diameter_mm", "Pipe ø160", 160),
    ("diameter_mm", "Tuyau Ø 200", 200),
    ("diameter_mm", "Pipe DN 300", 300),
    ("diameter_mm", "Pipe dn300", 300),
    ("diameter_mm", "DN 300 reducing to DN 200", None),
    ("diameter_mm", "pipe Ø 1.5 m", None),
    ("diameter_mm", "Tube ø 1,5 mm", None),
    ("diameter_mm", "DN 1,5", None),
    ("areal_mass_g_m2", "Géotextile 250 g/m²", 250),
    ("areal_mass_g_m2", "geotextile 300g/m2", 300),
    ("areal_mass_g_m2", "spray 0.45 kg/m2", None),
    ("areal_mass_g_m2", "1,5 g/m2", None),
    ("areal_mass_g_m2", "112,5 g/m²", None),
    ("grading", "GNT 0/31,5", frozenset({"0/31.5"})),
    ("grading", "Bitumen 50/70", frozenset({"50/70"})),
    ("grading", "Concrete C30/37", frozenset()),
    ("grading", "C 30/37", frozenset({"30/37"})),
]


@pytest.mark.parametrize(("field", "text", "expected"), EXTRACTION_CASES)
def test_extractor_template(field: str, text: str, expected: Any) -> None:
    """Each extractor gives the expected value on normalised text."""
    assert _field(text, field) == expected


@pytest.mark.parametrize(("field", "text", "expected"), EXTRACTION_CASES)
def test_extract_normalises_raw_text_idempotently(field: str, text: str, expected: Any) -> None:
    """Raw text gives the same value as normalised text."""
    assert getattr(extract(text), field) == expected


def test_semelles_line_agrees_with_real_fr_row() -> None:
    """The §9.3 Semelles example agrees with the real spec-bundled FR row."""
    row_text = _fr_row_text(FR_ROW_SUBTYPE)
    line = extract(normalize(SEMELLES_LINE))
    row = extract(normalize(row_text))
    assert line.strength_class == (30, 37)
    assert line.exposure_classes == frozenset({"xc4", "xa1"})
    assert row.cem_type == ("i",)
    assert compare(line, row) is AttrResult.AGREE


FAMILY_CONFLICTS: list[tuple[str, str]] = [
    ("Concrete C30/37", "Concrete C35/45"),
    ("Béton XC4 XA2", "Béton XC3-XC4-XA1"),
    ("CEM II/A", "CEM II/B"),
    ("CEM I", "CEM III"),
    ("AC base, RA 30 %", "Asphalt Concrete (AC) - HMA 50% RAP"),
    ("surface layer, virgin", "Porous Asphalt (PA) - 10% RAP"),
    ("AC 22, reduced temperature", "Asphalt Concrete (AC) - HMA"),
    ("EWC 17 05 04", "17 05 03 - Soil and stones"),
    ("Pipe DN 300", "Pipe ø200"),
    ("Geotextile 250 g/m²", "Géotextile 300 g/m²"),
]


@pytest.mark.parametrize(("line_text", "row_text"), FAMILY_CONFLICTS)
def test_each_family_conflict(line_text: str, row_text: str) -> None:
    """Each hard family can veto on its own."""
    assert compare(extract(line_text), extract(row_text)) is AttrResult.CONFLICT


@pytest.mark.parametrize(
    ("line_text", "row_text"),
    [
        ("CEM II", "CEM II/A"),
        ("EWC 17 05", "17 05 04 - Soil and stones"),
        ("EWC 17 05 04", "17 05 - Soil"),
        ("Béton XC4", "Béton XC3-XC4-XA1"),
    ],
)
def test_levelled_and_set_families_agree(line_text: str, row_text: str) -> None:
    """Levelled, prefix and subset families agree when compatible."""
    assert compare(extract(line_text), extract(row_text)) is AttrResult.AGREE


def test_absence_rule_row_lacks_line_family() -> None:
    """A row is not penalised for lacking a family the line states."""
    line = extract("Semelles C30/37 XC4/XA1 CEM III")
    row = extract("Concrete for footings C30/37")
    assert compare(line, row) is AttrResult.AGREE


def test_absence_rule_line_lacks_row_family() -> None:
    """A row's extra families do not stop agreement."""
    line = extract("Semelles C30/37")
    row = extract(FR_ROW_SUBTYPE)
    assert compare(line, row) is AttrResult.AGREE


def test_absence_rule_no_shared_family_is_no_evidence() -> None:
    """No family stated on both sides gives no_evidence."""
    line = extract("Pipe DN 300")
    row = extract("Concrete for footings C30/37")
    assert compare(line, row) is AttrResult.NO_EVIDENCE


@pytest.mark.parametrize(
    ("line_text", "row_text"),
    [("Bitumen 50/70", "Bitume pur 35/50"), ("GNT 0/31,5", "Grave 0/20"), ("0/20", "")],
)
def test_grading_is_soft_and_never_conflicts(line_text: str, row_text: str) -> None:
    """Grading is extracted but never takes part in the comparison."""
    line = extract(line_text)
    row = extract(row_text)
    assert line.grading
    assert compare(line, row) is AttrResult.NO_EVIDENCE


def test_bitumen_grade_is_not_a_strength_class() -> None:
    """A bitumen grade is a soft grading, not a strength class."""
    attributes = extract(normalize("Bitumen 50/70 penetration grade"))
    assert attributes.strength_class is None
    assert attributes.grading == frozenset({"50/70"})
    assert compare(attributes, extract("Concrete C30/37")) is AttrResult.NO_EVIDENCE


AMBIGUOUS_HARD_LINES: list[tuple[str, str]] = [
    ("DN 125 and DN 100 gutters, lump sum", "diameter_mm"),
    ("gutters dn 125 and downpipes dn 100", "diameter_mm"),
    ("C25/30 blinding and C30/37 walls", "strength_class"),
    ("CEM I or CEM III", "cem_type"),
    ("hot or warm mix", "process"),
    ("virgin, RA 30 %", "recycled_pct"),
    ("12,5 % RAP asphalt", "recycled_pct"),
    ("pipe Ø 1.5 m", "diameter_mm"),
    ("1,5 g/m2 coating", "areal_mass_g_m2"),
    ("geotextile 200 g/m2 or 300 g/m2", "areal_mass_g_m2"),
    ("EWC 17 05 04 or 17 05 06", "ewc_code"),
]


@pytest.mark.parametrize(("text", "family"), AMBIGUOUS_HARD_LINES)
def test_stated_but_ambiguous_family_still_counts_as_hard(text: str, family: str) -> None:
    """A hard family stated with several or non-integer values still blocks gate G2."""
    attributes = extract(text)
    assert getattr(attributes, family) in (None, ())
    assert family in attributes.ambiguous_families
    assert has_hard_attribute(attributes)


@pytest.mark.parametrize("text", ["", "Labour, lump sum", "Bitumen 50/70", "Concrete C30/37"])
def test_unambiguous_text_has_no_ambiguous_family(text: str) -> None:
    """Absent or single-valued families are never marked ambiguous."""
    assert extract(text).ambiguous_families == frozenset()


def test_ambiguous_family_never_vetoes() -> None:
    """A line stating two EWC codes does not conflict with a row it allows."""
    line = extract("EWC 17 05 04 or 17 05 06")
    row = extract("17 05 - Soil 17 05 06 - Dredging spoil other than those mentioned in 17 05 05")
    assert row.ewc_code == "170506"
    assert compare(line, row) is not AttrResult.CONFLICT


def _asphalt(subtype: str) -> Attributes:
    """Extract the attributes of a library subtype text."""
    return extract(normalize(subtype))


def test_implicit_zero_from_positive_sibling() -> None:
    """A row with no percentage counts as 0 next to a positive sibling."""
    group = [
        _asphalt("Asphalt Concrete (AC) - HMA"),
        _asphalt("Asphalt Concrete (AC) - HMA 30% RAP"),
    ]
    result = implicit_zero_siblings(group)
    assert result[0].recycled_pct == 0
    assert result[1].recycled_pct == 30
    assert result[0].process is Process.HMA


def test_implicit_zero_not_triggered_by_virgin_only_sibling() -> None:
    """A sibling that states only an explicit 0 does not trigger the rule."""
    group = [_asphalt("Gravel"), _asphalt("Virgin Aggregates")]
    result = implicit_zero_siblings(group)
    assert result[0].recycled_pct is None
    assert result[1].recycled_pct == 0


def test_implicit_zero_without_any_percentage_changes_nothing() -> None:
    """A group with no percentage is left unchanged."""
    group = (_asphalt("Asphalt Concrete (AC) - HMA"), _asphalt("Asphalt Concrete (AC) - WMA"))
    assert implicit_zero_siblings(group) == group


def test_implicit_zero_vetoes_recycled_line_against_unstated_row() -> None:
    """A recycled line conflicts with the implicit-zero sibling and agrees with the stated one."""
    plain, recycled = implicit_zero_siblings(
        [_asphalt("Asphalt Concrete (AC) - HMA"), _asphalt("Asphalt Concrete (AC) - HMA 30% RAP")]
    )
    line = extract("AC base course, hot mix, RA 30 %")
    assert compare(line, plain) is AttrResult.CONFLICT
    assert compare(line, recycled) is AttrResult.AGREE


def test_implicit_zero_skips_row_with_contradictory_percentages() -> None:
    """A row that states contradictory percentages is not given an invented 0."""
    stated, ambiguous = implicit_zero_siblings(
        [_asphalt("AC HMA 30% RAP"), _asphalt("AC HMA virgin, RA 30 %")]
    )
    assert stated.recycled_pct == 30
    assert ambiguous.recycled_pct is None


def test_apply_implicit_zero_is_per_parent_group() -> None:
    """The post-pass works per parent group and keeps the key order."""
    groups = {
        "T01.U01": [_asphalt("AC - HMA"), _asphalt("AC - HMA 30% RAP")],
        "T01.U02": [_asphalt("AC - HMA")],
    }
    result = apply_implicit_zero(groups, library="global")
    assert result["T01.U01"][0].recycled_pct == 0
    assert result["T01.U02"][0].recycled_pct is None
    assert list(result) == list(groups)


@pytest.mark.parametrize(("library", "expected"), [("global", 0), ("fr", None), ("", None)])
def test_implicit_zero_applies_only_to_checked_catalogues(library: str, expected: Any) -> None:
    """The A2 implicit zero is applied only on catalogues where it was checked."""
    groups = {"T01.U01": [_asphalt("AC - HMA"), _asphalt("AC - HMA 30% RAP")]}
    result = apply_implicit_zero(groups, library=library)
    assert result["T01.U01"][0].recycled_pct == expected
    assert implicit_zero_applies(library) is (expected is not None)


def _eval_script() -> ModuleType:
    """Import the eval dev-table script by path."""
    spec = importlib.util.spec_from_file_location("attr_dev_table", EVAL_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    """Write a small synthetic CSV fixture."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_eval_script_defaults_are_anchored_to_the_repo() -> None:
    """The eval script's default paths do not depend on the working directory."""
    module = _eval_script()
    defaults = [module.DEFAULT_GT, module.DEFAULT_SPLIT, module.DEFAULT_LIBRARY]
    defaults += list(module.DEFAULT_INPUTS.values())
    assert all(path.is_absolute() and path.is_relative_to(REPO_ROOT) for path in defaults)


def test_eval_labels_reject_dev_id_missing_from_gt(tmp_path: Path) -> None:
    """A dev id with no ground-truth row raises a clear error."""
    module = _eval_script()
    gt_path = _write_csv(tmp_path / "gt.csv", GT_HEADER, [["A1", "T", "U", "S"]])
    with pytest.raises(ValueError, match="A2"):
        module.load_dev_labels(gt_path, ["A1", "A2"])


def test_eval_labels_skip_unlabelled_dev_rows(tmp_path: Path) -> None:
    """A dev row with an empty label is unlabelled, not an error."""
    module = _eval_script()
    rows = [["A1", "T", "U", "S"], ["A2", "", "", ""]]
    gt_path = _write_csv(tmp_path / "gt.csv", GT_HEADER, rows)
    assert module.load_dev_labels(gt_path, ["A1", "A2"]) == {"A1": ("T", "U", "S")}


def test_eval_line_texts_reject_missing_item(tmp_path: Path) -> None:
    """A labelled item with no line text raises a clear error."""
    module = _eval_script()
    input_path = _write_csv(tmp_path / "in.csv", INPUT_HEADER, [["A1", "short", "long"]])
    with pytest.raises(ValueError, match="A2"):
        module.load_line_texts(input_path, {"A1", "A2"})
