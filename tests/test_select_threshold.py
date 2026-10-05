"""`oris select`: replayed votes re-decided at T1-T8, scored and selected (§10.6, A59.3)."""

import csv
import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from oris_matcher.doctor import LLMSpec, Runtime
from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.boq import BoqFile, LineKind
from oris_matcher.domain.library import Library, load_library
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, default_answer
from oris_matcher.settings import load_models_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SPLIT = ROOT / "eval" / "split_v1.json"
REFERENCE = ROOT / "data" / "boq_dataset_matched_GT.csv"
CLASSES = ROOT / "eval" / "annotations" / "blank_line_classes.csv"
GLOBAL_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
FR_LIBRARY = ROOT / "data" / "oris_materials_fr.csv"
INPUTS = {
    "en": ROOT / "input" / "boq_dataset_input_en.csv",
    "fr": ROOT / "input" / "boq_dataset_input_fr.csv",
}
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
FIXED_NOW = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
ITEMS = 8
CONFIDENCES = (95, 85, 75, 60)
WRONG_EVERY = 4
THRESHOLD_IDS = [f"T{index}" for index in range(1, 9)]
SCORE_RULES = {"D9", "D10"}
B3_ID = "B3-fake"
B2_ID = "R-10.9-evidence"
LANGUAGE_FIELDS = {
    "matched",
    "correct",
    "precision",
    "cp_lower_95",
    "coverage_labelled",
    "h265",
    "review_load_per_100",
    "needs_review",
    "not_a_material",
    "false_not_a_material",
    "mean_cost_usd",
    "item_correct",
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


runner = _load("oris_eval_run_experiment", ROOT / "eval" / "run_experiment.py")
selector = _load("oris_eval_select_threshold", ROOT / "eval" / "select_threshold.py")


class Git:
    """A fake read-only git runner: a clean tree, no tag."""

    def __call__(self, args: Sequence[str], root: Path) -> str:
        del root
        return "d" * 40 if "rev-parse" in args else ""


async def no_sleep(seconds: float) -> None:
    del seconds


def _reference() -> dict[str, tuple[str, str, str]]:
    with REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["Item No."]: (row["material_type"], row["material_usage"], row["material_subtype"])
            for row in csv.DictReader(handle)
        }


def _code(library: Library, position: int, wanted: tuple[str, str, str]) -> str:
    by_triple = {
        (row.material_type, row.material_usage, row.material_subtype): row.code
        for row in library.rows
    }
    if position % WRONG_EVERY != WRONG_EVERY - 1 and wanted in by_triple:
        return by_triple[wanted]
    return library.rows[position % len(library.rows)].code


def answers_for(boq: BoqFile, library: Library, items: frozenset[str]) -> dict[str, Any]:
    """Return the same answer for both passes: mostly the GT row, confidence by position."""
    reference = _reference()
    answers: dict[str, Any] = {}
    chosen = [line for line in boq.lines if line.kind == LineKind.ITEM and line.item_no in items]
    for index, line in enumerate(chosen):
        words = line.short.split()
        answers[transport_id(line)] = {
            **default_answer(transport_id(line)),
            "evidence": words[0] if words else "",
            "top1": _code(library, index, reference.get(line.item_no, ("", "", ""))),
            "confidence": CONFIDENCES[index % len(CONFIDENCES)],
        }
    return answers


def make_runtime(root: Path, answers: dict[str, Any] | None = None) -> Runtime:
    def factory(spec: LLMSpec, model: str) -> FakeLLM:
        del spec
        return FakeLLM(model, ALLOWLIST, FakeBehaviour(answers=answers or {}))

    return Runtime(
        root=lambda: root,
        clock=lambda: FIXED_NOW,
        sleep=no_sleep,
        git=Git(),
        adapter_factory=factory if answers is not None else None,
    )


def write_split(path: Path, dev: Sequence[str]) -> Path:
    payload = json.loads(SPLIT.read_text(encoding="utf-8"))
    every = [*payload["item_ids_dev"], *payload["item_ids_lockbox"]]
    lockbox = [item for item in every if item not in set(dev)]
    path.write_text(
        json.dumps({"item_ids_dev": list(dev), "item_ids_lockbox": lockbox}), encoding="utf-8"
    )
    return path


def first_dev_items(count: int) -> list[str]:
    dev = set(json.loads(SPLIT.read_text(encoding="utf-8"))["item_ids_dev"])
    boq = read_boq(INPUTS["en"])
    items = [line.item_no for line in boq.lines if line.kind == LineKind.ITEM]
    return [item for item in items if item in dev][:count]


@dataclass(frozen=True)
class World:
    """Fake B3 and B2 dev runs of a few items, in both languages."""

    root: Path
    split: Path
    ledger: Path
    runs: dict[str, Path]
    b2_runs: dict[str, Path]
    items: list[str]


def _run(root: Path, split: Path, lang: str, *extra: str) -> Path:
    library = load_library(GLOBAL_LIBRARY.read_bytes())
    items = frozenset(first_dev_items(ITEMS))
    answers = answers_for(read_boq(INPUTS[lang]), library, items)
    argv = [
        "--lang",
        lang,
        "--input",
        str(INPUTS[lang]),
        "--library",
        str(GLOBAL_LIBRARY),
        "--split",
        str(split),
        "--llm",
        "fake",
        "--hypothesis",
        "selection test",
        "--change",
        "none",
        "--runs-dir",
        str(root / "runs"),
        "--ledger",
        str(root / "experiments.jsonl"),
        *extra,
    ]
    assert runner.main(argv, make_runtime(root, answers)) == 0
    row = json.loads((root / "experiments.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    return root / "runs" / str(row["run_id"])


def _environment(patch: pytest.MonkeyPatch, root: Path) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ORIS_PRIMARY_MODEL"):
        patch.delenv(name, raising=False)
    patch.setenv("ORIS_CONFIG_DIR", str(CONFIG))
    libraries = {"global": str(GLOBAL_LIBRARY), "fr": str(FR_LIBRARY)}
    patch.setenv("ORIS_LIBRARIES", json.dumps(libraries))
    patch.chdir(root)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    root = tmp_path_factory.mktemp("select")
    items = first_dev_items(ITEMS)
    split = write_split(root / "split.json", items)
    with pytest.MonkeyPatch.context() as patch:
        _environment(patch, root)
        runs = {lang: _run(root, split, lang, "--id", B3_ID) for lang in ("en", "fr")}
        b2 = {lang: _run(root, split, lang, "--id", B2_ID, "--profile", "b2") for lang in runs}
    yield World(root, split, root / "experiments.jsonl", runs, b2, items)


@pytest.fixture
def env(world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    _environment(monkeypatch, world.root)
    return world


def select_argv(
    world: World, out: Path, *extra: str, runs: dict[str, Path] | None = None
) -> list[str]:
    chosen = runs or world.runs
    return [
        "--run-en",
        str(chosen["en"]),
        "--run-fr",
        str(chosen["fr"]),
        "--split",
        str(world.split),
        "--reference",
        str(REFERENCE),
        "--classes",
        str(CLASSES),
        "--ledger",
        str(world.ledger),
        "--output-json",
        str(out / "selection.json"),
        "--output-md",
        str(out / "selection.md"),
        *extra,
    ]


def run_select(world: World, out: Path, *extra: str, runs: dict[str, Path] | None = None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    code: int = selector.main(select_argv(world, out, *extra, runs=runs), make_runtime(world.root))
    return code


def _audit(result: Any) -> list[dict[str, Any]]:
    return [dict(record) for record in result.audit]


def test_replay_over_t1_to_t8_moves_only_scored_lines(env: World, tmp_path: Path) -> None:
    runtime = make_runtime(env.root)
    redecided = [
        selector.redecide(env.runs["en"], threshold, runtime, tmp_path)
        for threshold in THRESHOLD_IDS
    ]

    matched = [
        sum(item.decision.decision.value == "matched" for item in entry.result.lines)
        for entry in redecided
    ]
    assert matched == sorted(matched)
    assert matched[-1] > matched[0]
    sequences = [[call.request_sha256 for call in entry.result.calls] for entry in redecided]
    assert all(sequence == sequences[0] for sequence in sequences)
    for entry, threshold in zip(redecided, THRESHOLD_IDS, strict=True):
        assert entry.result.policy.resolution.value == "override"
        assert entry.result.policy.policy_id == threshold
        assert entry.result.manifest["mode"] == "replay"
    first = _audit(redecided[0].result)
    for entry in redecided[1:]:
        for before, after in zip(first, _audit(entry.result), strict=True):
            if (before["decision"], before["reason"]) != (after["decision"], after["reason"]):
                assert {before["rule"], after["rule"]} <= SCORE_RULES


def test_selection_output_carries_every_field(env: World, tmp_path: Path) -> None:
    assert run_select(env, tmp_path) == 0
    payload = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))

    assert set(payload) >= {"inputs", "rule", "thresholds", "reference_rows", "selected"}
    assert "sensitivity" in payload
    inputs = payload["inputs"]
    for key in (
        "run_ids",
        "ledger_ids",
        "model",
        "input_sha256",
        "library_sha256",
        "split_sha256",
        "reference_sha256",
        "prompt_versions",
    ):
        assert key in inputs
    assert inputs["ledger_ids"] == {"en": B3_ID, "fr": B3_ID}
    assert inputs["run_ids"]["en"] == env.runs["en"].name
    assert payload["rule"] == {
        "precision_bar": 0.95,
        "min_matched": 40,
        "tie_window": 3,
        "target": None,
    }
    assert [entry["id"] for entry in payload["thresholds"]] == THRESHOLD_IDS
    for entry in payload["thresholds"]:
        assert set(entry["minimum"]) == {"votes", "attributes", "confidence_bucket"}
        assert entry["minimum"]["votes"] == 2
        assert {"summed_correct", "qualifies"} <= set(entry)
        for lang in ("en", "fr"):
            stats = entry["per_language"][lang]
            assert set(stats) >= LANGUAGE_FIELDS
            assert set(stats["item_correct"]) == set(env.items)
    for lang in ("en", "fr"):
        matched = [entry["per_language"][lang]["matched"] for entry in payload["thresholds"]]
        assert matched == sorted(matched)
    assert set(payload["reference_rows"]) == {"match_all", "b2"}
    assert payload["reference_rows"]["b2"]["ledger_id"] == B2_ID
    selected = payload["selected"]
    assert selected["threshold_id"] in THRESHOLD_IDS
    assert selected["status"] in {"meets the dev bar", "below the dev bar"}
    assert selected["dev_bar_met"] is False
    assert set(payload["sensitivity"]["per_language_optimum"]) == {"en", "fr"}
    assert payload["sensitivity"]["target"] is None
    markdown = (tmp_path / "selection.md").read_text(encoding="utf-8")
    assert "| T1 " in markdown
    assert "match-all" in markdown


def test_match_all_reference_row_matches_every_valid_plurality_top1(
    env: World, tmp_path: Path
) -> None:
    assert run_select(env, tmp_path) == 0
    payload = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    loosest = payload["thresholds"][-1]["per_language"]["en"]["matched"]
    match_all = payload["reference_rows"]["match_all"]["per_language"]["en"]["matched"]
    assert match_all >= loosest
    assert match_all == ITEMS


def test_target_adds_only_a_sensitivity_row(env: World, tmp_path: Path) -> None:
    assert run_select(env, tmp_path / "plain") == 0
    assert run_select(env, tmp_path / "target", "--target", "0.5") == 0
    plain = json.loads((tmp_path / "plain" / "selection.json").read_text(encoding="utf-8"))
    target = json.loads((tmp_path / "target" / "selection.json").read_text(encoding="utf-8"))

    assert target["selected"] == plain["selected"]
    assert target["thresholds"] == plain["thresholds"]
    assert target["rule"]["target"] == 0.5
    assert target["sensitivity"]["target"]["value"] == 0.5


def test_selection_is_byte_identical_on_a_second_run(env: World, tmp_path: Path) -> None:
    assert run_select(env, tmp_path / "first") == 0
    assert run_select(env, tmp_path / "second") == 0
    for name in ("selection.json", "selection.md"):
        first = (tmp_path / "first" / name).read_bytes()
        assert first == (tmp_path / "second" / name).read_bytes()
        assert b"\r\n" not in first
    assert str(env.root).encode() not in (tmp_path / "first" / "selection.json").read_bytes()


def test_selection_ignores_config_policy_yaml(
    env: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_select(env, tmp_path / "shipped") == 0
    manifest = json.loads((env.runs["en"] / "manifest.json").read_text(encoding="utf-8"))
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    entry = {"policy_id": "T8", "certified_by": "dev_selection"}
    policy = {"policies": {manifest["requested_model"]: {manifest["library_sha256"]: entry}}}
    (config / "policy.yaml").write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setenv("ORIS_CONFIG_DIR", str(config))

    assert run_select(env, tmp_path / "planted") == 0
    for name in ("selection.json", "selection.md"):
        shipped = (tmp_path / "shipped" / name).read_bytes()
        assert shipped == (tmp_path / "planted" / name).read_bytes()


def test_refuses_a_run_holding_a_non_dev_item(
    env: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    narrow = write_split(tmp_path / "narrow.json", env.items[:-1])
    calls: list[str] = []
    monkeypatch.setattr(selector, "redecide", lambda *args: calls.append("replayed"))
    narrowed = World(env.root, narrow, env.ledger, env.runs, env.b2_runs, env.items[:-1])

    assert run_select(narrowed, tmp_path / "out") == selector.EXIT_REFUSED
    assert calls == []
    assert not (tmp_path / "out" / "selection.json").exists()


def _tampered(world: World, tmp_path: Path, field: str, value: Any) -> dict[str, Path]:
    copy = tmp_path / "tampered" / world.runs["fr"].name
    shutil.copytree(world.runs["fr"], copy)
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    manifest[field] = value
    (copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"en": world.runs["en"], "fr": copy}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fallback_engaged", True),
        ("requested_model", "gpt-4o-mini-2024-07-18"),
        ("library_sha256", "0" * 64),
        ("prompt_version", ["v1+00000000", "v1+11111111"]),
    ],
)
def test_refuses_mismatched_or_fallback_runs(
    env: World, tmp_path: Path, field: str, value: Any
) -> None:
    runs = _tampered(env, tmp_path, field, value)
    assert run_select(env, tmp_path / "out", runs=runs) == selector.EXIT_REFUSED


def test_refuses_a_run_that_is_not_b3(env: World, tmp_path: Path) -> None:
    assert run_select(env, tmp_path, runs=env.b2_runs) == selector.EXIT_REFUSED


def test_select_is_a_passthrough_to_eval_select_threshold() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "from oris_matcher.cli import app; app()", "select", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--run-en" in completed.stdout


def test_script_help_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "eval" / "select_threshold.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
