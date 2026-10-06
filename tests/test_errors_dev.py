"""`eval/errors_dev.py`: the dev error taxonomy, labeller packet and panel merge (G2-T7)."""

import csv
import importlib.util
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "errors_dev"
MODULE_PATH = ROOT / "eval" / "errors_dev.py"
DATE = "2026-10-06"
BLANK = ("", "", "")
LEAN_12 = ("Concrete", "Lean concrete", "C12/15")
LEAN_16 = ("Concrete", "Lean concrete", "C16/20")
STRUCTURAL = ("Concrete", "Structural concrete", "C30/37")
STEEL = ("Steel", "Reinforcement", "B500B")
NORMAL_BITUMEN = ("Bitumen", "bitumen for asphalt mixtures", "Normal Bitumen")
CAUSES = (
    "lexical_gap",
    "usage_confuser",
    "subtype_parse",
    "header_context",
    "not_in_library",
    "llm_failure",
    "gt_convention",
)
PROTOCOL_FIELDS = [
    "item_id",
    "lang",
    "split",
    "family",
    "kind",
    "level_first_wrong",
    "usage_split",
    "gt_suspect",
    "gt_suspect_reason",
    "cause",
    "cause_2",
    "evidence",
]
TICKET_FIELDS = [
    "top1",
    "reason",
    "v",
    "labeller_a",
    "labeller_b",
    "adjudication_reason",
    "provenance",
    "date",
]
EXPECTED_ERRORS = {
    ("en", "01.01.0020."): ("false_match", "subtype", "Concrete"),
    ("en", "01.01.0030."): ("missed", "decision", "Concrete"),
    ("en", "01.01.0040."): ("wrong_proposal_reviewed", "type", "Bitumen"),
    ("en", "01.01.0050."): ("wrong_proposal_reviewed", "type", "Steel"),
    ("en", "01.01.0060."): ("match_on_blank", "decision", "Bitumen"),
    ("en", "01.01.0080."): ("false_nm", "decision", ""),
    ("en", "01.01.0090."): ("false_nm", "decision", "Bitumen"),
    ("fr", "01.01.0030."): ("false_match", "usage", "Concrete"),
    ("fr", "01.01.0040."): ("wrong_proposal_reviewed", "type", "Bitumen"),
    ("fr", "01.01.0050."): ("wrong_proposal_reviewed", "type", "Steel"),
    ("fr", "01.01.0060."): ("match_on_blank", "decision", "Bitumen"),
    ("fr", "01.01.0090."): ("false_nm", "decision", "Bitumen"),
}
EXPECTED_CANDIDATES = {
    ("en", "01.01.0020."),
    ("en", "01.01.0040."),
    ("en", "01.01.0060."),
    ("fr", "01.01.0030."),
    ("fr", "01.01.0060."),
}
LABEL_FIELDS = ["item_id", "lang", "cause", "cause_2", "usage_split", "evidence"]
ADJUDICATION_FIELDS = [
    *LABEL_FIELDS,
    "adjudication_reason",
    "gt_suspect",
    "gt_suspect_reason",
]


def _load(name: str, path: Path) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


errors_dev = _load("oris_eval_errors_dev_under_test", MODULE_PATH)


def build_argv(out: Path, split: Path = FIXTURES / "split.json", *extra: str) -> list[str]:
    return [
        "build",
        *("--run-en", str(FIXTURES / "runs" / "en"), "--run-fr", str(FIXTURES / "runs" / "fr")),
        *("--input-en", str(FIXTURES / "input_en.csv")),
        *("--input-fr", str(FIXTURES / "input_fr.csv")),
        *("--library", str(FIXTURES / "library.csv")),
        *("--reference", str(FIXTURES / "reference.csv")),
        *("--split", str(split), "--classes", str(FIXTURES / "classes.csv")),
        *("--output", str(out / "errors_dev.csv")),
        *("--packet", str(out / "errors_dev_packet.jsonl")),
        *("--date", DATE),
        *extra,
    ]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_packet(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def built(tmp_path: Path) -> Path:
    assert errors_dev.main(build_argv(tmp_path)) == 0
    return tmp_path


# --- pure helpers ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "ref_class", "gt", "top1", "kind"),
    [
        ("matched", "L", LEAN_12, LEAN_12, None),
        ("matched", "L", LEAN_12, LEAN_16, "false_match"),
        ("matched", "E", BLANK, LEAN_16, "match_on_blank"),
        ("matched", "S", BLANK, LEAN_16, "match_on_blank"),
        ("matched", "A", BLANK, LEAN_16, "match_on_blank"),
        ("matched", "blank", BLANK, LEAN_16, "match_on_blank"),
        ("not_a_material", "L", LEAN_12, None, "false_nm"),
        ("not_a_material", "E", BLANK, None, "false_nm"),
        ("not_a_material", "S", BLANK, None, None),
        ("not_a_material", "A", BLANK, None, None),
        ("needs_review", "L", LEAN_12, LEAN_12, "missed"),
        ("needs_review", "L", LEAN_12, LEAN_16, "wrong_proposal_reviewed"),
        ("needs_review", "L", LEAN_12, None, "wrong_proposal_reviewed"),
        ("needs_review", "E", BLANK, LEAN_16, None),
        ("needs_review", "S", BLANK, None, None),
        ("needs_review", "A", BLANK, LEAN_16, None),
    ],
)
def test_kind_truth_table(
    decision: str,
    ref_class: str,
    gt: tuple[str, str, str],
    top1: tuple[str, str, str] | None,
    kind: str | None,
) -> None:
    assert errors_dev.error_kind(decision, ref_class, gt, top1) == kind


@pytest.mark.parametrize(
    ("kind", "gt", "top1", "level"),
    [
        ("false_match", LEAN_12, STEEL, "type"),
        ("false_match", LEAN_12, STRUCTURAL, "usage"),
        ("false_match", LEAN_12, LEAN_16, "subtype"),
        ("wrong_proposal_reviewed", LEAN_12, STEEL, "type"),
        ("wrong_proposal_reviewed", LEAN_12, STRUCTURAL, "usage"),
        ("wrong_proposal_reviewed", LEAN_12, LEAN_16, "subtype"),
        ("wrong_proposal_reviewed", LEAN_12, None, "type"),
        ("missed", LEAN_12, LEAN_12, "decision"),
        ("false_nm", LEAN_12, None, "decision"),
        ("false_nm", BLANK, None, "decision"),
        ("match_on_blank", BLANK, LEAN_16, "decision"),
    ],
)
def test_first_wrong_level(
    kind: str, gt: tuple[str, str, str], top1: tuple[str, str, str] | None, level: str
) -> None:
    assert errors_dev.first_wrong_level(kind, gt, top1) == level


def test_family_is_gt_type_else_predicted(built: Path) -> None:
    assert errors_dev.family_of(LEAN_12, STEEL) == "Concrete"
    assert errors_dev.family_of(BLANK, STEEL) == "Steel"
    assert errors_dev.family_of(BLANK, None) == ""
    rows = read_rows(built / "errors_dev.csv")
    assert {(row["lang"], row["item_id"]): row["family"] for row in rows} == {
        key: value[2] for key, value in EXPECTED_ERRORS.items()
    }


def _raw(kind: str, top1: str) -> str:
    return json.dumps(
        {
            "confidence": 80,
            "element_or_application": "element",
            "evidence": "concrete",
            "id": "L1",
            "kind": kind,
            "material_family": "family",
            "nm_category": "",
            "self_reported_candidate_gap": "clear",
            "top1": top1,
            "top2": "",
        }
    )


def test_gt_suspect_candidates(built: Path) -> None:
    library = errors_dev.read_library(FIXTURES / "library.csv", "fixture")
    lean_12, lean_16 = "T02.U01.S01", "T02.U01.S02"
    agree = [_raw("material", lean_12), _raw("material", lean_12)]
    assert errors_dev.is_gt_suspect_candidate(agree, 2, library, LEAN_16)
    assert errors_dev.is_gt_suspect_candidate(agree, 2, library, BLANK)
    assert not errors_dev.is_gt_suspect_candidate(agree, 2, library, LEAN_12)
    split = [_raw("material", lean_12), _raw("material", lean_16)]
    assert not errors_dev.is_gt_suspect_candidate(split, 2, library, STEEL)
    one_invalid = ["{not json", _raw("material", lean_12)]
    assert not errors_dev.is_gt_suspect_candidate(one_invalid, 2, library, STEEL)
    not_material = [_raw("no_equivalent", ""), _raw("no_equivalent", "")]
    assert not errors_dev.is_gt_suspect_candidate(not_material, 2, library, STEEL)
    unknown_code = [_raw("material", "T99.U99.S99"), _raw("material", "T99.U99.S99")]
    assert not errors_dev.is_gt_suspect_candidate(unknown_code, 2, library, STEEL)
    assert not errors_dev.is_gt_suspect_candidate(agree[:1], 2, library, STEEL)
    rows = read_rows(built / "errors_dev.csv")
    flagged = {(row["lang"], row["item_id"]) for row in rows if row["gt_suspect_candidate"] == "1"}
    assert flagged == EXPECTED_CANDIDATES


def test_cohen_kappa_known_value() -> None:
    pairs = [("a", "a")] * 20 + [("a", "b")] * 5 + [("b", "a")] * 10 + [("b", "b")] * 15
    agreement = errors_dev.cohen_kappa(pairs)
    assert agreement.n == 50
    assert agreement.raw_agreement == pytest.approx(0.7)
    assert agreement.expected == pytest.approx(0.5)
    assert agreement.kappa == pytest.approx(0.4)
    assert errors_dev.agreement_block(agreement)["kappa"] == pytest.approx(0.4)


def test_cohen_kappa_is_undefined_when_chance_agreement_is_one() -> None:
    agreement = errors_dev.cohen_kappa([("a", "a")] * 6)
    assert agreement.raw_agreement == pytest.approx(1.0)
    assert agreement.expected == pytest.approx(1.0)
    assert agreement.kappa is None
    assert errors_dev.agreement_block(agreement)["kappa"] == "undefined"
    empty = errors_dev.cohen_kappa([])
    assert empty.n == 0
    assert errors_dev.agreement_block(empty)["kappa"] == "undefined"


def _cause_rows(lang: str, causes: Sequence[str], kind: str = "missed") -> list[dict[str, str]]:
    return [{"lang": lang, "cause": cause, "kind": kind} for cause in causes]


def test_trigger_counts() -> None:
    en = ["lexical_gap"] * 5 + ["usage_confuser"] * 4 + ["llm_failure"] * 12
    fr = ["header_context"] * 2 + ["subtype_parse"] + ["gt_convention"] * 7
    counts = errors_dev.trigger_counts(_cause_rows("en", en) + _cause_rows("fr", fr))
    assert counts["en"]["error_rows"] == 21
    assert counts["en"]["causes"]["lexical_gap"] == {
        "count": 5,
        "share": pytest.approx(5 / 21),
        "triggered": True,
    }
    assert counts["en"]["causes"]["usage_confuser"]["triggered"] is False
    assert counts["en"]["triggered"] == ["lexical_gap", "llm_failure"]
    assert counts["fr"]["error_rows"] == 10
    assert counts["fr"]["causes"]["header_context"]["triggered"] is True
    assert counts["fr"]["causes"]["subtype_parse"]["triggered"] is False
    assert counts["fr"]["triggered"] == ["header_context", "gt_convention"]
    assert set(counts["fr"]["causes"]) == set(CAUSES)


def test_trigger_counts_include_missed_rows_and_weight_only_the_display() -> None:
    rows = _cause_rows("en", ["lexical_gap"] * 4, kind="missed") + _cause_rows(
        "en", ["usage_confuser"] * 3, kind="false_match"
    )
    counts = errors_dev.trigger_counts(rows)
    assert counts["en"]["causes"]["lexical_gap"]["count"] == 4
    assert counts["en"]["causes"]["lexical_gap"]["triggered"] is True
    assert counts["en"]["pareto_weighted_display_only"]["usage_confuser"] == 6
    assert counts["en"]["pareto_weighted_display_only"]["lexical_gap"] == 4


# --- the build -------------------------------------------------------------------------


def test_errors_csv_has_the_protocol_fields_and_kinds(built: Path) -> None:
    with (built / "errors_dev.csv").open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))
    assert header[: len(PROTOCOL_FIELDS) + len(TICKET_FIELDS)] == PROTOCOL_FIELDS + TICKET_FIELDS
    rows = read_rows(built / "errors_dev.csv")
    found = {
        (row["lang"], row["item_id"]): (row["kind"], row["level_first_wrong"], row["family"])
        for row in rows
    }
    assert found == EXPECTED_ERRORS
    assert all(row["split"] == "dev" for row in rows)
    assert all(row["provenance"] == "builder labels (assistant panel)" for row in rows)
    assert all(row["date"] == DATE for row in rows)
    assert all(row["cause"] == "" and row["labeller_a"] == "" for row in rows)


def test_errors_rows_carry_top1_reason_v_and_signals(built: Path) -> None:
    rows = {(row["lang"], row["item_id"]): row for row in read_rows(built / "errors_dev.csv")}
    missed = rows["en", "01.01.0030."]
    assert missed["top1"] == " | ".join(STRUCTURAL)
    assert missed["reason"] == "LOW_SIGNAL:v"
    assert missed["rule"] == "D10"
    assert missed["v"] == "1"
    assert missed["signal_b"] == "no_evidence"
    assert rows["en", "01.01.0020."]["v"] == "2"
    assert rows["en", "01.01.0050."]["top1"] == ""
    assert rows["fr", "01.01.0050."]["v"] == "1"


def test_blank_gt_lines_in_review_are_not_errors(built: Path) -> None:
    rows = read_rows(built / "errors_dev.csv")
    keys = {(row["lang"], row["item_id"]) for row in rows}
    assert ("en", "01.01.0070.") not in keys
    assert ("fr", "01.01.0080.") not in keys


def test_packet_holds_line_context_and_no_panel_outputs(built: Path) -> None:
    records = read_packet(built / "errors_dev_packet.jsonl")
    header, rows = records[0], records[1:]
    assert header["record"] == "header"
    assert set(header["cause_definitions"]) == set(CAUSES)
    assert len(rows) == len(EXPECTED_ERRORS)
    row = next(r for r in rows if r["lang"] == "en" and r["item_id"] == "01.01.0030.")
    assert row["line"]["short"] == "Structural concrete C30/37 walls"
    assert row["line"]["unit"] == "m3"
    assert row["line"]["section_path"] == [
        "01. Structures",
        "01.01. Concrete and binders",
    ]
    assert row["gt"]["triple"] == list(STRUCTURAL)
    assert [entry["top1"]["triple"] for entry in row["passes"]] == [
        list(STRUCTURAL),
        list(LEAN_16),
    ]
    assert row["attributes"]["strength_class"] == [30, 37]
    assert set(row["siblings"]) == {"Concrete"}
    assert len(row["siblings"]["Concrete"]) == 3
    banned = {"cause", "cause_2", "labeller_a", "labeller_b", "adjudication_reason"}
    assert not banned & set(row)
    assert not {"gt_suspect", "gt_suspect_reason", "usage_split", "evidence"} & set(row)


def test_packet_decodes_an_invalid_pass_without_failing(built: Path) -> None:
    rows = read_packet(built / "errors_dev_packet.jsonl")[1:]
    row = next(r for r in rows if r["lang"] == "fr" and r["item_id"] == "01.01.0050.")
    first, second = row["passes"]
    assert first["valid"] is False
    assert first["raw"] == "{not json"
    assert second["valid"] is True
    assert second["top1"]["triple"] == list(STEEL)
    assert set(row["siblings"]) == {"Steel"}


def test_build_is_byte_identical_on_a_second_run(built: Path, tmp_path_factory: Any) -> None:
    again = tmp_path_factory.mktemp("again")
    assert errors_dev.main(build_argv(again)) == 0
    for name in ("errors_dev.csv", "errors_dev_packet.jsonl"):
        assert (again / name).read_bytes() == (built / name).read_bytes()


def test_refuses_a_run_holding_a_non_dev_item(tmp_path: Path, capsys: Any) -> None:
    payload = json.loads((FIXTURES / "split.json").read_text(encoding="utf-8"))
    payload["item_ids_dev"].remove("01.01.0040.")
    payload["item_ids_lockbox"].append("01.01.0040.")
    split = tmp_path / "split.json"
    split.write_text(json.dumps(payload), encoding="utf-8")
    assert errors_dev.main(build_argv(tmp_path, split)) == 4
    assert "01.01.0040." in capsys.readouterr().err
    assert not (tmp_path / "errors_dev.csv").exists()


def test_a_library_that_is_not_the_runs_library_is_an_error(tmp_path: Path) -> None:
    shutil.copytree(FIXTURES, tmp_path / "fx")
    library = tmp_path / "fx" / "library.csv"
    library.write_bytes(library.read_bytes() + b"Steel,Reinforcement,B500C\n")
    argv = build_argv(tmp_path)
    argv[argv.index("--library") + 1] = str(library)
    assert errors_dev.main(argv) == 2


def test_show_prints_decision_rule_and_reason(tmp_path: Path, capsys: Any) -> None:
    assert (
        errors_dev.main(build_argv(tmp_path, FIXTURES / "split.json", "--show", "01.01.0070.")) == 0
    )
    out = capsys.readouterr().out
    assert "01.01.0070. en: needs_review D3 NM_UNCONFIRMED (not an error row)" in out
    assert "01.01.0070. fr: needs_review D3 NM_UNCONFIRMED (not an error row)" in out


# --- the panel merge -------------------------------------------------------------------


def _write(path: Path, fields: Sequence[str], rows: Sequence[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _labels(causes: dict[tuple[str, str], str]) -> list[dict[str, str]]:
    return [
        {
            "item_id": item,
            "lang": lang,
            "cause": cause,
            "cause_2": "",
            "usage_split": "",
            "evidence": f"{cause} seen",
        }
        for (lang, item), cause in sorted(causes.items())
    ]


def _panel(tmp_path: Path, labels_b_override: dict[tuple[str, str], str]) -> list[Path]:
    labels_a = dict.fromkeys(EXPECTED_ERRORS, "lexical_gap")
    labels_a["fr", "01.01.0050."] = "llm_failure"
    labels_b = {**labels_a, **labels_b_override}
    blank = dict.fromkeys(ADJUDICATION_FIELDS, "")
    rulings = {
        (lang, item): {
            **blank,
            "item_id": item,
            "lang": lang,
            "gt_suspect": "true" if item == "01.01.0060." else "false",
            "gt_suspect_reason": f"ruling on {item}",
        }
        for lang, item in EXPECTED_CANDIDATES
        if (lang, item) != ("fr", "01.01.0060.")
    }
    rulings["en", "01.01.0020."].update(
        cause="gt_convention",
        adjudication_reason="GT follows the project's lean-base convention",
    )
    adjudication = [rulings[key] for key in sorted(rulings)]
    return [
        _write(tmp_path / "labels_A.csv", LABEL_FIELDS, _labels(labels_a)),
        _write(tmp_path / "labels_B.csv", LABEL_FIELDS, _labels(labels_b)),
        _write(tmp_path / "adjudication.csv", ADJUDICATION_FIELDS, adjudication),
    ]


def merge_argv(built: Path, panel: Sequence[Path]) -> list[str]:
    return [
        "merge",
        *("--errors", str(built / "errors_dev.csv")),
        *("--labels-a", str(panel[0]), "--labels-b", str(panel[1])),
        *("--adjudication", str(panel[2])),
        *("--output", str(built / "merged.csv")),
        *("--summary", str(built / "summary.json")),
    ]


def test_merge_fills_causes_and_reports_agreement(built: Path) -> None:
    panel = _panel(built, {("en", "01.01.0020."): "usage_confuser"})
    assert errors_dev.main(merge_argv(built, panel)) == 0
    rows = {(row["lang"], row["item_id"]): row for row in read_rows(built / "merged.csv")}
    disputed = rows["en", "01.01.0020."]
    assert disputed["cause"] == "gt_convention"
    assert disputed["labeller_a"] == "lexical_gap"
    assert disputed["labeller_b"] == "usage_confuser"
    assert disputed["adjudication_reason"] == "GT follows the project's lean-base convention"
    assert rows["fr", "01.01.0050."]["cause"] == "llm_failure"
    assert rows["en", "01.01.0060."]["gt_suspect"] == "true"
    assert rows["fr", "01.01.0060."]["gt_suspect"] == "true"
    assert rows["fr", "01.01.0060."]["gt_suspect_reason"] == "ruling on 01.01.0060."
    assert rows["en", "01.01.0040."]["gt_suspect"] == "false"
    assert rows["en", "01.01.0030."]["gt_suspect"] == "false"
    assert rows["en", "01.01.0050."]["gt_suspect"] == ""
    summary = json.loads((built / "summary.json").read_text(encoding="utf-8"))
    assert summary["agreement"]["pooled"]["n"] == 12
    assert summary["agreement"]["pooled"]["raw_agreement"] == pytest.approx(11 / 12)
    assert summary["agreement"]["fr"]["kappa"] == pytest.approx(1.0)
    assert summary["agreement"]["en"]["n"] == 7
    assert summary["triggers"]["en"]["causes"]["lexical_gap"]["count"] == 6
    assert summary["triggers"]["en"]["triggered"] == ["lexical_gap"]


def test_merge_refuses_an_out_of_list_cause(built: Path, capsys: Any) -> None:
    panel = _panel(built, {("fr", "01.01.0030."): "bad_cause"})
    assert errors_dev.main(merge_argv(built, panel)) == 2
    assert "bad_cause" in capsys.readouterr().err


def test_merge_refuses_an_unadjudicated_disagreement(built: Path, capsys: Any) -> None:
    panel = _panel(built, {("fr", "01.01.0030."): "usage_confuser"})
    assert errors_dev.main(merge_argv(built, panel)) == 2
    assert "01.01.0030." in capsys.readouterr().err


def test_merge_refuses_a_candidate_without_a_ruling(built: Path, capsys: Any) -> None:
    panel = _panel(built, {})
    rows = [row for row in read_rows(panel[2]) if row["item_id"] != "01.01.0040."]
    _write(panel[2], ADJUDICATION_FIELDS, rows)
    assert errors_dev.main(merge_argv(built, panel)) == 2
    assert "01.01.0040." in capsys.readouterr().err
