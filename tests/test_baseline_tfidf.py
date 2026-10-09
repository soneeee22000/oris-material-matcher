import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from oris_matcher.domain.library import make_row_id
from oris_matcher.io.boq_reader import RowCells, classify_rows

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "eval" / "baseline_tfidf.py"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


baseline = _load("oris_eval_baseline_tfidf", BASELINE_PATH)
SCORE = _load("oris_eval_score", ROOT / "eval" / "score.py")

INPUT_HEADER = ["Item No.", "Short Description", "Long Description", "Unit", "BoQ Qty"]
LIBRARY = [
    ("Concrete", "for footings", "C30/37"),
    ("Concrete", "for piers", "C30/37"),
    ("Steel", "for concrete reinforcement", ""),
    ("Asphalt", "asphalt base course", "AC 20"),
]
LINES = [
    ("1", "Structures", "", "", ""),
    ("01.01.", "Foundations", "", "", ""),
    ("01.01.0010.", "Footing concrete C30/37", "footings in C30/37", "m³", "10"),
    ("01.01.0020.", "Reinforcement steel", "concrete reinforcement bars", "t", "2"),
    ("01.02.", "Lockbox section", "", "", ""),
    ("01.02.0010.", "Zanzibarite piers C30/37", "zanzibarite pier shafts", "m³", "5"),
    ("01.02.0020.", "Asphalt base course AC 20", "asphalt base", "t", "7"),
    ("7.1.2", "Ready-mix concrete C30/37", "ready-mix footings", "m3", "50"),
]
GT = {
    "01.01.0010.": ("Concrete", "for footings", "C30/37"),
    "01.01.0020.": ("Steel", "for concrete reinforcement", ""),
    "01.02.0010.": ("Concrete", "for piers", "C30/37"),
    "01.02.0020.": ("Asphalt", "asphalt base course", "AC 20"),
}
DEV = ["01.01.0010.", "01.01.0020.", "7.1.2"]
LOCKBOX = ["01.02.0010.", "01.02.0020."]


def _write_csv(path: Path, header: list[str], rows: list[tuple[str, ...]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def tiny(tmp_path: Path) -> dict[str, Path]:
    library = _write_csv(
        tmp_path / "library.csv", ["material_type", "material_usage", "material_subtype"], LIBRARY
    )
    boq_en = _write_csv(tmp_path / "boq_en.csv", INPUT_HEADER, LINES)
    boq_fr = _write_csv(tmp_path / "boq_fr.csv", INPUT_HEADER, LINES)
    gt_rows = [(line[0], *GT.get(line[0], ("", "", ""))) for line in LINES]
    reference = _write_csv(
        tmp_path / "gt.csv",
        ["Item No.", "material_type", "material_usage", "material_subtype"],
        gt_rows,
    )
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps({"item_ids_dev": DEV, "item_ids_lockbox": LOCKBOX}), encoding="utf-8"
    )
    return {
        "library": library,
        "en": boq_en,
        "fr": boq_fr,
        "reference": reference,
        "split": split,
        "out_en": tmp_path / "b1_en.csv",
        "out_fr": tmp_path / "b1_fr.csv",
        "summary": tmp_path / "b1_summary.json",
    }


def _b1_args(paths: dict[str, Path], *extra: str) -> list[str]:
    return [
        "b1",
        "--input-en",
        str(paths["en"]),
        "--input-fr",
        str(paths["fr"]),
        "--library",
        str(paths["library"]),
        "--reference",
        str(paths["reference"]),
        "--split",
        str(paths["split"]),
        "--output-en",
        str(paths["out_en"]),
        "--output-fr",
        str(paths["out_fr"]),
        "--summary",
        str(paths["summary"]),
        *extra,
    ]


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# --- selection rule (§10.6) -----------------------------------------------------------


def _language(scores: list[float], correct: list[bool]) -> list[tuple[float, bool]]:
    return list(zip(scores, correct, strict=True))


def test_selection_picks_the_loosest_threshold_meeting_the_bar() -> None:
    strong = _language([0.9 - index * 0.01 for index in range(50)], [True] * 50)
    weak_tail = _language([0.3, 0.29, 0.28], [False, False, False])
    selection = baseline.select_threshold({"en": strong + weak_tail, "fr": strong + weak_tail})
    assert selection.status == baseline.STATUS_MEETS_BAR
    assert selection.loosest_qualifying == pytest.approx(0.29)
    assert selection.threshold == pytest.approx(0.9 - 48 * 0.01)
    assert selection.per_language["en"].matched == 49


def test_selection_within_three_correct_prefers_the_stricter() -> None:
    scores = [0.9 - index * 0.01 for index in range(60)]
    correct = [True] * 58 + [False, True]
    pool = _language(scores, correct)
    selection = baseline.select_threshold({"en": pool, "fr": pool})
    assert selection.status == baseline.STATUS_MEETS_BAR
    assert selection.per_language["en"].matched == 58
    assert selection.per_language["en"].correct == 58


def test_selection_falls_back_to_the_best_minimum_cp_bound() -> None:
    pool = _language([0.9, 0.8, 0.7, 0.6], [True, True, False, False])
    selection = baseline.select_threshold({"en": pool, "fr": pool})
    assert selection.status == baseline.STATUS_BELOW_BAR
    assert selection.threshold == pytest.approx(0.8)


def test_selection_needs_forty_matched_in_each_language() -> None:
    small = _language([0.9 - index * 0.01 for index in range(39)], [True] * 39)
    selection = baseline.select_threshold({"en": small, "fr": small})
    assert selection.status == baseline.STATUS_BELOW_BAR


# --- lockbox isolation ----------------------------------------------------------------


def test_fit_texts_never_contain_lockbox_lines(tiny: dict[str, Path]) -> None:
    lines = baseline.read_boq(tiny["en"])
    library = baseline.read_library(tiny["library"])
    texts = baseline.fit_texts(library, lines, set(DEV))
    assert not any("zanzibarite" in text.lower() for text in texts)
    assert any("footing concrete" in text.lower() for text in texts)


def test_b1_vocabulary_has_no_lockbox_only_token(tiny: dict[str, Path]) -> None:
    lines = baseline.read_boq(tiny["en"])
    library = baseline.read_library(tiny["library"])
    model = baseline.fit_b1(library, lines, set(DEV))
    vocabulary = model.word.vocabulary_
    assert not any("zanzibarite" in term for term in vocabulary)


def test_lockbox_side_is_refused_without_the_session_flag(tiny: dict[str, Path]) -> None:
    code = baseline.main(_b1_args(tiny, "--side", "lockbox"))
    assert code == baseline.EXIT_REFUSED
    assert not tiny["out_en"].exists()
    code_all = baseline.main(_b1_args(tiny, "--side", "all"))
    assert code_all == baseline.EXIT_REFUSED
    assert not tiny["out_en"].exists()
    assert not tiny["out_fr"].exists()


def test_lockbox_session_flag_allows_the_lockbox_side(tiny: dict[str, Path]) -> None:
    code = baseline.main(_b1_args(tiny, "--side", "lockbox", "--lockbox-session"))
    assert code == 0
    keys = [row["Item No."] for row in _read(tiny["out_en"])]
    assert set(LOCKBOX) <= set(keys)
    assert not set(DEV) & set(keys)


# --- output shape (§9.6) ---------------------------------------------------------------


def test_dev_output_has_the_writer_shape(tiny: dict[str, Path]) -> None:
    code = baseline.main(_b1_args(tiny))
    assert code == 0
    raw = tiny["out_en"].read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    rows = _read(tiny["out_en"])
    assert list(rows[0].keys()) == [*INPUT_HEADER, *baseline.OUTPUT_COLUMNS]
    keys = [row["Item No."] for row in rows]
    assert not set(LOCKBOX) & set(keys)
    assert {"01.01.0010.", "01.01.0020."} <= set(keys)
    headers = [row for row in rows if row["decision"] == "not_a_material"]
    assert {row["reason"] for row in headers} == {"HEADER"}
    assert {row["model"] for row in headers} == {"rules"}
    items = [row for row in rows if row["Item No."] in DEV]
    assert {row["model"] for row in items} == {"tfidf-b1"}
    for row in items:
        assert row["suggested_type"]
        assert row["library_row_id"]
        assert row["cost_usd"] == "0.000000"
        assert row["latency_ms"] == "0"
        if row["decision"] == "matched":
            assert row["material_type"] == row["suggested_type"]
            assert row["reason"].startswith("SIGNAL:b1_cosine_")
        else:
            assert row["decision"] == "needs_review"
            assert row["reason"] == "LOW_SIGNAL:b1_cosine"
            assert (row["material_type"], row["material_usage"], row["material_subtype"]) == (
                "",
                "",
                "",
            )
    summary = json.loads(tiny["summary"].read_text(encoding="utf-8"))
    assert summary["status"] == baseline.STATUS_BELOW_BAR
    assert summary["side"] == "dev"


def test_row_id_is_short_sha256_over_unit_separator() -> None:
    expected = hashlib.sha256("\x1f".join(("a", "b", "")).encode("utf-8")).hexdigest()[:12]
    assert baseline.row_id(("a", "b", "")) == expected


# --- label-free scorer check (A29) ----------------------------------------------------


LABEL_FREE_MEASURED = {
    "en": {"type": 0.722, "type_usage": 0.524, "triple": 0.405, "usage": 0.725, "subtype": 0.773},
    "fr": {"type": 0.560, "type_usage": 0.210, "triple": 0.159, "usage": 0.376, "subtype": 0.755},
}


def test_label_free_reproduces_the_measured_top1_through_the_scorer() -> None:
    result = baseline.label_free_top1(
        {
            "en": ROOT / "input" / "boq_dataset_input_en.csv",
            "fr": ROOT / "input" / "boq_dataset_input_fr.csv",
        },
        ROOT / "data" / "oris_materials_global.csv",
        ROOT / "data" / "boq_dataset_matched_GT.csv",
    )
    for lang, measured in LABEL_FREE_MEASURED.items():
        report = result[lang]
        assert report["source"] == "eval/score.py"
        levels = report["per_level_labelled"]
        for level in ("type", "type_usage", "triple"):
            assert levels[level] == pytest.approx(measured[level], abs=0.01)
        cascade = report["cascade"]
        assert cascade["usage_given_type"] == pytest.approx(measured["usage"], abs=0.01)
        assert cascade["subtype_given_type_usage"] == pytest.approx(measured["subtype"], abs=0.01)
        assert report["coverage_labelled"]["denominator"] == 252
        assert report["coverage_labelled"]["value"] == pytest.approx(levels["triple"])


# --- G1 gate (§9.5 D0 / D0a / D0b) ------------------------------------------------------


GATE_ROWS = [
    ("1", "Structures", "", "", ""),
    ("01.01.", "Foundations", "", "", ""),
    ("01.01.0010.", "Footing", "", "m³", "10"),
    ("01.01.0099.", "Orphan note", "", "", ""),
    ("", "", "", "", ""),
    ("A-1", "Section A", "", "", ""),
    ("A-1-1", "Item without unit", "", "", "3"),
    ("7.1.2", "Ready-mix", "", "m3", "50"),
]


def test_gate_classifies_headers_empty_rows_and_unconfirmed() -> None:
    kinds = baseline.line_kinds([list(row) for row in GATE_ROWS], INPUT_HEADER)
    assert kinds == [
        baseline.KIND_HEADER,
        baseline.KIND_HEADER,
        baseline.KIND_ITEM,
        baseline.KIND_HEADER_UNCONFIRMED,
        baseline.KIND_EMPTY_ROW,
        baseline.KIND_HEADER,
        baseline.KIND_ITEM,
        baseline.KIND_ITEM,
    ]


@pytest.mark.parametrize("name", ["boq_dataset_input_en.csv", "boq_dataset_input_fr.csv"])
def test_gate_agrees_with_the_shared_reader(name: str) -> None:
    with (ROOT / "input" / name).open(encoding="utf-8-sig", newline="") as handle:
        records = list(csv.reader(handle))
    rows = [*records[1:], *[list(row) for row in GATE_ROWS]]
    cells = [
        RowCells(
            item_no=row[0],
            short=row[1],
            long=row[2],
            unit=row[3],
            qty=row[4],
            all_blank=all(not value.strip() for value in row),
        )
        for row in rows
    ]
    shared = [str(kind) for kind in classify_rows(cells)]
    assert baseline.line_kinds(rows, records[0]) == shared


def test_no_measured_row_is_not_a_material(tiny: dict[str, Path]) -> None:
    assert baseline.main(_b1_args(tiny)) == 0
    for path in (tiny["out_en"], tiny["out_fr"]):
        rows = _read(path)
        measured = [row for row in rows if row["Unit"].strip() or row["BoQ Qty"].strip()]
        assert measured
        assert all(row["decision"] != "not_a_material" for row in measured)
        assert any(row["Item No."] == "7.1.2" for row in measured)


def test_item_outside_the_split_is_an_error_not_a_silent_drop(tiny: dict[str, Path]) -> None:
    tiny["split"].write_text(
        json.dumps({"item_ids_dev": DEV[:2], "item_ids_lockbox": LOCKBOX}), encoding="utf-8"
    )
    code = baseline.main(_b1_args(tiny, "--side", "all", "--lockbox-session"))
    assert code == baseline.EXIT_ERROR
    assert not tiny["out_en"].exists()


# --- B1 output through the scorer -----------------------------------------------------


def test_b1_output_passes_the_strict_scorer_and_agrees_with_the_summary(
    tiny: dict[str, Path], tmp_path: Path
) -> None:
    assert baseline.main(_b1_args(tiny)) == 0
    summary = json.loads(tiny["summary"].read_text(encoding="utf-8"))
    report_path = tmp_path / "score.json"
    for lang in ("en", "fr"):
        code = SCORE.main(
            [
                "--output",
                str(tiny[f"out_{lang}"]),
                "--reference",
                str(tiny["reference"]),
                "--split",
                str(tiny["split"]),
                "--side",
                "dev",
                "--strict",
                "--json",
                str(report_path),
            ]
        )
        assert code == 0
        precision = json.loads(report_path.read_text(encoding="utf-8"))["outputs"][0]["precision"]
        expected = summary["dev_at_threshold"][lang]
        assert (precision["correct"], precision["matched"]) == (
            expected["correct"],
            expected["matched"],
        )


def test_fallback_tie_goes_to_the_stricter_threshold() -> None:
    pool = _language([0.9, 0.8], [False, False])
    selection = baseline.select_threshold({"en": pool, "fr": pool})
    assert selection.status == baseline.STATUS_BELOW_BAR
    assert selection.threshold == pytest.approx(0.9)


def test_row_id_matches_the_library_contract() -> None:
    for triple in [("a", "b", ""), ("Concrete", "for footings", "C30/37"), ("É", " x ", "0/31,5")]:
        assert baseline.row_id(triple) == make_row_id(*triple)
