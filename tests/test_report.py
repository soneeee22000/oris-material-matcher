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
from matplotlib.backends.backend_agg import FigureCanvasAgg

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "report"
SCRIPT = ROOT / "eval" / "report.py"
LABELLED_DEV = 7
SEED = 1234
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_REFUSED = 4
LOCKBOX_ITEM = "01.02.0010."
POINTS_PER_INCH = 72

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
# EN 01.01.0065. is a D5a veto carrying a top score; FR 01.01.0070. is a D5 whose passes agree
# on a code outside library.csv; FR 01.01.0080. is a D1b partial signal (settled, not counted).
EXPECTED_BY_RULE = {
    "en": {"D10": 1, "D5a": 1, "D9": 6},
    "fr": {"D10": 1, "D5": 1, "D7": 1, "D9": 5},
}
THRESHOLDS = {
    "en": {"T1": (2, 2), "T2": (3, 2), "T5": (3, 2), "T6": (4, 3), "T7": (5, 3), "T8": (6, 4)},
    "fr": {"T1": (0, 0), "T2": (2, 2), "T5": (3, 3), "T7": (4, 3), "T8": (5, 4)},
}
GAP_LEVELS = ("decisive", "clear", "narrow", "tossup")
EXPECTED_GAP = {"en": ([1, 3, 2, 1], [1, 3, 0, 1]), "fr": ([1, 3, 1, 1], [1, 3, 0, 1])}
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
    assert payload["risk_coverage"][lang]["curve"] == curve
    assert payload["risk_coverage"][lang]["population"] == {
        "lines": POPULATION,
        "by_rule": EXPECTED_BY_RULE[lang],
    }


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_figure_marks_equal_recomputed_marks(
    produced: tuple[Path, dict[str, Any]], lang: str
) -> None:
    out, _ = produced
    marks = {mark["name"]: mark for mark in _sidecar(out, f"risk_coverage_{lang}")["marks"]}
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


def test_gap_is_the_weakest_supporter() -> None:
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


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_precision_by_gap_levels(produced: tuple[Path, dict[str, Any]], lang: str) -> None:
    out, payload = produced
    by_gap = payload["by_gap"]
    assert by_gap["label"] == "verbal ordinal judgement, not a probability"
    levels = by_gap[lang]["levels"]
    expected_n, expected_correct = EXPECTED_GAP[lang]
    assert [levels[gap]["n"] for gap in GAP_LEVELS] == expected_n
    assert [levels[gap]["correct"] for gap in GAP_LEVELS] == expected_correct
    assert _sidecar(out, "precision_by_gap")["by_gap"] == by_gap


def _append_lockbox_record(audit: Path) -> None:
    record: dict[str, Any] = {
        "item_no": LOCKBOX_ITEM,
        "rule": "D9",
        "raw_line_response": [],
        "signals": None,
    }
    with audit.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")


def _append_lockbox_row(output: Path) -> None:
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{LOCKBOX_ITEM},matched,Concrete,Foundations,C30/37,SIGNAL\n")


def _edit_json(path: Path, key: str, value: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    payload[key] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


@pytest.mark.parametrize("folder", ["b3_fr", "b2_en", "b1"])
def test_refuses_a_run_holding_a_non_dev_item(tmp_path: Path, folder: str) -> None:
    world = _world(tmp_path)
    if folder == "b1":
        _append_lockbox_row(world / "b1" / "output_en.csv")
    else:
        _append_lockbox_record(world / folder / "audit.jsonl")
    out = tmp_path / "out"
    assert report.main(_argv(world, out)) == EXIT_REFUSED
    assert not (out / "report.json").exists()
    assert not (out / "assets").exists()


def test_refuses_a_run_that_is_not_b3(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _edit_json(world / "b3_fr" / "manifest.json", "profile", "b2")
    out = tmp_path / "out"
    assert report.main(_argv(world, out)) == EXIT_REFUSED
    assert not (out / "report.json").exists()


def test_a_run_decided_at_another_threshold_is_an_error(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _edit_json(world / "b3_en" / "manifest.json", "threshold_id", "T7")
    out = tmp_path / "out"
    assert report.main(_argv(world, out)) == EXIT_ERROR
    assert not (out / "report.json").exists()


def test_another_library_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    world = _world(tmp_path)
    with (world / "library.csv").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("Steel,Rebar,B500C\n")
    out = tmp_path / "out"
    assert report.main(_argv(world, out)) == EXIT_ERROR
    assert "--library" in capsys.readouterr().err
    assert not (out / "report.json").exists()


def test_the_selected_flag_follows_the_selection(tmp_path: Path) -> None:
    world = _world(tmp_path)
    selection = json.loads((world / "selection.json").read_text(encoding="utf-8"))
    _edit_json(
        world / "selection.json", "selected", {**selection["selected"], "threshold_id": "T7"}
    )
    for lang in ("en", "fr"):
        _edit_json(world / f"b3_{lang}" / "manifest.json", "threshold_id", "T7")
    payload = _run(world, tmp_path / "out")
    assert payload["inputs"]["selected"] == "T7"
    for lang in ("en", "fr"):
        marks = payload["risk_coverage"][lang]["marks"]
        assert [mark["name"] for mark in marks if mark["selected"]] == ["T7"]


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


# (C252, P) of T1-T8 and B1, B2, match-all in the real G2 B3-dev-sel runs: T3 and T4 share a
# point next to T5, and T7 sits next to T8.
REAL_GEOMETRY = {
    "en": {
        "thresholds": [
            (0.014, 1.0),
            (0.18, 0.862),
            (0.209, 0.806),
            (0.209, 0.806),
            (0.252, 0.814),
            (0.46, 0.853),
            (0.612, 0.885),
            (0.647, 0.891),
        ],
        "references": [(0.309, 0.413), (0.791, 0.791), (0.755, 0.761)],
    },
    "fr": {
        "thresholds": [
            (0.022, 1.0),
            (0.173, 0.8),
            (0.223, 0.795),
            (0.223, 0.795),
            (0.245, 0.81),
            (0.439, 0.884),
            (0.604, 0.913),
            (0.683, 0.913),
        ],
        "references": [(0.086, 0.429), (0.813, 0.813), (0.82, 0.844)],
    },
}


def _mark(name: str, kind: str, position: tuple[float, float]) -> dict[str, Any]:
    coverage, precision = position
    return {
        "name": name,
        "kind": kind,
        "coverage_labelled": coverage,
        "precision": precision,
        "selected": name == "T8",
        "band": None,
    }


def _geometry_sidecar(lang: str) -> dict[str, Any]:
    geometry = REAL_GEOMETRY[lang]
    thresholds = [
        _mark(f"T{index}", "threshold", position)
        for index, position in enumerate(geometry["thresholds"], start=1)
    ]
    references = [
        _mark(name, "reference", position)
        for name, position in zip(("B1", "B2", "match-all"), geometry["references"], strict=True)
    ]
    curve = [
        {"coverage_labelled": x, "precision": y, "band": None}
        for x, y in dict.fromkeys(geometry["thresholds"])
    ]
    return {"lang": lang, "labelled": 252, "curve": curve, "marks": [*thresholds, *references]}


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_threshold_labels_clear_every_other_point(lang: str) -> None:
    sidecar = _geometry_sidecar(lang)
    figure = report.risk_coverage_figure(sidecar)
    canvas = FigureCanvasAgg(figure)
    canvas.draw()
    renderer = canvas.get_renderer()
    axes = figure.axes[0]
    marker = report.MARKER_SIZE * figure.dpi / POINTS_PER_INCH
    for label in axes.texts:
        box = label.get_window_extent(renderer).expanded(1.0, 1.0).padded(marker / 2)
        own = axes.transData.transform(label.xy)
        for mark in sidecar["marks"]:
            point = axes.transData.transform((mark["coverage_labelled"], mark["precision"]))
            if np.allclose(point, own):
                continue
            assert not box.contains(*point), f"{label.get_text()} covers {mark['name']}"


def test_a_label_turns_away_from_its_nearest_neighbour() -> None:
    spans = (1.0, 1.0)
    assert report.label_quadrant((0.5, 0.5), [], spans) == (1, 1)
    assert report.label_quadrant((0.5, 0.5), [(0.52, 0.51)], spans) == (-1, -1)
    assert report.label_quadrant((0.5, 0.5), [(0.48, 0.49)], spans) == (1, 1)
    assert report.label_quadrant((0.5, 0.5), [(0.53, 0.49)], spans) == (-1, 1)
