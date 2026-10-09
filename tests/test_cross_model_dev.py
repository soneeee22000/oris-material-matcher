"""`eval/cross_model_dev.py`: the post-freeze cross-model agreement analysis on dev."""

import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOOD = ("Aggregates", "Unbound", "Type 1")
OTHER = ("Aggregates", "Unbound", "Type 2")


def _load() -> ModuleType:
    name = "oris_eval_cross_model_dev"
    spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / "cross_model_dev.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cm = _load()


def _line(decision: str, agreeing: int, correct: bool) -> Any:
    return cm.Line(
        decision=decision,
        agreeing=agreeing,
        primary_correct=correct,
        second_correct=False,
        second_type_correct=False,
    )


@pytest.mark.parametrize(
    ("policy", "decision", "agreeing", "expected"),
    [
        ("shipped", "matched", 0, True),
        ("shipped", "needs_review", 2, False),
        ("rescue_any", "needs_review", 1, True),
        ("rescue_any", "needs_review", 0, False),
        ("rescue_both", "needs_review", 1, False),
        ("rescue_both", "needs_review", 2, True),
        ("rescue_any", "not_a_material", 2, False),
        ("veto_any", "matched", 0, False),
        ("veto_any", "matched", 1, True),
        ("veto_any", "needs_review", 2, False),
    ],
)
def test_policies(policy: str, decision: str, agreeing: int, expected: bool) -> None:
    assert cm.is_matched(_line(decision, agreeing, True), policy) is expected


def test_policy_figures_count_only_the_primary_suggestion() -> None:
    lines = [
        _line("matched", 0, True),
        _line("needs_review", 1, False),
        _line("needs_review", 2, True),
        _line("needs_review", 0, True),
    ]
    rescue = cm.policy_figures(lines, "rescue_any")
    assert (rescue["matched"], rescue["correct"]) == (3, 2)
    assert rescue["coverage"] == pytest.approx(2 / 4)
    assert cm.policy_figures(lines, "veto_any")["matched"] == 0
    assert cm.policy_figures(lines, "veto_any")["cp_lower_bound"] is None


def _votes(primary_top1: str, passes: list[str], decision: str = "needs_review") -> Any:
    return {
        "primary": {"decision": decision, "top1": primary_top1, "suggested": list(GOOD)},
        "second": {"pass_top1": passes, "suggested": list(OTHER)},
    }


def test_classify_counts_agreeing_passes_and_skips_unlabelled_items() -> None:
    items = {
        "01.01.0010.": _votes("T1.U1.S1", ["T1.U1.S1", "T1.U1.S1"]),
        "01.01.0020.": _votes("T1.U1.S1", ["T1.U1.S1", ""]),
        "01.01.0030.": _votes("", ["", ""]),
        "01.01.0040.": _votes("T1.U1.S1", ["T1.U1.S1", "T1.U1.S1"]),
    }
    reference = {
        "01.01.0010.": GOOD,
        "01.01.0020.": OTHER,
        "01.01.0030.": GOOD,
        "01.01.0040.": ("", "", ""),
    }
    lines = cm.classify(items, reference)
    assert [line.agreeing for line in lines] == [2, 1, 0]
    assert [line.primary_correct for line in lines] == [True, False, True]
    assert [line.second_type_correct for line in lines] == [True, True, True]
    assert [line.second_correct for line in lines] == [False, True, False]


def test_pass_top1s_tolerates_unparsable_passes() -> None:
    record = {"raw_line_response": ['{"top1": "T2.U1.S0"}', "not json", '["list"]']}
    assert cm.pass_top1s(record) == ["T2.U1.S0", "", ""]
    assert cm.pass_top1s({}) == []


def _write_run(folder: Path, library_sha: str, top1: str) -> Path:
    folder.mkdir()
    manifest = {"run_id": folder.name, "library_sha256": library_sha, "input_sha256": "in"}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    record = {
        "item_no": "01.01.0010.",
        "top1": top1,
        "raw_line_response": [f'{{"top1": "{top1}"}}'],
    }
    (folder / "audit.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    columns = ["Item No.", "decision", "suggested_type", "suggested_usage", "suggested_subtype"]
    with (folder / "output.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerow(["01.01.0010.", "needs_review", *GOOD])
        writer.writerow(["09.09.0090.", "matched", *GOOD])
    return folder


def test_extract_keeps_dev_items_only_and_refuses_another_library(tmp_path: Path) -> None:
    split = tmp_path / "split.json"
    split.write_text(json.dumps({"item_ids_dev": ["01.01.0010."]}), encoding="utf-8")
    primary = _write_run(tmp_path / "primary", "lib", "T1.U1.S1")
    second = _write_run(tmp_path / "second", "lib", "T1.U1.S1")
    votes = cm.extract(split, {"en": primary}, {"en": second})
    items = votes["languages"]["en"]["items"]
    assert list(items) == ["01.01.0010."]
    assert items["01.01.0010."]["second"]["pass_top1"] == ["T1.U1.S1"]
    other = _write_run(tmp_path / "other", "another-lib", "T1.U1.S1")
    with pytest.raises(cm.AnalysisError, match="library_sha256"):
        cm.extract(split, {"en": primary}, {"en": other})


def test_committed_report_is_current() -> None:
    code = cm.main(
        [
            "report",
            "--votes",
            str(ROOT / "eval" / "cross_model_dev_votes.json"),
            "--reference",
            str(ROOT / "data" / "boq_dataset_matched_GT.csv"),
            "--output",
            str(ROOT / "eval" / "cross_model_dev"),
            "--check",
        ]
    )
    assert code == cm.EXIT_OK


def test_shipped_rows_reproduce_the_dev_ladder() -> None:
    report = json.loads((ROOT / "eval" / "cross_model_dev.json").read_text(encoding="utf-8"))
    shipped = {
        language: (
            figures["policies"]["shipped"]["correct"],
            figures["policies"]["shipped"]["matched"],
        )
        for language, figures in report["languages"].items()
    }
    assert shipped == {"en": (92, 94), "fr": (102, 103)}


def _languages(shipped: tuple[int, int], rescued: tuple[int, int]) -> Any:
    def row(correct: int, matched: int) -> dict[str, Any]:
        return {"correct": correct, "matched": matched, "precision": correct / matched}

    return {"policies": {"shipped": row(*shipped), "rescue_any": row(*rescued)}}


def test_e02d_rule_needs_three_added_correct_at_no_lower_precision() -> None:
    adopted = {"en": _languages((10, 10), (12, 12)), "fr": _languages((10, 10), (11, 11))}
    assert cm.rule_verdict(adopted, "rescue_any") == {
        "added_correct": 3,
        "precision_not_lower": True,
        "adopted": True,
    }
    too_few = {"en": _languages((10, 10), (12, 12)), "fr": _languages((10, 10), (10, 10))}
    assert cm.rule_verdict(too_few, "rescue_any")["adopted"] is False
    less_precise = {"en": _languages((10, 10), (14, 15)), "fr": _languages((10, 10), (10, 10))}
    verdict = cm.rule_verdict(less_precise, "rescue_any")
    assert (verdict["added_correct"], verdict["precision_not_lower"]) == (4, False)
    assert verdict["adopted"] is False


def test_committed_report_applies_the_rule() -> None:
    report = json.loads((ROOT / "eval" / "cross_model_dev.json").read_text(encoding="utf-8"))
    assert report["e02d_rule"]["rescue_any"]["adopted"] is False
    assert report["e02d_rule"]["rescue_both"]["adopted"] is False
