"""`eval/report.py`: risk-coverage, precision by v and by gap on dev (G2-T9, A60.10-11)."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "report"
SCRIPT = ROOT / "eval" / "report.py"
LABELLED_DEV = 7
SEED = 1234
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_REFUSED = 4
LOCKBOX_ITEM = "01.02.0010."

# Hand-worked from the fixture lines (tests/fixtures/report, see each audit.jsonl):
# (minimum score of the step, matched, correct) in descending score order.
EXPECTED_STEPS = {
    "en": [
        ({"v": 2, "b": "agree", "confidence_bucket": ">=90"}, 2, 2),
        ({"v": 2, "b": "agree", "confidence_bucket": "80-89"}, 3, 2),
        ({"v": 2, "b": "no_evidence", "confidence_bucket": "80-89"}, 4, 3),
        ({"v": 2, "b": "no_evidence", "confidence_bucket": "70-79"}, 5, 3),
        ({"v": 2, "b": "no_evidence", "confidence_bucket": "<70"}, 6, 4),
    ],
    "fr": [
        ({"v": 2, "b": "agree", "confidence_bucket": "80-89"}, 2, 2),
        ({"v": 2, "b": "no_evidence", "confidence_bucket": ">=90"}, 3, 3),
        ({"v": 2, "b": "no_evidence", "confidence_bucket": "70-79"}, 4, 3),
        ({"v": 2, "b": "no_evidence", "confidence_bucket": "<70"}, 5, 4),
    ],
}
POPULATION = 8
THRESHOLDS = {
    "en": {"T1": (2, 2), "T2": (3, 2), "T5": (3, 2), "T6": (4, 3), "T7": (5, 3), "T8": (6, 4)},
    "fr": {"T1": (0, 0), "T2": (2, 2), "T5": (3, 3), "T7": (4, 3), "T8": (5, 4)},
}
MARKS = {
    "en": {"B1": (0.5, 1 / 7), "B2": (5 / 8, 5 / 7), "match-all": (0.75, 6 / 7)},
    "fr": {"B1": (1.0, 1 / 7), "B2": (4 / 6, 4 / 7), "match-all": (5 / 7, 5 / 7)},
}


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


report = _load("oris_eval_report", SCRIPT)


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    shutil.copytree(FIXTURE, world)
    return world


def _argv(world: Path, out: Path) -> list[str]:
    return [
        *("--run-en", str(world / "b3_en"), "--run-fr", str(world / "b3_fr")),
        *("--selection", str(world / "selection.json"), "--b1", str(world / "b1")),
        *("--b2-en", str(world / "b2_en"), "--b2-fr", str(world / "b2_fr")),
        *("--reference", str(world / "reference.csv"), "--split", str(world / "split.json")),
        *("--classes", str(world / "classes.csv"), "--library", str(world / "library.csv")),
        *("--assets-dir", str(out / "assets"), "--output-json", str(out / "report.json")),
        *("--seed", str(SEED)),
    ]


def _run(world: Path, out: Path) -> dict[str, Any]:
    assert report.main(_argv(world, out)) == EXIT_OK
    payload: dict[str, Any] = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return payload


@pytest.fixture(scope="module")
def produced(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    base = tmp_path_factory.mktemp("report")
    out = base / "out"
    return out, _run(_world(base), out)


def _sidecar(out: Path, name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (out / "assets" / f"{name}.json").read_text(encoding="utf-8")
    )
    return payload


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_figure_points_equal_recomputed_points(
    produced: tuple[Path, dict[str, Any]], lang: str
) -> None:
    out, payload = produced
    sidecar = _sidecar(out, f"risk_coverage_{lang}")
    assert (out / "assets" / f"risk_coverage_{lang}.svg").is_file()
    curve = sidecar["curve"]
    assert [point["score"] for point in curve] == [step[0] for step in EXPECTED_STEPS[lang]]
    for point, (_, matched, correct) in zip(curve, EXPECTED_STEPS[lang], strict=True):
        assert (point["matched"], point["correct"]) == (matched, correct)
        assert point["precision"] == pytest.approx(correct / matched)
        assert point["coverage_labelled"] == pytest.approx(correct / LABELLED_DEV)
        assert point["selective_coverage"] == pytest.approx(matched / POPULATION)
        assert point["risk"] == pytest.approx(1 - correct / matched)
    marks = {mark["name"]: mark for mark in sidecar["marks"]}
    for threshold_id, (matched, correct) in THRESHOLDS[lang].items():
        assert (marks[threshold_id]["matched"], marks[threshold_id]["correct"]) == (
            matched,
            correct,
        )
    for name, (precision, coverage) in MARKS[lang].items():
        assert marks[name]["precision"] == pytest.approx(precision)
        assert marks[name]["coverage_labelled"] == pytest.approx(coverage)
    assert marks["T8"]["selected"] is True
    assert not any(mark["selected"] for name, mark in marks.items() if name != "T8")
    assert payload["risk_coverage"][lang]["curve"] == curve
    assert payload["risk_coverage"][lang]["population"]["lines"] == POPULATION


def test_equal_scores_are_one_step() -> None:
    lines = [
        report.CurveLine("a", (2, 1, 3), matchable=True, correct=True),
        report.CurveLine("b", (2, 1, 3), matchable=True, correct=False),
        report.CurveLine("c", (2, 0, 1), matchable=True, correct=True),
        report.CurveLine("d", (2, 1, 3), matchable=False, correct=True),
        report.CurveLine("e", None, matchable=False, correct=False),
    ]
    steps = report.curve_steps(lines)
    assert [step.rank for step in steps] == [(2, 1, 3), (2, 0, 1)]
    assert [sorted(step.items) for step in steps] == [["a", "b"], ["a", "b", "c"]]
    assert [(step.matched, step.correct) for step in steps] == [(2, 1), (3, 2)]


def _manual_band(
    weights: np.ndarray, items: list[str], matched: set[str], correct: set[str], labelled: set[str]
) -> dict[str, Any]:
    def vector(chosen: set[str]) -> np.ndarray:
        return np.array([float(item in chosen) for item in items])

    total_matched = weights @ vector(matched)
    total_correct = weights @ vector(correct)
    total_labelled = weights @ vector(labelled)
    keep = (total_matched > 0) & (total_labelled > 0)
    precision = total_correct[keep] / total_matched[keep]
    coverage = total_correct[keep] / total_labelled[keep]
    return {
        "precision": [float(value) for value in np.percentile(precision, [2.5, 97.5])],
        "coverage_labelled": [float(value) for value in np.percentile(coverage, [2.5, 97.5])],
        "dropped": int((~keep).sum()),
    }


def test_bootstrap_uses_the_same_draw_across_thresholds(
    produced: tuple[Path, dict[str, Any]],
) -> None:
    out, payload = produced
    bootstrap = payload["bootstrap"]
    assert (bootstrap["seed"], bootstrap["resamples"]) == (SEED, report.RESAMPLES)
    assert report.RESAMPLES == 1000
    items = json.loads((FIXTURE / "split.json").read_text(encoding="utf-8"))["item_ids_dev"]
    weights = report.resample_weights(sorted(items), SEED, report.RESAMPLES)
    assert bootstrap["draw_sha256"] == report.draw_sha256(weights)
    labelled = set(sorted(items)[:7])
    en = {mark["name"]: mark for mark in _sidecar(out, "risk_coverage_en")["marks"]}
    fr = {mark["name"]: mark for mark in _sidecar(out, "risk_coverage_fr")["marks"]}
    for threshold_id in ("T2", "T3", "T4", "T5"):
        assert en[threshold_id]["band"] == en["T2"]["band"]
    en_t8 = {"01.01.0010.", "01.01.0020.", "01.01.0030.", "01.01.0040.", "01.01.0050."}
    en_t8 |= {"01.01.0070."}
    en_t8_correct = {"01.01.0010.", "01.01.0030.", "01.01.0040.", "01.01.0050."}
    fr_t8 = {"01.01.0010.", "01.01.0020.", "01.01.0030.", "01.01.0050.", "01.01.0060."}
    fr_t8_correct = {"01.01.0010.", "01.01.0020.", "01.01.0030.", "01.01.0060."}
    sorted_items = sorted(items)
    expected_en = _manual_band(weights, sorted_items, en_t8, en_t8_correct, labelled)
    expected_fr = _manual_band(weights, sorted_items, fr_t8, fr_t8_correct, labelled)
    for found, expected in ((en["T8"]["band"], expected_en), (fr["T8"]["band"], expected_fr)):
        assert found["dropped"] == expected["dropped"]
        assert found["precision"] == pytest.approx(expected["precision"])
        assert found["coverage_labelled"] == pytest.approx(expected["coverage_labelled"])
    assert fr["T1"]["band"]["dropped"] == report.RESAMPLES


def test_precision_by_v_population(produced: tuple[Path, dict[str, Any]]) -> None:
    out, payload = produced
    by_v = payload["by_v"]
    assert by_v["en"]["population"] == 8
    assert by_v["fr"]["population"] == 7
    assert by_v["en"]["levels"]["1"] == {"n": 1, "correct": 1, "precision": 1.0}
    assert by_v["en"]["levels"]["2"]["n"] == 7
    assert by_v["en"]["levels"]["2"]["correct"] == 5
    assert by_v["fr"]["levels"]["1"] == {"n": 1, "correct": 0, "precision": 0.0}
    assert (by_v["fr"]["levels"]["2"]["n"], by_v["fr"]["levels"]["2"]["correct"]) == (6, 5)
    share = payload["systematic_error_share"]
    assert share["en"] == {"wrong_v_k": 2, "wrong": 2, "value": 1.0}
    assert share["fr"] == {"wrong_v_k": 1, "wrong": 2, "value": 0.5}
    assert _sidecar(out, "precision_by_v")["by_v"] == by_v


def _answer(top1: str, gap: str, kind: str = "material", confidence: int = 80) -> str:
    return json.dumps(
        {
            "confidence": confidence,
            "element_or_application": "e",
            "evidence": "word",
            "id": "L1",
            "kind": kind,
            "material_family": "f",
            "nm_category": "",
            "self_reported_candidate_gap": gap,
            "top1": top1,
            "top2": "",
        }
    )


def test_a_line_with_an_invalid_pass_is_outside_v() -> None:
    record = {"raw_line_response": [_answer("T03.U01.S01", "clear"), "{not json"]}
    assert report.read_vote(record, passes=2) is None
    short = {"raw_line_response": [_answer("T03.U01.S01", "clear")]}
    assert report.read_vote(short, passes=2) is None
    split_kinds = {
        "raw_line_response": [
            _answer("T03.U01.S01", "clear"),
            _answer("", "clear", kind="non_material"),
        ]
    }
    assert report.read_vote(split_kinds, passes=2) is None


def test_gap_is_the_weakest_supporter(produced: tuple[Path, dict[str, Any]]) -> None:
    out, payload = produced
    record = {
        "raw_line_response": [
            _answer("T03.U01.S01", "decisive"),
            _answer("T03.U01.S01", "narrow"),
            _answer("T02.U01.S01", "tossup"),
        ]
    }
    vote = report.read_vote(record, passes=3)
    assert vote is not None
    assert (vote.top1, vote.votes, vote.gap) == ("T03.U01.S01", 2, "narrow")
    by_gap = payload["by_gap"]
    assert by_gap["label"] == "verbal ordinal judgement, not a probability"
    en = by_gap["en"]["levels"]
    assert [en[level]["n"] for level in ("decisive", "clear", "narrow", "tossup")] == [1, 3, 2, 1]
    assert [en[level]["correct"] for level in ("decisive", "clear", "narrow", "tossup")] == [
        1,
        3,
        0,
        1,
    ]
    fr = by_gap["fr"]["levels"]
    assert [fr[level]["n"] for level in ("decisive", "clear", "narrow", "tossup")] == [1, 3, 1, 1]
    assert [fr[level]["correct"] for level in ("decisive", "clear", "narrow", "tossup")] == [
        1,
        3,
        0,
        1,
    ]
    assert _sidecar(out, "precision_by_gap")["by_gap"] == by_gap


def _append_lockbox_record(audit: Path) -> None:
    record = {"item_no": LOCKBOX_ITEM, "rule": "D9", "raw_line_response": [], "signals": None}
    with audit.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")


@pytest.mark.parametrize("folder", ["b3_fr", "b2_en"])
def test_refuses_a_run_holding_a_non_dev_item(tmp_path: Path, folder: str) -> None:
    world = _world(tmp_path)
    _append_lockbox_record(world / folder / "audit.jsonl")
    out = tmp_path / "out"
    assert report.main(_argv(world, out)) == EXIT_REFUSED
    assert not (out / "report.json").exists()
    assert not (out / "assets").exists()


def test_disagreement_with_the_selection_item_correct_is_an_error(tmp_path: Path) -> None:
    world = _world(tmp_path)
    path = world / "selection.json"
    selection = json.loads(path.read_text(encoding="utf-8"))
    selection["thresholds"][7]["per_language"]["en"]["item_correct"]["01.01.0040."] = 0
    path.write_text(json.dumps(selection), encoding="utf-8")
    assert report.main(_argv(world, tmp_path / "out")) == EXIT_ERROR


def test_a_run_not_replayed_from_the_selection_source_is_an_error(tmp_path: Path) -> None:
    world = _world(tmp_path)
    path = world / "b3_en" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["source_run_id"] = "another-run"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert report.main(_argv(world, tmp_path / "out")) == EXIT_ERROR


def test_outputs_are_byte_identical_on_a_second_run(
    tmp_path: Path, produced: tuple[Path, dict[str, Any]]
) -> None:
    first, _ = produced
    second = tmp_path / "again"
    _run(_world(tmp_path), second)
    names = sorted(path.name for path in (first / "assets").iterdir())
    assert names == sorted(path.name for path in (second / "assets").iterdir())
    assert {"risk_coverage_en.svg", "precision_by_v.svg", "precision_by_gap.svg"} <= set(names)
    for name in names:
        assert (first / "assets" / name).read_bytes() == (second / "assets" / name).read_bytes()
    svg = (first / "assets" / "risk_coverage_en.svg").read_text(encoding="utf-8")
    assert "<dc:date>" not in svg


def test_help_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, check=False, timeout=60
    )
    assert completed.returncode == EXIT_OK
