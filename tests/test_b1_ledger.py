"""B1 ledger rows: the TF-IDF baseline recorded in ``eval/experiments.jsonl`` (G2-T0)."""

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
SHA_PREFIX = 8
G1_PRECISION = {"en": (43, 104), "fr": (12, 28)}
G1_COVERAGE = {"en": 0.309, "fr": 0.086}
DIGITS = 3


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


b1_ledger = _load("oris_eval_b1_ledger", ROOT / "eval" / "b1_ledger.py")
runner = _load("oris_eval_run_experiment", ROOT / "eval" / "run_experiment.py")


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("b1_root")
    argv = ["--ledger", str(root / "experiments.jsonl"), "--runs-dir", str(root / "runs")]
    assert b1_ledger.main(argv, root=root, clock=lambda: FIXED_NOW) == 0
    return root


def _rows(root: Path) -> list[dict[str, Any]]:
    text = (root / "experiments.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_b1_appends_one_ledger_row_per_language_with_one_id(recorded: Path) -> None:
    rows = _rows(recorded)
    assert [row["lang"] for row in rows] == ["en", "fr"]
    assert {row["id"] for row in rows} == {"B1-dev"}
    summary = next((recorded / "runs").iterdir()) / "summary.json"
    sha8 = hashlib.sha256(summary.read_bytes()).hexdigest()[:SHA_PREFIX]
    threshold = json.loads(summary.read_text(encoding="utf-8"))["threshold"]
    for row in rows:
        assert row["run_id"] == f"b1-dev-{sha8}"
        assert row["output"] == f"runs/b1-dev-{sha8}/output_{row['lang']}.csv"
        expected = {
            "profile": "b1",
            "llm": "tfidf-b1",
            "mode": "offline",
            "spend_usd": 0,
            "attributed_cost_usd": 0,
            "prompt_version": [],
            "policy_resolution": f"b1_cosine_{threshold:.4f}",
            "latency_s_per_routed": None,
            "kept": "baseline",
            "change": f"cosine threshold {threshold:.4f} (§10.6 on dev)",
            "side": "dev",
            "slice_sha256": None,
            "limit": None,
            "baseline_id": None,
            "cause_targeted": None,
            "status": "scored",
            "date": FIXED_NOW.isoformat(),
        }
        assert {key: row[key] for key in expected} == expected
        assert row["metrics"]["strict"] is True
        assert row["metrics"]["false_not_a_material"] == 0
        precision = row["metrics"]["precision"]
        assert (precision["correct"], precision["matched"]) == G1_PRECISION[row["lang"]]
        coverage = round(row["metrics"]["coverage_labelled"]["value"], DIGITS)
        assert coverage == G1_COVERAGE[row["lang"]]
    markdown = (recorded / "experiments.md").read_text(encoding="utf-8")
    assert markdown.count("| B1-dev |") == len(rows)


def test_b1_rerun_keeps_one_run_folder_and_appends_two_more_rows(
    tmp_path: Path,
) -> None:
    argv = ["--ledger", str(tmp_path / "experiments.jsonl"), "--runs-dir", str(tmp_path / "runs")]
    for _ in range(2):
        assert b1_ledger.main(argv, root=tmp_path, clock=lambda: FIXED_NOW) == 0
    folders = sorted(path.name for path in (tmp_path / "runs").iterdir())
    assert len(folders) == 1
    assert folders[0].startswith("b1-dev-")
    assert len(_rows(tmp_path)) == len(b1_ledger.LANGUAGES) * 2


def test_b1_missing_input_exits_2_without_a_row_or_a_temp_folder(tmp_path: Path) -> None:
    argv = ["--ledger", str(tmp_path / "experiments.jsonl"), "--runs-dir", str(tmp_path / "runs")]
    argv += ["--input-en", str(tmp_path / "no_such_input.csv")]
    assert b1_ledger.main(argv, root=tmp_path, clock=lambda: FIXED_NOW) == b1_ledger.EXIT_ERROR
    assert not (tmp_path / "experiments.jsonl").exists()
    assert list((tmp_path / "runs").iterdir()) == []


def test_b1_baseline_output_resolves(recorded: Path) -> None:
    for row in _rows(recorded):
        args = argparse.Namespace(runs_dir=recorded / "runs", baseline_id="B1-dev")
        found = runner._baseline_output(args, row, recorded)
        assert found == recorded / "runs" / row["run_id"] / f"output_{row['lang']}.csv"
        assert found.is_file()
