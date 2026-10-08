import csv
import hashlib
import importlib.util
import io
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from oris_matcher.io.boq_reader import read_boq

ROOT = Path(__file__).resolve().parents[1]
CHECK_PATH = ROOT / "eval" / "check_requirements.py"
CONFIG_DIR = ROOT / "config"
GLOSSARY = ROOT / "src" / "oris_matcher" / "prompts" / "v1" / "glossary.yaml"
MODEL = "claude-haiku-4-5-20251001"
RQ_IDS = [f"RQ{number}" for number in range(1, 12)]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("oris_eval_check_requirements", CHECK_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()

INPUT_HEADER = ["Item No.", "Short Description", "Long Description", "Unit", "BoQ Qty"]
INPUT_ROWS = [
    ["01.01.", "Concrete works", "", "", ""],
    ["01.01.0010.", "Concrete C30/37", "Supply ready mix", "m³", "10"],
    ["01.01.0020.", "Site supervision", "", "month", "3"],
    ["01.01.0030.", "Unknown thing", "", "m", "5"],
]
OUTPUT_COLUMNS = [
    *INPUT_HEADER,
    "decision",
    "material_type",
    "material_usage",
    "material_subtype",
    "reason",
    "model",
    "prompt_version",
    "latency_ms",
    "cost_usd",
    "suggested_type",
    "suggested_usage",
    "suggested_subtype",
    "library_row_id",
    "call_ids",
    "suggested2_type",
    "suggested2_usage",
    "suggested2_subtype",
]
LIBRARY_ROWS = [["Concrete", "Ready-mix", "C30/37"], ["Steel", "Rebar", ""]]
CONCRETE = ("Concrete", "Ready-mix", "C30/37")
STEEL = ("Steel", "Rebar", "")


def _row(index: int, **cells: str) -> dict[str, str]:
    row = dict.fromkeys(OUTPUT_COLUMNS, "")
    row.update(zip(INPUT_HEADER, INPUT_ROWS[index], strict=True))
    row.update(
        {"model": MODEL, "prompt_version": "v1", "latency_ms": "1200", "cost_usd": "0.001000"}
    )
    row.update(cells)
    return row


def _output_rows() -> list[dict[str, str]]:
    labels = dict(
        zip(("material_type", "material_usage", "material_subtype"), CONCRETE, strict=True)
    )
    suggested = {f"suggested_{name.split('_')[1]}": value for name, value in labels.items()}
    second = dict(
        zip(("suggested2_type", "suggested2_usage", "suggested2_subtype"), STEEL, strict=True)
    )
    return [
        _row(0, decision="not_a_material", reason="HEADER", model="rules", latency_ms="0")
        | {"cost_usd": "0.000000"},
        _row(1, decision="matched", reason="SIGNAL:T1", call_ids="c1", **labels, **suggested)
        | second,
        _row(2, decision="not_a_material", reason="G2_SERVICE", call_ids="c1"),
        _row(3, decision="needs_review", reason="NO_LIBRARY_EQUIVALENT", call_ids="c1"),
    ]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config_shas() -> dict[str, str]:
    shas = {
        f"config/{path.relative_to(CONFIG_DIR).as_posix()}": _sha(path)
        for path in sorted(CONFIG_DIR.rglob("*"))
        if path.is_file()
    }
    shas["src/oris_matcher/prompts/v1/glossary.yaml"] = _sha(GLOSSARY)
    return shas


def _call(line_ids: list[str]) -> dict[str, Any]:
    return {
        "call_id": "c1",
        "parent_call_id": None,
        "attempt_no": 1,
        "reason_for_call": "first",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": MODEL,
        "gen_ai.response.model": MODEL,
        "gen_ai.response.id": "msg_1",
        "provider_request_id": "req_1",
        "gen_ai.response.finish_reasons": ["end_turn"],
        "gen_ai.usage.input_tokens": 100,
        "gen_ai.usage.output_tokens": 50,
        "gen_ai.usage.cache_read": 0,
        "gen_ai.usage.cache_creation": 0,
        "http_status": 200,
        "error_class": None,
        "raw_response": "{}",
        "cost_usd": 0.003,
        "latency_ms": 3600,
        "line_ids": line_ids,
        "cache_hit": False,
        "source_call_id": None,
    }


def _manifest(library_sha: str) -> dict[str, Any]:
    return {
        "mode": "live",
        "source_run_id": None,
        "requested_model": MODEL,
        "fallback_model": None,
        "served_models": [MODEL],
        "library_sha256": library_sha,
        "prompt_version": "v1",
        "enrichment_sha256": None,
        "config_sha256": _config_shas(),
        "policy_resolution": "selected",
        "code_sha": "0123abcd",
        "split_sha256": _sha(ROOT / "eval" / "split_v1.json"),
        "price_date": "2026-10-01",
        "otel_semconv_version": "1.37.0",
        "rate_limit_tier": "tier-1",
        "rate_limit_headers": {"anthropic-ratelimit-requests-limit": "50"},
        "rate_limit_source": "last_live_call",
        "spend_usd": 0.003,
        "attributed_cost_usd": 0.003,
        "cache_hits": 0,
        "wall_clock_s": 3.0,
        "n_routed": 3,
    }


def _csv_bytes(rows: list[list[str]], terminator: str = "\r\n") -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator=terminator).writerows(rows)
    return buffer.getvalue().encode("utf-8")


@dataclass
class World:
    root: Path
    output_rows: list[dict[str, str]] = field(default_factory=_output_rows)
    columns: list[str] = field(default_factory=lambda: list(OUTPUT_COLUMNS))
    manifest: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    output_bytes: Callable[[bytes], bytes] = lambda data: data
    with_run: bool = True
    replay_exit: int = 0

    @property
    def input_path(self) -> Path:
        return self.root / "input.csv"

    @property
    def library_path(self) -> Path:
        return self.root / "library.csv"

    def inputs(self) -> Any:
        output = self.root / "output.csv"
        rows = [
            self.columns,
            *[[row.get(name, "") for name in self.columns] for row in self.output_rows],
        ]
        output.write_bytes(self.output_bytes(_csv_bytes(rows)))
        run = self.root / "run"
        run.mkdir(exist_ok=True)
        (run / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        _write_jsonl(run / "calls.jsonl", self.calls)
        _write_jsonl(run / "audit.jsonl", self.audit)
        script = self.root / "fake_oris.py"
        script.write_text(f"import sys\nsys.exit({self.replay_exit})\n", encoding="utf-8")
        return check.Inputs(
            output=output,
            input=self.input_path,
            library=self.library_path,
            reference=self.root / "reference.csv",
            run=run if self.with_run else None,
            config_dir=CONFIG_DIR,
            oris=(sys.executable, str(script)),
        )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(record) + "\n" for record in records)
    path.write_text(text, encoding="utf-8")


def _audit_record(line_id: str, row: dict[str, str]) -> dict[str, Any]:
    call_ids = [part for part in row["call_ids"].split(";") if part]
    response = [{"id": line_id, "kind": "material"}] if call_ids else None
    return {
        "line_id": line_id,
        "reason": row["reason"],
        "call_ids": call_ids,
        "raw_line_response": response,
    }


@pytest.fixture
def world(tmp_path: Path) -> World:
    built = World(root=tmp_path)
    built.input_path.write_bytes(_csv_bytes([INPUT_HEADER, *INPUT_ROWS]))
    built.library_path.write_bytes(
        _csv_bytes([["material_type", "material_usage", "material_subtype"], *LIBRARY_ROWS])
    )
    reference = [["Item No.", "material_type", "material_usage", "material_subtype"]]
    reference += [[row[0], "", "", ""] for row in INPUT_ROWS]
    reference[2][1:] = list(CONCRETE)
    (tmp_path / "reference.csv").write_bytes(_csv_bytes(reference))
    line_ids = [line.line_id for line in read_boq(built.input_path).lines]
    built.manifest = _manifest(_sha(built.library_path))
    built.calls = [_call(line_ids[1:])]
    built.audit = [
        _audit_record(line_id, row)
        for line_id, row in zip(line_ids, built.output_rows, strict=True)
    ]
    return built


def _statuses(world: World) -> dict[str, Any]:
    return {result.rq: result for result in check.run_checks(world.inputs())}


def _status(world: World, rq: str) -> str:
    result = _statuses(world)[rq]
    status: str = result.status
    return status


# --- the passing world ---------------------------------------------------------------


def test_synthetic_run_passes_every_check(world: World) -> None:
    results = _statuses(world)
    assert set(results) == {"STRICT", *RQ_IDS}
    failures = {rq: result.detail for rq, result in results.items() if result.status != "PASS"}
    assert failures == {}


def test_main_prints_a_table_and_exits_zero(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = world.inputs()
    argv = [
        "--output", str(inputs.output),
        "--input", str(inputs.input),
        "--library", str(inputs.library),
        "--reference", str(inputs.reference),
        "--run", str(inputs.run),
        "--config", str(inputs.config_dir),
        "--oris", inputs.oris[0], inputs.oris[1],
    ]  # fmt: skip
    assert check.main(argv) == 0
    printed = capsys.readouterr().out
    for rq in RQ_IDS:
        assert rq in printed
    assert "PASS" in printed


def test_main_exits_one_on_a_failure(world: World) -> None:
    world.output_rows[1]["decision"] = "maybe"
    inputs = world.inputs()
    argv = ["--output", str(inputs.output), "--input", str(inputs.input)]
    argv += ["--library", str(inputs.library), "--reference", str(inputs.reference)]
    assert check.main(argv) == 1


def test_json_report(world: World, tmp_path: Path) -> None:
    inputs = world.inputs()
    report = tmp_path / "report.json"
    argv = ["--output", str(inputs.output), "--input", str(inputs.input)]
    argv += ["--library", str(inputs.library), "--reference", str(inputs.reference)]
    argv += ["--json", str(report)]
    assert check.main(argv) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    by_rq = {entry["rq"]: entry["status"] for entry in payload["checks"]}
    assert by_rq["RQ11"] == "SKIP"
    assert by_rq["RQ1"] == "PASS"


# --- STRICT ----------------------------------------------------------------------------


def test_strict_scorer_failure(world: World) -> None:
    world.output_rows[3]["Item No."] = "01.01.0020."
    assert _status(world, "STRICT") == "FAIL"


# --- RQ1 columns -----------------------------------------------------------------------


def test_rq1_fails_without_the_second_suggestion(world: World) -> None:
    world.columns = OUTPUT_COLUMNS[:-3]
    assert _status(world, "RQ1") == "FAIL"


def test_rq1_fails_on_reordered_columns(world: World) -> None:
    world.columns = [*OUTPUT_COLUMNS[:5], "material_type", "decision", *OUTPUT_COLUMNS[7:]]
    assert _status(world, "RQ1") == "FAIL"


# --- RQ2 length, order, bytes -----------------------------------------------------------


def test_rq2_fails_on_a_changed_input_cell(world: World) -> None:
    world.output_rows[1]["Short Description"] = "Concrete C30/37 "
    assert _status(world, "RQ2") == "FAIL"


def test_rq2_fails_on_a_dropped_row(world: World) -> None:
    del world.output_rows[3]
    assert _status(world, "RQ2") == "FAIL"


def test_rq2_fails_on_lf_line_endings(world: World) -> None:
    world.output_bytes = lambda data: data.replace(b"\r\n", b"\n")
    assert _status(world, "RQ2") == "FAIL"


def test_rq2_fails_on_a_bom(world: World) -> None:
    world.output_bytes = lambda data: b"\xef\xbb\xbf" + data
    assert _status(world, "RQ2") == "FAIL"


# --- RQ3 decision enum -----------------------------------------------------------------


@pytest.mark.parametrize("decision", ["maybe", "", "Matched"])
def test_rq3_fails_outside_the_enum(world: World, decision: str) -> None:
    world.output_rows[3]["decision"] = decision
    assert _status(world, "RQ3") == "FAIL"


# --- RQ4 closed world ------------------------------------------------------------------


def test_rq4_fails_on_a_triple_outside_the_library(world: World) -> None:
    world.output_rows[1]["material_subtype"] = "C30/37 "
    assert _status(world, "RQ4") == "FAIL"


def test_rq4_fails_on_a_case_change(world: World) -> None:
    world.output_rows[1]["material_type"] = "concrete"
    assert _status(world, "RQ4") == "FAIL"


def test_rq4_fails_on_labels_on_a_review_row(world: World) -> None:
    world.output_rows[3]["material_type"] = "Steel"
    assert _status(world, "RQ4") == "FAIL"


def test_rq4_accepts_a_blank_subtype_leaf(world: World) -> None:
    world.output_rows[1].update(
        {"material_type": "Steel", "material_usage": "Rebar", "material_subtype": ""}
    )
    assert _status(world, "RQ4") == "PASS"


# --- RQ5 not_a_material provenance ----------------------------------------------------


def test_rq5_fails_on_a_model_only_skip(world: World) -> None:
    world.output_rows[3].update(decision="not_a_material", reason="NM_UNCONFIRMED")
    assert _status(world, "RQ5") == "FAIL"


def test_rq5_fails_on_a_service_skip_with_a_measured_unit(world: World) -> None:
    world.output_rows[3].update(decision="not_a_material", reason="G2_SERVICE")
    assert _status(world, "RQ5") == "FAIL"


def test_rq5_fails_on_a_service_skip_with_a_supply_marker(world: World) -> None:
    rows = [list(row) for row in INPUT_ROWS]
    rows[2][2] = "Supply and install the site office"
    world.input_path.write_bytes(_csv_bytes([INPUT_HEADER, *rows]))
    world.output_rows[2]["Long Description"] = rows[2][2]
    assert _status(world, "RQ5") == "FAIL"


def test_rq5_accepts_a_service_unit_alias(world: World) -> None:
    rows = [list(row) for row in INPUT_ROWS]
    rows[2][3] = "mois"
    world.input_path.write_bytes(_csv_bytes([INPUT_HEADER, *rows]))
    world.output_rows[2]["Unit"] = "mois"
    assert check.supply_marker_in("Fourniture et pose de bordures", ("fourniture et pose",))
    assert not check.supply_marker_in("resupplying", ("supply",))
    assert _status(world, "RQ5") == "PASS"


# --- RQ6 audit and call records --------------------------------------------------------


def test_rq6_fails_on_a_missing_audit_record(world: World) -> None:
    del world.audit[2]
    assert _status(world, "RQ6") == "FAIL"


def test_rq6_fails_on_a_missing_raw_line_response(world: World) -> None:
    world.audit[1]["raw_line_response"] = None
    assert _status(world, "RQ6") == "FAIL"


def test_rq6_fails_on_an_unknown_call_id(world: World) -> None:
    world.output_rows[1]["call_ids"] = "c1;c9"
    assert _status(world, "RQ6") == "FAIL"


@pytest.mark.parametrize(
    ("name", "value"),
    [("provider_request_id", None), ("gen_ai.response.finish_reasons", []), ("cost_usd", None)],
)
def test_rq6_fails_on_an_incomplete_successful_call(world: World, name: str, value: Any) -> None:
    world.calls[0][name] = value
    assert _status(world, "RQ6") == "FAIL"


def test_rq6_allows_a_failed_attempt_without_request_id(world: World) -> None:
    failed = world.calls[0] | {
        "call_id": "c0",
        "provider_request_id": None,
        "gen_ai.response.finish_reasons": [],
        "gen_ai.response.model": None,
        "error_class": "APITimeoutError",
        "http_status": None,
        "cost_usd": 0.0,
    }
    world.calls.insert(0, failed)
    assert _status(world, "RQ6") == "PASS"


def test_rq6_skips_without_a_run_folder(world: World) -> None:
    world.with_run = False
    assert _status(world, "RQ6") == "SKIP"


# --- RQ7 cost --------------------------------------------------------------------------


def test_rq7_fails_when_line_costs_do_not_sum(world: World) -> None:
    world.output_rows[1]["cost_usd"] = "0.002000"
    assert _status(world, "RQ7") == "FAIL"


def test_rq7_fails_above_two_dollars_per_hundred_lines(world: World) -> None:
    for row in world.output_rows[1:]:
        row["cost_usd"] = "0.100000"
    world.calls[0]["cost_usd"] = 0.3
    world.manifest.update(spend_usd=0.3, attributed_cost_usd=0.3)
    assert _status(world, "RQ7") == "FAIL"


def test_rq7_skips_a_replayed_run(world: World) -> None:
    world.manifest["mode"] = "replay"
    assert _status(world, "RQ7") == "SKIP"


def test_rq7_skips_a_run_with_cache_hits(world: World) -> None:
    world.manifest["cache_hits"] = 2
    assert _status(world, "RQ7") == "SKIP"


# --- RQ8 latency -----------------------------------------------------------------------


def test_rq8_fails_above_two_seconds_per_routed_line(world: World) -> None:
    world.manifest["wall_clock_s"] = 6.3
    assert _status(world, "RQ8") == "FAIL"


def test_rq8_fails_without_a_wall_clock(world: World) -> None:
    del world.manifest["wall_clock_s"]
    assert _status(world, "RQ8") == "FAIL"


# --- RQ9 manifest ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["code_sha", "split_sha256", "rate_limit_tier", "mode", "otel_semconv_version"]
)
def test_rq9_fails_on_a_missing_pin(world: World, name: str) -> None:
    del world.manifest[name]
    assert _status(world, "RQ9") == "FAIL"


def test_rq9_fails_on_a_config_file_without_a_hash(world: World) -> None:
    del world.manifest["config_sha256"]["config/policy.yaml"]
    assert _status(world, "RQ9") == "FAIL"


def test_rq9_fails_on_a_different_library(world: World) -> None:
    world.manifest["library_sha256"] = "0" * 64
    assert _status(world, "RQ9") == "FAIL"


def test_rq9_fails_when_a_fallback_served_but_is_not_pinned(world: World) -> None:
    world.calls[0]["gen_ai.request.model"] = "gpt-4o-mini-2024-07-18"
    world.calls[0]["gen_ai.response.model"] = "gpt-4o-mini-2024-07-18"
    assert _status(world, "RQ9") == "FAIL"
    world.manifest["fallback_model"] = "gpt-4o-mini-2024-07-18"
    assert _status(world, "RQ9") == "PASS"


# --- RQ10 allowlist --------------------------------------------------------------------


def test_rq10_fails_on_a_large_served_model(world: World) -> None:
    world.calls[0]["gen_ai.response.model"] = "gpt-4o-2024-08-06"
    assert _status(world, "RQ10") == "FAIL"


def test_rq10_fails_on_a_large_configured_fallback(world: World) -> None:
    world.manifest["fallback_model"] = "claude-sonnet-4-5"
    assert _status(world, "RQ10") == "FAIL"


def test_rq10_fails_on_a_large_model_in_served_models(world: World) -> None:
    world.manifest["served_models"] = [MODEL, "gemini-2.5-pro"]
    assert _status(world, "RQ10") == "FAIL"


def _zero_calls(world: World) -> None:
    world.calls = []
    world.manifest["served_models"] = []


def test_rq10_zero_call_run_passes_with_no_served_model(world: World) -> None:
    _zero_calls(world)
    world.manifest["fallback_model"] = "gpt-4o-mini-2024-07-18"
    result = _statuses(world)["RQ10"]
    assert result.status == "PASS"
    configured = ", ".join(sorted([MODEL, "gpt-4o-mini-2024-07-18"]))
    assert result.detail == f"no served model (0 calls); configured on the allowlist: {configured}"


def test_rq10_zero_call_run_with_a_refused_configured_model_fails(world: World) -> None:
    _zero_calls(world)
    world.manifest["fallback_model"] = "claude-sonnet-4-5"
    assert _status(world, "RQ10") == "FAIL"


def test_rq10_served_run_detail_counts_the_models(world: World) -> None:
    result = _statuses(world)["RQ10"]
    assert result.status == "PASS"
    assert result.detail == f"1 model(s) on the allowlist: {MODEL}"


def test_rq10_run_with_calls_but_no_served_models_is_not_a_zero_call_run(world: World) -> None:
    world.manifest["served_models"] = []
    result = _statuses(world)["RQ10"]
    assert result.status == "PASS"
    assert result.detail == f"1 model(s) on the allowlist: {MODEL}"


# --- RQ11 replay -----------------------------------------------------------------------


def test_rq11_fails_when_the_replay_differs(world: World) -> None:
    world.replay_exit = 1
    assert _status(world, "RQ11") == "FAIL"


def test_rq11_skips_without_a_b3_run(world: World) -> None:
    world.with_run = False
    result = _statuses(world)["RQ11"]
    assert result.status == "SKIP"
    assert result.detail == "skipped: no B3 run"


def test_rq11_fails_when_oris_is_missing(world: World) -> None:
    inputs = world.inputs()
    missing = check.Inputs(**{**vars(inputs), "oris": (str(world.root / "no_such_oris"),)})
    results = {result.rq: result for result in check.run_checks(missing)}
    assert results["RQ11"].status == "FAIL"


def test_no_skips_turns_a_skip_into_a_failure(world: World) -> None:
    inputs = world.inputs()
    argv = ["--output", str(inputs.output), "--input", str(inputs.input)]
    argv += ["--library", str(inputs.library), "--reference", str(inputs.reference)]
    assert check.main([*argv, "--no-skips"]) == 1


def test_unreadable_input_exits_two(world: World) -> None:
    inputs = world.inputs()
    argv = ["--output", str(inputs.output), "--input", str(world.root / "missing.csv")]
    argv += ["--library", str(inputs.library), "--reference", str(inputs.reference)]
    assert check.main(argv) == 2


@pytest.mark.parametrize("mode", ["fake", "replay", "cached"])
@pytest.mark.parametrize("name", ["rate_limit_tier", "rate_limit_headers", "rate_limit_source"])
def test_rq9_allows_null_rate_limits_on_a_non_live_run_but_needs_the_keys(
    world: World, mode: str, name: str
) -> None:
    world.manifest.update(
        mode=mode, rate_limit_tier=None, rate_limit_headers=None, split_sha256=None
    )
    world.manifest["rate_limit_source"] = "none"
    assert _status(world, "RQ9") == "PASS"
    world.manifest["rate_limit_tier"] = "unknown"
    assert _status(world, "RQ9") == "PASS"
    del world.manifest[name]
    assert _status(world, "RQ9") == "FAIL"


@pytest.mark.parametrize("name", ["rate_limit_tier", "rate_limit_headers", "split_sha256"])
def test_rq9_fails_on_a_null_rate_limit_or_split_on_a_live_run(world: World, name: str) -> None:
    world.manifest[name] = None
    assert _status(world, "RQ9") == "FAIL"


def test_rq9_fails_on_an_unknown_tier_on_a_live_run(world: World) -> None:
    world.manifest["rate_limit_tier"] = "unknown"
    assert _status(world, "RQ9") == "FAIL"


@pytest.mark.parametrize(("mode", "hits"), [("fake", 0), ("replay", 0), ("cached", 3), ("live", 1)])
def test_rq8_is_skipped_unless_a_cold_live_run(world: World, mode: str, hits: int) -> None:
    world.manifest.update(mode=mode, cache_hits=hits)
    result = _statuses(world)["RQ8"]
    assert result.status == "SKIP"
    assert result.detail.startswith("skipped (not a cold live run)")


def test_json_report_is_byte_portable(world: World, tmp_path: Path) -> None:
    inputs = world.inputs()
    report = tmp_path / "report.json"
    argv = ["--output", str(inputs.output), "--input", str(inputs.input)]
    argv += ["--library", str(inputs.library), "--reference", str(inputs.reference)]
    assert check.main([*argv, "--json", str(report)]) == 0
    raw = report.read_bytes()
    assert b"\r" not in raw
    payload = json.loads(raw)
    expected = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert raw.decode("utf-8") == expected


@pytest.mark.parametrize("mode", ["live", "cached", "replay", "fake", "rules"])
def test_rq9_accepts_every_run_mode_the_service_writes(world: World, mode: str) -> None:
    world.manifest.update(mode=mode)
    if mode != "live":
        world.manifest.update(rate_limit_tier=None, rate_limit_headers=None, split_sha256=None)
    assert _status(world, "RQ9") == "PASS"


@pytest.mark.parametrize("mode", ["offline", "LIVE", ""])
def test_rq9_fails_on_an_unknown_run_mode(world: World, mode: str) -> None:
    world.manifest["mode"] = mode
    assert _status(world, "RQ9") == "FAIL"


def test_checks_runs_only_the_named_checks_without_the_reference(
    world: World, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """G3-T19: CI runs RQ1-RQ5 on a B0 output and never reads the ground truth (§10.3)."""
    inputs = world.inputs()
    argv = ["--output", str(inputs.output), "--input", str(inputs.input)]
    argv += ["--library", str(inputs.library), "--reference", str(tmp_path / "absent.csv")]
    argv += ["--checks", "RQ1,RQ2,RQ3,RQ4,RQ5"]
    assert check.main(argv) == 0
    printed = capsys.readouterr().out
    assert [line.split()[0] for line in printed.splitlines()[1:]] == [
        "RQ1",
        "RQ2",
        "RQ3",
        "RQ4",
        "RQ5",
    ]
    assert "STRICT" not in printed


def test_checks_refuses_an_unknown_check(world: World) -> None:
    inputs = world.inputs()
    argv = ["--output", str(inputs.output), "--input", str(inputs.input)]
    argv += ["--library", str(inputs.library), "--checks", "RQ1,RQ99"]
    assert check.main(argv) == 2
