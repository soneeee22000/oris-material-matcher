"""`eval/smoke_a10.py`: the A10 smoke verdict for a library without labels (DESIGN §6, A70)."""

import csv
import hashlib
import importlib.util
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
LABEL_COLUMNS = [
    "item_no",
    "subset",
    "expected",
    "material_type",
    "material_usage",
    "material_subtype",
    "is_material",
    "is_concrete",
    "decoy_close",
    "source_item_no",
    "drafted_by",
    "labelled_by",
    "note",
]
OUTPUT_COLUMNS = ["Item No.", "decision", "material_type", "material_usage", "material_subtype"]
LIBRARY = [("Bétons", "Béton prêt à l'emploi", "C25/30"), ("Granulats", "Couche de forme", "")]


def _load() -> ModuleType:
    name = "oris_eval_smoke_a10"
    spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / "smoke_a10.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _label(
    item: str, subset: str, expected: str, triple: tuple[str, ...] = ("", "", "")
) -> list[str]:
    material = "false" if subset == "service" else "true"
    concrete = "true" if triple and triple[0] == "Bétons" else "false"
    real_subset = "handwritten" if subset == "service" else subset
    return [
        item,
        real_subset,
        expected,
        *triple,
        material,
        concrete,
        "false",
        "",
        "agent",
        "owner",
        "",
    ]


RUN_CONFIGURATION = {
    "threshold_id": "T8",
    "requested_model": "claude-haiku-4-5-20251001",
    "enrichment_sha256": "f" * 64,
    "fallback_engaged": False,
    "decision_profile.verifier_adopted": True,
}


def _positives(count: int) -> list[list[str]]:
    exact = [_label(f"01.01.{i:04d}.", "base_exact", "match", LIBRARY[0]) for i in range(10)]
    fr_only = [_label(f"01.02.{i:04d}.", "base_fr_only", "match", LIBRARY[1]) for i in range(8)]
    return (exact + fr_only)[:count]


def _world(
    tmp_path: Path,
    decisions: Mapping[str, tuple[str, tuple[str, ...]]],
    positives: int = 18,
) -> dict[str, Path]:
    """Write a frozen smoke set, a library and a run whose output carries the given decisions."""
    smoke_dir = tmp_path / "smoke"
    smoke_dir.mkdir(parents=True)
    labels = [
        *_positives(positives),
        _label("01.03.0001.", "base_decoy", "no_match"),
        _label("01.03.0002.", "service", "no_match"),
    ]
    _write_csv(smoke_dir / "labels.csv", LABEL_COLUMNS, labels)
    (smoke_dir / "input.csv").write_text("Item No.,Short Description\n", encoding="utf-8")
    library = tmp_path / "library.csv"
    _write_csv(
        library, ["material_type", "material_usage", "material_subtype"], [list(r) for r in LIBRARY]
    )
    freeze = {
        "input_sha256": _sha(smoke_dir / "input.csv"),
        "labels_sha256": _sha(smoke_dir / "labels.csv"),
        "library_sha256": _sha(library),
        "run_configuration": RUN_CONFIGURATION,
    }
    (smoke_dir / "freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    rows = []
    for label in labels:
        item = label[0]
        decision, triple = decisions.get(item, _default(label))
        rows.append([item, decision, *triple])
    _write_csv(run / "output.csv", OUTPUT_COLUMNS, rows)
    audit = [
        {"item_no": row[0], "signals": {"b": "agree" if row[2] == "Bétons" else "none"}}
        for row in rows
    ]
    (run / "audit.jsonl").write_text("".join(json.dumps(a) + "\n" for a in audit), encoding="utf-8")
    manifest = {
        "input_sha256": freeze["input_sha256"],
        "library_sha256": freeze["library_sha256"],
        "spend_usd": 0.01,
        "run_id": "smoke-run",
        "threshold_id": "T8",
        "requested_model": "claude-haiku-4-5-20251001",
        "enrichment_sha256": "f" * 64,
        "fallback_engaged": False,
        "decision_profile": {"drop_conflicting_votes": False, "verifier_adopted": True},
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"smoke": smoke_dir, "library": library, "run": run}


def _default(label: list[str]) -> tuple[str, tuple[str, ...]]:
    """Every positive matched right, every no-match line sent to review."""
    if label[2] == "match":
        return "matched", tuple(label[3:6])
    return "needs_review", ("", "", "")


def _verdict(world: dict[str, Path]) -> dict[str, Any]:
    result: dict[str, Any] = smoke.evaluate(world["smoke"], world["library"], world["run"])
    return result


def test_a_clean_run_passes(tmp_path: Path) -> None:
    result = _verdict(_world(tmp_path, {}))
    assert result["verdict"] == "pass"
    assert result["base_positives_correct"] == {"k": 18, "n": 18}
    assert result["closed_world_violations"] == 0
    assert result["false_not_a_material"] == 0
    assert result["decoy_matches"] == 0
    assert result["signal_b_agree_on_a_concrete_positive"] is True


def test_nine_of_eighteen_base_positives_is_the_bar(tmp_path: Path) -> None:
    review = ("needs_review", ("", "", ""))
    nine_missed = {f"01.01.{i:04d}.": review for i in range(9)}
    assert _verdict(_world(tmp_path / "a", nine_missed))["verdict"] == "pass"
    ten_missed = {f"01.01.{i:04d}.": review for i in range(10)}
    assert _verdict(_world(tmp_path / "b", ten_missed))["verdict"] == "fail"


@pytest.mark.parametrize(
    ("item", "decision", "condition"),
    [
        ("01.03.0001.", ("matched", LIBRARY[0]), "decoy_matches"),
        ("01.01.0000.", ("not_a_material", ("", "", "")), "false_not_a_material"),
        ("01.01.0000.", ("matched", ("Bétons", "Inventé", "X")), "closed_world_violations"),
    ],
)
def test_each_zero_condition_fails_the_verdict(
    tmp_path: Path, item: str, decision: tuple[str, tuple[str, ...]], condition: str
) -> None:
    result = _verdict(_world(tmp_path, {item: decision}))
    assert result[condition] == 1
    assert result["verdict"] == "fail"


def test_a_service_line_sent_to_not_a_material_is_not_a_false_skip(tmp_path: Path) -> None:
    result = _verdict(_world(tmp_path, {"01.03.0002.": ("not_a_material", ("", "", ""))}))
    assert result["false_not_a_material"] == 0
    assert result["verdict"] == "pass"


def test_a_run_of_another_input_or_library_is_refused(tmp_path: Path) -> None:
    world = _world(tmp_path, {})
    manifest = json.loads((world["run"] / "manifest.json").read_text(encoding="utf-8"))
    manifest["input_sha256"] = "0" * 64
    (world["run"] / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(smoke.RefusedError, match="input"):
        _verdict(world)


def test_edited_labels_are_refused(tmp_path: Path) -> None:
    world = _world(tmp_path, {})
    with (world["smoke"] / "labels.csv").open("a", encoding="utf-8") as handle:
        handle.write("01.09.0001.,handwritten,no_match,,,,true,false,false,,agent,owner,\n")
    with pytest.raises(smoke.RefusedError, match="labels"):
        _verdict(world)


def _rewrite_output(world: dict[str, Path], edit: Any) -> None:
    """Apply ``edit`` to the run's output rows (header excluded) and write them back."""
    path = world["run"] / "output.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    _write_csv(path, rows[0], edit(rows[1:]))


def test_an_unlabelled_output_line_is_refused(tmp_path: Path) -> None:
    world = _world(tmp_path, {})
    extra = ["01.09.0001.", "matched", "Bétons", "Inventé", "X"]
    _rewrite_output(world, lambda rows: [*rows, extra])
    with pytest.raises(smoke.RefusedError, match="not labelled"):
        _verdict(world)


def test_a_duplicate_output_item_is_refused(tmp_path: Path) -> None:
    world = _world(tmp_path, {})
    bad = ["01.03.0001.", "matched", *LIBRARY[0]]
    _rewrite_output(world, lambda rows: [bad, *rows])
    with pytest.raises(smoke.RefusedError, match="duplicate"):
        _verdict(world)


def test_a_label_set_without_eighteen_base_positives_is_refused(tmp_path: Path) -> None:
    world = _world(tmp_path, {}, positives=17)
    with pytest.raises(smoke.RefusedError, match="18"):
        _verdict(world)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("threshold_id", "T1"),
        ("requested_model", "gpt-4o-mini-2024-07-18"),
        ("enrichment_sha256", "0" * 64),
        ("fallback_engaged", True),
        ("decision_profile.verifier_adopted", False),
    ],
)
def test_a_run_of_another_configuration_is_refused(tmp_path: Path, key: str, value: Any) -> None:
    world = _world(tmp_path, {})
    path = world["run"] / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    head, _, tail = key.partition(".")
    if tail:
        manifest[head][tail] = value
    else:
        manifest[head] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(smoke.RefusedError, match=key):
        _verdict(world)


def test_a_matched_service_line_is_reported_not_a_decoy(tmp_path: Path) -> None:
    result = _verdict(_world(tmp_path, {"01.03.0002.": ("matched", LIBRARY[0])}))
    assert result["matched_non_material"] == 1
    assert result["decoy_matches"] == 0


def test_main_writes_the_result_files(tmp_path: Path) -> None:
    world = _world(tmp_path, {})
    out = tmp_path / "result"
    argv = ["--smoke", str(world["smoke"]), "--library", str(world["library"])]
    argv += ["--run", str(world["run"]), "--output", str(out)]
    assert smoke.main(argv) == 0
    payload = json.loads((out.with_suffix(".json")).read_text(encoding="utf-8"))
    assert payload["verdict"] == "pass"
    assert "verdict: **pass**" in out.with_suffix(".md").read_text(encoding="utf-8")
