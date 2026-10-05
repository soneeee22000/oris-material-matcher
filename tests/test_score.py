import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[1]
SCORE_PATH = ROOT / "eval" / "score.py"
FIXTURES = ROOT / "tests" / "fixtures" / "score"
GT_PATH = ROOT / "data" / "boq_dataset_matched_GT.csv"
SAMPLE_OUTPUT = ROOT / "output" / "boq_dataset_output_sample.csv"
SPLIT_PATH = ROOT / "eval" / "split_v1.json"


def _load_score() -> ModuleType:
    spec = importlib.util.spec_from_file_location("oris_eval_score", SCORE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


score = _load_score()


def _run(tmp_path: Path, *args: str) -> tuple[int, dict[str, Any]]:
    report_path = tmp_path / "report.json"
    code = score.main([*args, "--json", str(report_path)])
    if not report_path.exists():
        return code, {}
    return code, json.loads(report_path.read_text(encoding="utf-8"))


def _fx(name: str) -> str:
    return str(FIXTURES / name)


def _only(report: dict[str, Any]) -> dict[str, Any]:
    assert len(report["outputs"]) == 1
    result: dict[str, Any] = report["outputs"][0]
    return result


# --- Clopper-Pearson and Wilson -------------------------------------------------------


@pytest.mark.parametrize(
    ("correct", "total", "expected"),
    [
        (30, 30, 0.905),
        (40, 40, 0.928),
        (39, 40, 0.887),
        (57, 60, 0.876),
        (59, 60, 0.923),
        (95, 100, 0.898),
        (102, 113, 0.844),
        (133, 139, 0.917),
    ],
)
def test_cp_lower_bound_matches_the_protocol_table(
    correct: int, total: int, expected: float
) -> None:
    assert round(score.cp_lower_bound(correct, total), 3) == expected


@pytest.mark.parametrize(("correct", "total"), [(1, 1), (3, 8), (10, 11), (1, 50), (49, 50)])
def test_cp_lower_bound_agrees_with_scipy_beta(correct: int, total: int) -> None:
    exact = float(beta.ppf(0.05, correct, total - correct + 1))
    assert score.cp_lower_bound(correct, total) == pytest.approx(exact, abs=1e-9)


def test_cp_lower_bound_is_zero_without_successes() -> None:
    assert score.cp_lower_bound(0, 20) == 0.0


def test_wilson_matches_the_protocol_table() -> None:
    low, high = score.wilson_interval(28, 30)
    assert (round(low, 3), round(high, 3)) == (0.787, 0.982)


# --- hand-computed 15-row fixture -----------------------------------------------------


def test_hand_computed_fixture(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "--output",
        _fx("output.csv"),
        "--label",
        "en",
        "--reference",
        _fx("reference.csv"),
        "--classes",
        _fx("classes.csv"),
    )
    assert code == 0
    out = _only(report)
    assert out["label"] == "en"
    assert out["counts"]["rows"] == 16
    assert out["counts"]["items"] == 14
    assert out["counts"]["labelled"] == 11
    assert out["counts"]["no_equivalent"] == 2
    precision = out["precision"]
    assert (precision["correct"], precision["matched"]) == (3, 9)
    assert precision["value"] == pytest.approx(3 / 9)
    assert precision["cp_lower_95"] == pytest.approx(float(beta.ppf(0.05, 3, 7)), abs=1e-9)
    assert out["coverage_labelled"]["value"] == pytest.approx(3 / 11)
    assert out["coverage_labelled"]["denominator"] == 11
    assert out["coverage_material"]["value"] == pytest.approx(3 / 13)
    assert out["coverage_material"]["denominator"] == 13
    assert out["per_level_matched"] == pytest.approx(
        {"type": 7 / 9, "type_usage": 5 / 9, "triple": 3 / 9}
    )
    labelled = out["per_level_labelled"]
    assert labelled["source"] == "suggested"
    assert (labelled["type"], labelled["type_usage"], labelled["triple"]) == pytest.approx(
        (9 / 11, 6 / 11, 4 / 11)
    )
    assert out["cascade"]["usage_given_type"] == pytest.approx(6 / 9)
    assert out["cascade"]["subtype_given_type_usage"] == pytest.approx(4 / 6)
    assert out["false_not_a_material"] == 1
    assert out["f_nm_scope"] == "labelled+no_equivalent"
    assert out["not_a_material_with_unit"] == 2
    assert out["whitespace_only_mismatch"] == 1
    assert out["hit_at_1"] == {"hits": 1, "denominator": 2, "value": 0.5}
    assert out["hit_at_2"] == {"hits": 2, "denominator": 2, "value": 1.0}
    shares_all = out["decision_shares"]["all_rows"]
    assert shares_all["denominator"] == 16
    assert shares_all["counts"] == {"matched": 9, "not_a_material": 4, "needs_review": 3}
    shares_items = out["decision_shares"]["item_rows"]
    assert shares_items["denominator"] == 14
    assert shares_items["counts"] == {"matched": 9, "not_a_material": 2, "needs_review": 3}
    assert out["mean_cost_usd"] == pytest.approx(0.012 / 16)
    assert out["mean_latency_ms"] == pytest.approx(1200 / 16)
    assert out["lenient"]["precision"] == pytest.approx(4 / 9)
    assert any("lenient" in warning for warning in out["warnings"])
    assert out["confusion"]["E"] == {"matched": 1, "needs_review": 1}
    assert out["confusion"]["H"] == {"not_a_material": 2}


def test_hand_computed_fixture_passes_strict(tmp_path: Path) -> None:
    code, _ = _run(
        tmp_path, "--output", _fx("output.csv"), "--reference", _fx("reference.csv"), "--strict"
    )
    assert code == 0


def test_without_classes_safety_count_runs_on_labelled_lines_only(tmp_path: Path) -> None:
    _, report = _run(tmp_path, "--output", _fx("output.csv"), "--reference", _fx("reference.csv"))
    out = _only(report)
    assert out["false_not_a_material"] == 1
    assert out["f_nm_scope"] == "labelled"
    assert out["coverage_material"] is None
    assert out["counts"]["no_equivalent"] is None
    text = score.render(report)
    assert "F_NM over labelled lines only (no material-without-equivalent list given)" in text


def test_nothing_matched_gives_na_precision(tmp_path: Path) -> None:
    output = tmp_path / "none_matched.csv"
    output.write_text(
        "Item No.,Unit,decision,material_type,material_usage,material_subtype\n"
        "X1,t,needs_review,,,\nX2,t,needs_review,,,\n",
        encoding="utf-8",
    )
    _, report = _run(tmp_path, "--output", str(output), "--reference", _fx("two_line_output.csv"))
    out = _only(report)
    assert out["precision"]["value"] is None
    assert out["precision"]["cp_lower_95"] is None
    assert out["coverage_labelled"]["value"] == 0.0
    assert out["mean_cost_usd"] is None
    assert out["mean_latency_ms"] is None


# --- GT against itself, the sample output ---------------------------------------------


def _gt_as_output(tmp_path: Path) -> Path:
    with GT_PATH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    path = tmp_path / "gt_output.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["Item No.", "decision", "material_type", "material_usage", "material_subtype"]
        )
        for row in rows:
            labels = [row["material_type"], row["material_usage"], row["material_subtype"]]
            decision = "matched" if any(labels) else "not_a_material"
            writer.writerow([row["Item No."], decision, *labels])
    return path


def test_gt_scored_against_itself_gives_precision_one(tmp_path: Path) -> None:
    output = _gt_as_output(tmp_path)
    code, report = _run(tmp_path, "--output", str(output), "--reference", str(GT_PATH))
    assert code == 0
    out = _only(report)
    assert out["precision"]["value"] == 1.0
    assert out["precision"]["matched"] == 252
    assert out["coverage_labelled"]["value"] == 1.0
    assert out["counts"]["rows"] == 319
    assert out["counts"]["items"] == 282
    assert out["false_not_a_material"] == 0


def test_gt_against_itself_on_the_dev_side(tmp_path: Path) -> None:
    output = _gt_as_output(tmp_path)
    _, report = _run(
        tmp_path,
        "--output",
        str(output),
        "--reference",
        str(GT_PATH),
        "--split",
        str(SPLIT_PATH),
        "--side",
        "dev",
    )
    out = _only(report)
    assert out["counts"]["labelled"] == 139
    assert out["counts"]["rows"] == 162
    assert out["precision"]["value"] == 1.0
    assert report["side"] == "dev"
    assert out["decision_shares"]["all_rows"] is None
    assert out["decision_shares"]["item_rows"]["denominator"] == 162
    assert "decisions over all rows n/a" in score.render(report)


def test_lockbox_side_has_113_labelled(tmp_path: Path) -> None:
    output = _gt_as_output(tmp_path)
    _, report = _run(
        tmp_path,
        "--output",
        str(output),
        "--reference",
        str(GT_PATH),
        "--split",
        str(SPLIT_PATH),
        "--side",
        "lockbox",
    )
    assert _only(report)["counts"]["labelled"] == 113


def test_sample_output_against_gt_is_unscored_not_a_crash(tmp_path: Path) -> None:
    code, report = _run(tmp_path, "--output", str(SAMPLE_OUTPUT), "--reference", str(GT_PATH))
    assert code == 0
    out = _only(report)
    assert out["counts"]["unscored"] == 6
    assert out["counts"]["missing"] == 319
    assert out["precision"]["value"] is None
    assert out["coverage_labelled"]["value"] == 0.0
    assert out["mean_cost_usd"] is None


def test_sample_output_against_its_own_labels(tmp_path: Path) -> None:
    with SAMPLE_OUTPUT.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    reference = tmp_path / "sample_reference.csv"
    with reference.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Item No.", "material_type", "material_usage", "material_subtype"])
        for row in rows:
            writer.writerow(
                [
                    row["Item No."],
                    row["material_type"],
                    row["material_usage"],
                    row["material_subtype"],
                ]
            )
    code, report = _run(
        tmp_path, "--output", str(SAMPLE_OUTPUT), "--reference", str(reference), "--strict"
    )
    assert code == 0
    out = _only(report)
    assert (out["precision"]["correct"], out["precision"]["matched"]) == (4, 4)
    assert out["mean_cost_usd"] == pytest.approx(
        (0.0011 + 0.0010 + 0.0012 + 0.0011 + 0.0011 + 0.0008) / 6
    )
    assert out["mean_latency_ms"] == pytest.approx((412 + 388 + 455 + 431 + 402 + 290) / 6)
    assert out["per_level_labelled"]["source"] == "decision"


# --- lenient input handling -----------------------------------------------------------


def test_labels_only_reference_needs_row_order(tmp_path: Path) -> None:
    code, _ = _run(
        tmp_path, "--output", _fx("output.csv"), "--reference", _fx("labels_only_reference.csv")
    )
    assert code == score.EXIT_ERROR


def test_labels_only_reference_with_row_order(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "--output",
        _fx("output.csv"),
        "--reference",
        _fx("labels_only_reference.csv"),
        "--join",
        "row-order",
    )
    assert code == 0
    out = _only(report)
    assert (out["precision"]["correct"], out["precision"]["matched"]) == (3, 9)
    assert out["counts"]["items"] == 11
    assert out["counts"]["unclassed"] == 5
    assert out["counts"]["labelled"] == 11


def test_duplicate_codes_join_on_occurrence(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path, "--output", _fx("dup_output.csv"), "--reference", _fx("dup_reference.csv")
    )
    assert code == 0
    out = _only(report)
    assert (out["precision"]["correct"], out["precision"]["matched"]) == (4, 4)
    assert any("duplicate" in warning for warning in report["warnings"])
    assert any("blank" in warning for warning in report["warnings"])


def test_duplicate_codes_are_an_error_under_strict(tmp_path: Path) -> None:
    code, _ = _run(
        tmp_path,
        "--output",
        _fx("dup_output.csv"),
        "--reference",
        _fx("dup_reference.csv"),
        "--strict",
    )
    assert code == score.EXIT_ERROR


def test_nan_literals_count_as_blank(tmp_path: Path) -> None:
    _, report = _run(
        tmp_path, "--output", _fx("nan_output.csv"), "--reference", _fx("nan_reference.csv")
    )
    out = _only(report)
    assert report["reference"]["nan_literals"] == 6
    assert (out["precision"]["correct"], out["precision"]["matched"]) == (2, 2)
    assert out["counts"]["labelled"] == 3
    assert out["false_not_a_material"] == 0
    assert any("nan" in warning for warning in report["warnings"])


def test_nfd_reference_fails_exact_and_warns_on_lenient(tmp_path: Path) -> None:
    _, report = _run(
        tmp_path, "--output", _fx("nfc_output.csv"), "--reference", _fx("nfd_reference.csv")
    )
    out = _only(report)
    assert out["precision"]["value"] == 0.0
    assert out["lenient"]["precision"] == 1.0
    assert out["whitespace_only_mismatch"] == 0
    assert any("lenient" in warning for warning in out["warnings"])


def test_renamed_key_header_is_auto_detected(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "--output",
        _fx("two_line_output.csv"),
        "--reference",
        _fx("renamed_key_reference.csv"),
    )
    assert code == 0
    assert (_only(report)["precision"]["correct"], _only(report)["precision"]["matched"]) == (1, 2)


def test_key_override(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "--output",
        _fx("two_line_output.csv"),
        "--reference",
        _fx("custom_key_reference.csv"),
        "--key",
        "Code",
    )
    assert code == 0
    assert _only(report)["precision"]["correct"] == 1


def test_unknown_key_without_override_is_an_error(tmp_path: Path) -> None:
    code, _ = _run(
        tmp_path,
        "--output",
        _fx("two_line_output.csv"),
        "--reference",
        _fx("custom_key_reference.csv"),
    )
    assert code == score.EXIT_ERROR


def test_keyless_output_with_row_order(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "--output",
        _fx("keyless_output.csv"),
        "--reference",
        _fx("renamed_key_reference.csv"),
        "--join",
        "row-order",
    )
    assert code == 0
    assert (_only(report)["precision"]["correct"], _only(report)["precision"]["matched"]) == (1, 2)


def test_missing_and_extra_rows_are_listed(tmp_path: Path) -> None:
    output = tmp_path / "partial.csv"
    output.write_text(
        "Item No.,Unit,decision,material_type,material_usage,material_subtype\n"
        "X1,m³,matched,Concrete,for footings,C30/37\n"
        "Z9,t,matched,Steel,for rebar,B500\n",
        encoding="utf-8",
    )
    code, report = _run(
        tmp_path, "--output", str(output), "--reference", _fx("renamed_key_reference.csv")
    )
    assert code == 0
    out = _only(report)
    assert out["missing_keys"] == ["X2"]
    assert out["unscored_keys"] == ["Z9"]
    assert out["coverage_labelled"] == {"correct": 1, "denominator": 2, "value": 0.5}
    assert (out["precision"]["correct"], out["precision"]["matched"]) == (1, 1)

    strict_code, _ = _run(
        tmp_path,
        "--output",
        str(output),
        "--reference",
        _fx("renamed_key_reference.csv"),
        "--strict",
    )
    assert strict_code == score.EXIT_ERROR
    text = score.render(report)
    assert "missing: X2" in text
    assert "unscored: Z9" in text


@pytest.mark.parametrize(
    ("output_name", "reference_name"),
    [
        ("dup_only_output.csv", "dup_only_reference.csv"),
        ("missing_only_output.csv", "renamed_key_reference.csv"),
        ("extra_only_output.csv", "renamed_key_reference.csv"),
    ],
)
def test_each_key_violation_alone_is_an_error_only_under_strict(
    tmp_path: Path, output_name: str, reference_name: str
) -> None:
    args = ["--output", _fx(output_name), "--reference", _fx(reference_name)]
    lenient_code, _ = _run(tmp_path, *args)
    assert lenient_code == 0
    strict_code, _ = _run(tmp_path, *args, "--strict")
    assert strict_code == score.EXIT_ERROR


def test_key_listing_is_truncated(tmp_path: Path) -> None:
    keys = [f"K{index:02d}" for index in range(score.LIST_LIMIT + 3)]
    reference = tmp_path / "many.csv"
    reference.write_text(
        "Item No.,material_type,material_usage,material_subtype\n"
        + "".join(f"{key},Steel,for rebar,B500\n" for key in keys),
        encoding="utf-8",
    )
    _, report = _run(
        tmp_path, "--output", _fx("two_line_output.csv"), "--reference", str(reference)
    )
    assert "... (+3 more)" in score.render(report)


@pytest.mark.parametrize(
    "bad_row",
    [
        "X1,m³,maybe,,,",
        "X1,m³,matched,,,",
        "X1,m³,needs_review,Concrete,for footings,C30/37",
    ],
)
def test_strict_rejects_invalid_rows(tmp_path: Path, bad_row: str) -> None:
    output = tmp_path / "bad.csv"
    output.write_text(
        "Item No.,Unit,decision,material_type,material_usage,material_subtype\n"
        f"{bad_row}\nX2,t,matched,Steel,for rebar,B500\n",
        encoding="utf-8",
    )
    reference = _fx("renamed_key_reference.csv")
    lenient_code, _ = _run(tmp_path, "--output", str(output), "--reference", reference)
    assert lenient_code == 0
    strict_code, _ = _run(tmp_path, "--output", str(output), "--reference", reference, "--strict")
    assert strict_code == score.EXIT_ERROR


def test_strict_row_order_needs_equal_counts(tmp_path: Path) -> None:
    code, _ = _run(
        tmp_path,
        "--output",
        _fx("keyless_output.csv"),
        "--reference",
        _fx("reference.csv"),
        "--join",
        "row-order",
        "--strict",
    )
    assert code == score.EXIT_ERROR


def test_repeated_output_label_pairs(tmp_path: Path) -> None:
    code, report = _run(
        tmp_path,
        "--output",
        _fx("output.csv"),
        "--label",
        "en",
        "--output",
        _fx("output.csv"),
        "--label",
        "fr",
        "--reference",
        _fx("reference.csv"),
    )
    assert code == 0
    assert [out["label"] for out in report["outputs"]] == ["en", "fr"]


def test_cli_prints_headline_and_lenient_line() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCORE_PATH),
            "--output",
            _fx("output.csv"),
            "--label",
            "en",
            "--reference",
            _fx("reference.csv"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "matched precision 0.333 (3/9)" in completed.stdout
    assert "one-sided 95% CP lower bound" in completed.stdout
    assert "lenient (NFC + trim + whitespace collapse)" in completed.stdout
    assert "WARNING" in completed.stdout


def test_score_imports_nothing_from_src() -> None:
    text = SCORE_PATH.read_text(encoding="utf-8")
    assert "oris_matcher" not in text
    assert "pandas" not in text


def test_strict_on_a_side_ignores_rows_of_the_other_side(tmp_path: Path) -> None:
    with GT_PATH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    dev = set(split["item_ids_dev"])
    output = tmp_path / "dev_only.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["Item No.", "decision", "material_type", "material_usage", "material_subtype"]
        )
        for row in rows:
            if row["Item No."] in dev:
                writer.writerow([row["Item No."], "needs_review", "", "", ""])
    args = ["--output", str(output), "--reference", str(GT_PATH), "--split", str(SPLIT_PATH)]
    code, _ = _run(tmp_path, *args, "--side", "dev", "--strict")
    assert code == 0
    code_all, _ = _run(tmp_path, *args, "--side", "all", "--strict")
    assert code_all == score.EXIT_ERROR


# --- denominators come from the reference only ------------------------------------------


def _rewrite_output(tmp_path: Path, name: str, blank_unit: bool) -> Path:
    with (FIXTURES / "output.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    unit_at = rows[0].index("Unit")
    decision_at = rows[0].index("decision")
    for row in rows[1:]:
        if row[0] not in {"01.01.0060.", "01.01.0110."}:
            continue
        row[decision_at : decision_at + 4] = ["not_a_material", "", "", ""]
        if blank_unit:
            row[unit_at] = ""
    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return path


def test_blanking_an_output_unit_changes_no_denominator(tmp_path: Path) -> None:
    results = []
    for blank_unit in (False, True):
        output = _rewrite_output(tmp_path, f"hostile_{blank_unit}.csv", blank_unit)
        _, report = _run(
            tmp_path,
            "--output",
            str(output),
            "--reference",
            _fx("reference.csv"),
            "--classes",
            _fx("classes.csv"),
        )
        results.append(_only(report))
    for out in results:
        assert out["counts"]["items"] == 14
        assert out["counts"]["no_equivalent"] == 2
        assert out["coverage_material"]["denominator"] == 13
        assert out["false_not_a_material"] == 3
        assert out["decision_shares"]["item_rows"]["denominator"] == 14
    assert results[0]["not_a_material_with_unit"] == 4
    assert results[1]["not_a_material_with_unit"] == 2


def test_keyless_row_order_does_not_count_headers_as_items(tmp_path: Path) -> None:
    with (FIXTURES / "output.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    keep = [index for index, name in enumerate(rows[0]) if name not in {"Item No.", "Unit"}]
    output = tmp_path / "keyless_unitless.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([[row[index] for index in keep] for row in rows])
    row_order = ["--output", str(output), "--join", "row-order", "--reference"]
    _, keyed = _run(tmp_path, *row_order, _fx("reference.csv"))
    assert _only(keyed)["counts"]["items"] == 14
    assert _only(keyed)["not_a_material_with_unit"] is None
    _, labels_only = _run(tmp_path, *row_order, _fx("labels_only_reference.csv"))
    counts = _only(labels_only)["counts"]
    assert (counts["items"], counts["unclassed"]) == (11, 5)


def test_reference_unit_column_decides_headers(tmp_path: Path) -> None:
    _, report = _run(
        tmp_path,
        "--output",
        _fx("unit_output.csv"),
        "--reference",
        _fx("unit_reference.csv"),
        "--key",
        "Code",
    )
    out = _only(report)
    assert out["counts"]["items"] == 2
    assert out["confusion"]["H"] == {"not_a_material": 1}
    assert out["confusion"]["blank"] == {"not_a_material": 1}


def test_key_override_that_matches_no_file_is_an_error(tmp_path: Path) -> None:
    code, _ = _run(
        tmp_path,
        "--output",
        _fx("two_line_output.csv"),
        "--reference",
        _fx("renamed_key_reference.csv"),
        "--key",
        "NoSuchColumn",
    )
    assert code == score.EXIT_ERROR


def test_nan_literals_in_an_output_read_as_blank(tmp_path: Path) -> None:
    args = [
        "--output",
        _fx("nan_matched_output.csv"),
        "--reference",
        _fx("blank_subtype_reference.csv"),
    ]
    code, report = _run(tmp_path, *args, "--strict")
    assert code == 0
    out = _only(report)
    assert (out["precision"]["correct"], out["precision"]["matched"]) == (1, 1)
    assert "output: 4 nan-like label cell(s) read as blank" in out["warnings"]


def test_hit_at_2_says_why_it_is_unavailable(tmp_path: Path) -> None:
    _, report = _run(tmp_path, "--output", str(SAMPLE_OUTPUT), "--reference", str(GT_PATH))
    assert "hit@2 n/a (no second-suggestion columns in the §9.6 shape)" in score.render(report)


# --- hit@2 from the A56 suggested2_* columns -----------------------------------------

A56_HEADER = [
    "Item No.",
    "decision",
    "material_type",
    "material_usage",
    "material_subtype",
    "suggested_type",
    "suggested_usage",
    "suggested_subtype",
    "suggested2_type",
    "suggested2_usage",
    "suggested2_subtype",
]
A56_CONCRETE = ["Concrete", "Ready-mix", "C30/37"]
A56_STEEL = ["Steel", "Rebar", ""]
A56_BLANK = ["", "", ""]


def _write_rows(path: Path, rows: list[list[str]]) -> str:
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\r\n").writerows(rows)
    return str(path)


def _hit_files(tmp_path: Path, header: list[str], rows: list[list[str]]) -> list[str]:
    reference = [
        ["Item No.", "material_type", "material_usage", "material_subtype"],
        ["01.01.0010.", *A56_CONCRETE],
        ["01.01.0020.", *A56_STEEL],
        ["01.01.0030.", *A56_CONCRETE],
    ]
    output = [header, *[row[: len(header)] for row in rows]]
    return [
        "--output",
        _write_rows(tmp_path / "a56_output.csv", output),
        "--reference",
        _write_rows(tmp_path / "a56_reference.csv", reference),
    ]


def _review(key: str, first: list[str], second: list[str]) -> list[str]:
    return [key, "needs_review", *A56_BLANK, *first, *second]


A56_ROWS = [
    _review("01.01.0010.", A56_STEEL, A56_CONCRETE),
    _review("01.01.0020.", A56_STEEL, A56_CONCRETE),
    _review("01.01.0030.", A56_STEEL, A56_BLANK),
]


def test_hit_at_2_reads_the_a56_columns(tmp_path: Path) -> None:
    code, report = _run(tmp_path, *_hit_files(tmp_path, A56_HEADER, A56_ROWS), "--strict")
    assert code == 0
    out = _only(report)
    assert out["hit_at_1"] == {"hits": 1, "denominator": 3, "value": pytest.approx(1 / 3)}
    assert out["hit_at_2"] == {"hits": 2, "denominator": 3, "value": pytest.approx(2 / 3)}
    assert "note" not in out["hit_at_2"]
    assert "hit@1 0.333, hit@2 0.667 over 3 needs_review" in score.render(report)


def test_hit_at_2_is_na_without_the_a56_columns(tmp_path: Path) -> None:
    code, report = _run(tmp_path, *_hit_files(tmp_path, A56_HEADER[:8], A56_ROWS))
    assert code == 0
    out = _only(report)
    assert out["hit_at_1"]["hits"] == 1
    assert out["hit_at_2"]["hits"] is None
    assert out["hit_at_2"]["value"] is None
    assert "hit@2 n/a (no second-suggestion columns" in score.render(report)


def test_blank_second_suggestion_never_hits(tmp_path: Path) -> None:
    rows = [_review(key, A56_BLANK, A56_BLANK) for key in ("01.01.0010.", "01.01.0020.")]
    rows.append(_review("01.01.0030.", A56_BLANK, A56_BLANK))
    _, report = _run(tmp_path, *_hit_files(tmp_path, A56_HEADER, rows))
    assert _only(report)["hit_at_2"] == {"hits": 0, "denominator": 3, "value": 0.0}


def test_json_report_is_byte_portable(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    outside = tmp_path / "outside.csv"
    outside.write_bytes(Path(_fx("output.csv")).read_bytes())
    argv = ["--output", str(outside), "--reference", _fx("reference.csv"), "--label", "x"]
    assert score.main([*argv, "--json", str(report_path)]) == 0
    raw = report_path.read_bytes()
    assert b"\r" not in raw
    payload = json.loads(raw)
    expected = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert raw.decode("utf-8") == expected
    assert payload["outputs"][0]["path"] == {"external": True, "path": "outside.csv"}
    assert isinstance(payload["reference"]["path"], str)
    assert not Path(payload["reference"]["path"]).is_absolute()
    assert str(tmp_path) not in raw.decode("utf-8")
