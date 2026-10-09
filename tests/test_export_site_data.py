"""`scripts/export_site_data.py`: the project page's data file, derived from committed files."""

import csv
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_site_data.py"
SCORER = ROOT / "eval" / "score.py"
SMOKE_RESULT = ROOT / "eval" / "smoke_fr_v1_result.json"
REFERENCE = ROOT / "data" / "boq_dataset_matched_GT.csv"
SPLIT = ROOT / "eval" / "split_v1.json"
CHANGELOG = ROOT / "CHANGELOG.md"
LIBRARY_GLOBAL = ROOT / "data" / "oris_materials_global.csv"
LIBRARY_FR = ROOT / "data" / "oris_materials_fr.csv"
INPUT_EN = ROOT / "input" / "boq_dataset_input_en.csv"
LEAF_FIELDS = ("material_type", "material_usage", "material_subtype")
RELEASE_LINE = re.compile(r"^## (\S+) \(\d{4}-\d{2}-\d{2}\)$", re.MULTILINE)
OUTPUTS = {
    "en": ROOT / "output" / "improved_output_en.csv",
    "fr": ROOT / "output" / "improved_output_fr.csv",
}
EXIT_OK = 0
EXIT_STALE = 1


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


exporter = _load("oris_scripts_export_site_data", SCRIPT)
scorer = _load("oris_eval_score_for_site_test", SCORER)


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    result: dict[str, Any] = exporter.build_payload()
    return result


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _scored(side: str) -> dict[str, dict[str, Any]]:
    settings = scorer.Settings(
        jobs=[scorer.Job(label=lang, path=path) for lang, path in OUTPUTS.items()],
        reference=REFERENCE,
        key=None,
        strict=True,
        join=scorer.JOIN_KEY,
        split=SPLIT,
        side=side,
        classes=None,
        json_path=None,
    )
    report = scorer.run(settings)
    return {out["label"]: out for out in report["outputs"]}


def test_render_is_byte_identical_across_runs(tmp_path: Path) -> None:
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    assert exporter.main(["--output", str(first)]) == EXIT_OK
    assert exporter.main(["--output", str(second)]) == EXIT_OK
    data = first.read_bytes()
    assert data == second.read_bytes()
    assert data.endswith(b"}\n")
    assert b"\r\n" not in data
    text = data.decode("utf-8")
    assert text == json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def test_generated_from_lists_existing_repo_paths(payload: dict[str, Any]) -> None:
    sources = payload["generated_from"]
    assert sources == sorted(set(sources))
    for source in sources:
        assert "\\" not in source
        assert (ROOT / source).is_file(), source
    for needed in ("eval/score.py", "eval/smoke_fr_v1_result.json", "CHANGELOG.md"):
        assert needed in sources


@pytest.mark.parametrize(("lang", "correct", "matched"), [("en", 88, 89), ("fr", 79, 80)])
def test_lockbox_equals_the_scorer(
    payload: dict[str, Any], lang: str, correct: int, matched: int
) -> None:
    block = payload["lockbox"][lang]
    reference = _scored("lockbox")[lang]
    assert (block["correct"], block["matched"]) == (correct, matched)
    assert block["precision"] == reference["precision"]["value"]
    assert block["cp_lower_95"] == reference["precision"]["cp_lower_95"]
    assert block["coverage"] == reference["coverage_labelled"]["value"]
    assert block["labelled"] == reference["counts"]["labelled"] == 113
    assert block["false_not_a_material"] == reference["false_not_a_material"] == 0
    assert block["per_level_matched"] == reference["per_level_matched"]
    assert round(block["cp_lower_95"], 3) == {"en": 0.948, "fr": 0.942}[lang]


def test_all_labelled_equals_the_scorer(payload: dict[str, Any]) -> None:
    reference = _scored("all")
    for lang in ("en", "fr"):
        block = payload["all_labelled"][lang]
        assert block["labelled"] == reference[lang]["counts"]["labelled"] == 252
        assert block["matched"] == reference[lang]["precision"]["matched"]
        assert block["decisions_all_rows"] == reference[lang]["decision_shares"]["all_rows"]
    assert payload["all_labelled"]["en"]["decisions_all_rows"]["counts"]["matched"] == 185


def test_operations_match_the_readme_rounding(payload: dict[str, Any]) -> None:
    operations = payload["operations"]
    assert round(operations["en"]["cost_per_100_lines_usd"], 2) == 0.22
    assert round(operations["fr"]["cost_per_100_lines_usd"], 2) == 0.21
    assert round(operations["en"]["latency_s_per_routed_line"], 2) == 0.63
    assert round(operations["fr"]["latency_s_per_routed_line"], 2) == 0.68
    assert round(payload["lockbox_session"]["total_spend_usd"], 4) == 1.7982
    assert len(payload["lockbox_session"]["runs"]) == 4


def test_smoke_equals_the_committed_result(payload: dict[str, Any]) -> None:
    committed = json.loads(SMOKE_RESULT.read_text(encoding="utf-8"))
    smoke = payload["smoke_fr"]
    assert smoke["verdict"] == committed["verdict"] == "pass"
    assert smoke["lines"] == committed["lines"]
    assert smoke["base_positives_correct"] == committed["base_positives_correct"]
    for name in ("closed_world_violations", "false_not_a_material", "decoy_matches"):
        assert smoke[name] == committed[name] == 0
    assert {
        row["subset"]: {k: v for k, v in row.items() if k != "subset"} for row in smoke["subsets"]
    } == committed["subsets"]


def test_policies_releases_cases_and_libraries(payload: dict[str, Any]) -> None:
    libraries = {entry["library"]: entry for entry in payload["policies"]}
    assert libraries["global"]["certified_by"] == "dev_selection"
    assert libraries["fr"]["certified_by"] == "smoke_A10"
    assert all(entry["threshold"] == "T8" and entry["verifier"] for entry in payload["policies"])
    versions = [release["version"] for release in payload["releases"]]
    assert versions == RELEASE_LINE.findall(CHANGELOG.read_text(encoding="utf-8"))
    assert {"v1.0", "v1.2.1", "prereg-v1"} <= set(versions)
    cases = payload["traced_cases"]
    assert [case["item"] for case in cases] == [
        "03.02.0040.",
        "03.01.0020.",
        "01.03.0010.",
        "01.01.0010.",
    ]
    assert [case["decision"] for case in cases] == [
        "matched",
        "matched",
        "needs_review",
        "needs_review",
    ]
    assert cases[3]["reason"] == "BUDGET_CAP"
    assert payload["libraries"]["global"]["rows"] == 342


def test_library_tree_is_recomputed_from_the_csvs(payload: dict[str, Any]) -> None:
    tree = payload["library_tree"]
    labelled = [row for row in _csv(REFERENCE) if row["material_type"]]
    usage = Counter(tuple(row[field] for field in LEAF_FIELDS) for row in labelled)
    expected_global = [
        [*(row[field] for field in LEAF_FIELDS), usage[tuple(row[field] for field in LEAF_FIELDS)]]
        for row in _csv(LIBRARY_GLOBAL)
    ]
    assert tree["lib_g"] == expected_global
    assert tree["lib_f"] == [[row[field] for field in LEAF_FIELDS] for row in _csv(LIBRARY_FR)]
    used = [row for row in tree["lib_g"] if row[3] > 0]
    assert (len(used), sum(row[3] for row in used)) == (244, 252)
    assert sum(1 for row in used if row[3] == 1) == 238
    sections = {row["Item No."] for row in _csv(INPUT_EN) if "." not in row["Item No."]}
    assert set(tree["l0"]) == sections
    assert sum(sum(cells.values()) for cells in tree["heat"].values()) == len(labelled)
    concrete = Counter(
        str(int(row["Item No."].split(".")[0]))
        for row in labelled
        if row["material_type"] == "Concrete"
    )
    assert tree["heat"]["Concrete"] == dict(concrete)


def test_dev_ladder_rows_come_from_the_ledger(payload: dict[str, Any]) -> None:
    ladder = payload["dev_ladder"]
    assert ladder["source"] == "eval/experiments.jsonl"
    shipped = ladder["rows"][-1]
    assert shipped["shipped"] is True
    assert (shipped["en"]["correct"], shipped["en"]["matched"]) == (92, 94)
    assert (shipped["fr"]["correct"], shipped["fr"]["matched"]) == (102, 103)


def test_pipeline_rows_follow_the_en_output(payload: dict[str, Any]) -> None:
    pipeline = payload["pipeline_en"]
    rows = pipeline["rows"]
    assert len(rows) == 319
    assert [row["position"] for row in rows] == list(range(319))
    counts = pipeline["counts"]
    assert counts == {"header": 37, "matched": 185, "needs_review": 90, "not_a_material": 7}
    assert sum(1 for row in rows if row["side"] == "lockbox") == 120
    assert pipeline["batches"]["pass_calls"] == [42, 42]
    assert pipeline["batches"]["verifier_calls"] == 40


def test_unparsable_policy_is_a_source_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(_sources: object, _libraries: object) -> list[dict[str, Any]]:
        raise exporter.yaml.YAMLError("bad policy")

    monkeypatch.setattr(exporter, "policy_rows", broken)
    assert exporter.main(["--check"]) == exporter.EXIT_ERROR
    assert "bad policy" in capsys.readouterr().err


def test_check_flags_a_stale_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "data.json"
    assert exporter.main(["--output", str(target)]) == EXIT_OK
    assert exporter.main(["--output", str(target), "--check"]) == EXIT_OK
    target.write_text(target.read_text(encoding="utf-8").replace("88", "87", 1), encoding="utf-8")
    assert exporter.main(["--output", str(target), "--check"]) == EXIT_STALE
    assert "stale" in capsys.readouterr().err
    target.unlink()
    assert exporter.main(["--output", str(target), "--check"]) == EXIT_STALE


SERVICE = ROOT / "src" / "oris_matcher" / "service.py"
ARCHITECTURE_IDS = ["read", "plan", "passes", "fallback", "validate", "verify", "write"]
MATCH_CALLS = {
    "plan": "self._plan(",
    "passes": "self._call_passes(",
    "fallback": "self._rescue(",
    "verify": "self._verify(",
    "write": ".result(",
}


def _defines(source: str, name: str, kind: str) -> bool:
    if kind == "class":
        pattern = rf"^\s*class {re.escape(name)}\b"
    else:
        pattern = rf"^\s*(?:async )?def {re.escape(name)}\b"
    return re.search(pattern, source, re.MULTILINE) is not None


def _match_body() -> str:
    source = SERVICE.read_text(encoding="utf-8")
    start = source.index("    async def match(")
    end = source.index("\n    def ", start)
    return source[start:end]


def test_architecture_code_refs_name_real_symbols(payload: dict[str, Any]) -> None:
    stages = payload["architecture"]["stages"]
    assert stages
    for stage in stages:
        assert stage["code"], stage["id"]
        for ref in stage["code"]:
            path = ROOT / ref["path"]
            assert path.is_file(), ref
            source = path.read_text(encoding="utf-8")
            owner, _, method = ref["symbol"].rpartition(".")
            if owner:
                assert _defines(source, owner, "class"), ref
                assert _defines(source, method, "def"), ref
            else:
                name = ref["symbol"]
                assert _defines(source, name, "def") or _defines(source, name, "class"), ref


def _is_called(name: str) -> bool:
    call = re.compile(rf"(?<!def ){re.escape(name)}\(")
    package = ROOT / "src" / "oris_matcher"
    return any(call.search(path.read_text(encoding="utf-8")) for path in package.rglob("*.py"))


def test_architecture_code_refs_are_called_by_the_package(payload: dict[str, Any]) -> None:
    for stage in payload["architecture"]["stages"]:
        for ref in stage["code"]:
            name = ref["symbol"].rpartition(".")[2]
            assert _is_called(name), ref


def test_architecture_stage_order_follows_match_service(payload: dict[str, Any]) -> None:
    stages = payload["architecture"]["stages"]
    ids = [stage["id"] for stage in stages]
    assert ids == ARCHITECTURE_IDS
    body = _match_body()
    positions = {stage: body.index(marker) for stage, marker in MATCH_CALLS.items()}
    in_stage_order = [positions[stage] for stage in ids if stage in positions]
    assert in_stage_order == sorted(in_stage_order)
    assert len(set(in_stage_order)) == len(MATCH_CALLS)
    for stage in stages:
        assert stage["title"] and stage["summary"] and stage["guarantees"], stage["id"]


def test_architecture_counts_come_from_the_run(payload: dict[str, Any]) -> None:
    pipeline = payload["pipeline_en"]
    stages = {stage["id"]: stage["counts"] for stage in payload["architecture"]["stages"]}
    rows, counts, batches = pipeline["rows"], pipeline["counts"], pipeline["batches"]
    assert stages["read"] == {
        "rows": len(rows),
        "headers": counts["header"],
        "items": len(rows) - counts["header"],
    }
    assert stages["read"]["headers"] == 37
    assert stages["plan"]["batches"] == batches["pass_calls"][0] == 42
    assert stages["plan"]["batch_size"] == batches["batch_size"] == 10
    assert (stages["plan"]["threshold"], stages["plan"]["certified_by"]) == ("T8", "dev_selection")
    assert stages["passes"]["pass_calls"] == batches["pass_calls"] == [42, 42]
    assert stages["passes"]["model"].startswith("claude-haiku-4-5")
    assert stages["fallback"]["engaged"] is False
    assert stages["fallback"]["lines"] == 0
    assert stages["fallback"]["model"].startswith("gpt-4o-mini")
    flagged = [row for row in rows if row["verifier_batch"] is not None]
    validate = stages["validate"]
    assert validate["would_be_matched"] == len(flagged)
    assert validate["routed"] == (
        validate["would_be_matched"] + validate["to_review"] + validate["not_a_material"]
    )
    assert validate["not_a_material"] == counts["not_a_material"] == 7
    assert stages["verify"]["calls"] == batches["verifier_calls"] == 40
    assert stages["verify"]["lines"] == len(flagged)
    assert stages["verify"]["sent_to_review"] == sum(
        1 for row in flagged if row["class"] != "matched"
    )
    assert stages["write"] == {
        "matched": counts["matched"],
        "needs_review": counts["needs_review"],
        "not_a_material": counts["header"] + counts["not_a_material"],
        "headers": counts["header"],
        "items_not_a_material": counts["not_a_material"],
        "rows": len(rows),
        "total_calls": batches["total_calls"],
    }
    assert stages["write"]["not_a_material"] == 44
    assert sum(stages["passes"]["pass_calls"]) + stages["verify"]["calls"] == 124
    assert payload["architecture"]["inactive"] == ["fallback"]


def test_concurrency_comes_from_the_run_manifest(payload: dict[str, Any]) -> None:
    run_id = payload["pipeline_en"]["run_id"]
    manifest_path = ROOT / "runs" / "submission" / run_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    concurrency = manifest["settings_effective"]["concurrency"]
    stages = {stage["id"]: stage["counts"] for stage in payload["architecture"]["stages"]}
    assert concurrency == 4
    assert payload["pipeline_en"]["batches"]["concurrency"] == concurrency
    assert stages["passes"]["concurrency"] == concurrency
    assert stages["verify"]["concurrency"] == concurrency
