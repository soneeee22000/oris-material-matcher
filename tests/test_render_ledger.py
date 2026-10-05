"""The ledger renderer: eval/experiments.md is rendered from eval/experiments.jsonl (§7.2)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "eval" / "experiments.jsonl"
HEADER_LINES = 6


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


renderer = _load("oris_eval_render_ledger", ROOT / "eval" / "render_ledger.py")


def _metrics(precision: float, coverage: float, cost: float) -> dict[str, Any]:
    return {
        "precision": {"value": precision, "matched": 10, "correct": 7},
        "coverage_labelled": {"value": coverage},
        "false_not_a_material": 0,
        "mean_cost_usd": cost,
    }


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "E-00",
        "date": "2026-10-05T14:30:07.488339+00:00",
        "hypothesis": "baseline",
        "change": "none",
        "run_id": "run-a",
        "lang": "en",
        "metrics": _metrics(0.7, 0.18, 0.0034),
        "before": None,
        "kept": "baseline",
        "prompt_version": ["v1+79d291d0"],
    }
    row.update(overrides)
    return row


def _write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_bytes(text.encode("utf-8"))


def test_header_and_columns_are_kept() -> None:
    text = renderer.render([])
    assert text.startswith("# Experiment ledger\n\nThis ledger is append-only")
    lines = text.split("\n")
    assert lines[4].startswith("| id | date | hypothesis | cause targeted | change | dev before")
    assert lines[5] == "|---|---|---|---|---|---|---|---|---|"
    assert text.endswith("\n")
    assert len(lines) == HEADER_LINES + 1


def test_one_row_per_ledger_row_in_file_order() -> None:
    rows = [_row(id="E-00"), _row(id="E-01", run_id="run-b"), _row(id="E-00", run_id="run-c")]
    body = renderer.render(rows).split("\n")[HEADER_LINES:-1]
    assert [line.split(" | ")[0] for line in body] == ["| E-00", "| E-01", "| E-00"]


def test_cells_are_filled_from_the_row() -> None:
    base = _row()
    after = _row(
        id="E-01",
        run_id="run-b",
        cause_targeted="gt_convention",
        before={"run_id": "run-a", "correct": 7},
        metrics=_metrics(0.9, 0.2, 0.002),
        kept=False,
    )
    line = renderer.render([base, after]).split("\n")[HEADER_LINES + 1]
    cells = [cell.strip() for cell in line.strip("|").split(" | ")]
    assert cells[0] == "E-01"
    assert cells[1] == "2026-10-05 14:30 UTC"
    assert cells[3] == "gt_convention"
    assert cells[5] == "P EN 0.700 · C 0.180 · F_NM 0 · $0.340/100"
    assert cells[6] == "P EN 0.900 · C 0.200 · F_NM 0 · $0.200/100"
    assert cells[7] == "reverted"
    assert cells[8] == "v1+79d291d0"


def test_null_cells_render_as_a_dash() -> None:
    line = renderer.render([_row()]).split("\n")[HEADER_LINES]
    cells = [cell.strip() for cell in line.strip("|").split(" | ")]
    assert cells[3] == "—"
    assert cells[5] == "—"
    assert cells[7] == "baseline"


def test_pipe_and_newline_in_a_cell_are_escaped() -> None:
    line = renderer.render([_row(hypothesis="A | B\nC")]).split("\n")[HEADER_LINES]
    assert "A \\| B C" in line
    assert line.count(" | ") == 8


def test_rendering_twice_is_byte_identical(tmp_path: Path) -> None:
    ledger, output = tmp_path / "experiments.jsonl", tmp_path / "experiments.md"
    _write_ledger(ledger, [_row(), _row(id="E-01", hypothesis="x | y", kept=True)])
    renderer.write(ledger, output)
    first = output.read_bytes()
    renderer.write(ledger, output)
    assert output.read_bytes() == first
    assert b"\r" not in first
    assert first.decode("utf-8") == renderer.render(renderer.read_rows(ledger))
    assert "x \\| y" in first.decode("utf-8")


def test_main_renders_the_real_ledger_deterministically(tmp_path: Path) -> None:
    output = tmp_path / "experiments.md"
    assert renderer.main(["--ledger", str(LEDGER), "--output", str(output)]) == 0
    text = output.read_text(encoding="utf-8")
    rows = renderer.read_rows(LEDGER)
    assert len(text.split("\n")) == HEADER_LINES + len(rows) + 1
    assert text == renderer.render(rows)


def test_a_line_that_is_not_a_json_object_exits_2(tmp_path: Path) -> None:
    ledger, output = tmp_path / "experiments.jsonl", tmp_path / "experiments.md"
    ledger.write_bytes(b'{"id": "E-00"}\n[1, 2]\n')
    assert renderer.main(["--ledger", str(ledger), "--output", str(output)]) == 2
    assert not output.exists()


def test_non_mapping_nested_metrics_render_as_not_available() -> None:
    metrics = {"precision": 0.5, "coverage_labelled": [1], "mean_cost_usd": None}
    line = renderer.render([_row(metrics=metrics)]).split("\n")[HEADER_LINES]
    cells = [cell.strip() for cell in line.strip("|").split(" | ")]
    na = renderer.NOT_AVAILABLE
    assert cells[6] == f"P EN {na} · C {na} · F_NM {na} · ${na}/100"


def test_write_replaces_the_output_and_leaves_no_temp_file(tmp_path: Path) -> None:
    ledger, output = tmp_path / "experiments.jsonl", tmp_path / "experiments.md"
    _write_ledger(ledger, [_row()])
    output.write_bytes(b"stale\n")
    renderer.write(ledger, output)
    assert sorted(path.name for path in tmp_path.iterdir()) == [ledger.name, output.name]
    assert output.read_bytes() == renderer.render(renderer.read_rows(ledger)).encode("utf-8")


def test_verdict_cell_shows_d_and_the_sign_test_p_when_compared() -> None:
    compared = {"kept": False, "d": 12, "sign_test_p": 0.0311}
    assert renderer.verdict_cell(compared) == "reverted (d=12, p=0.0311)"
    assert renderer.verdict_cell({"kept": "baseline", "d": None}) == "baseline"
