"""Output CSV writer: §9.6 columns plus A56, encoding, line endings and attribution."""

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oris_matcher.domain.batching import transport_id
from oris_matcher.domain.boq import BoqFile, LineKind
from oris_matcher.domain.decision import Decision
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.io.writer import OUTPUT_COLUMNS, output_header, render_csv, write_csv
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, default_answer
from oris_matcher.llm.recording import MemoryCallSink
from oris_matcher.llm.wrapper import BudgetLedger, LLMWrapper, WrapperDeps
from oris_matcher.service import RULES_MODEL, MatchService, RunOptions, RunProfile, RunResult
from oris_matcher.settings import Settings, load_models_config, load_pricing

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
SMALL_BOQ = ROOT / "tests" / "fixtures" / "service" / "small_boq.csv"
LIBRARIES = {
    "global": ROOT / "data" / "oris_materials_global.csv",
    "fr": ROOT / "data" / "oris_materials_fr.csv",
}
ALLOWLIST = load_models_config(CONFIG / "models.toml").allowlist.patterns
PRICING = load_pricing(CONFIG / "pricing.toml")
HAIKU = "claude-haiku-4-5-20251001"
FIXED_NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
SERVICE = MatchService.from_settings(
    Settings(_env_file=None, config_dir=CONFIG, libraries=LIBRARIES)  # type: ignore[call-arg]
)
EXPECTED_COLUMNS = (
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
)
BOM = b"\xef\xbb\xbf"


def _code(subtype: str, usage_part: str = "") -> str:
    library = SERVICE.library("global")
    for row in library.rows:
        if row.material_subtype == subtype and usage_part in row.material_usage:
            return row.code
    raise AssertionError(subtype)


def _answers(boq: BoqFile) -> dict[str, dict[str, Any]]:
    concrete = _code("C30/37", "footing")
    by_position: dict[int, dict[str, Any]] = {
        2: {"evidence": "C30/37 footings", "top1": concrete, "top2": _code("C25/30", "footing")},
        3: {"evidence": "supervision", "kind": "non_material", "nm_category": "service"},
        5: {"evidence": "Rebar", "top1": "T99.U99.S99"},
        6: {"evidence": "blinding", "kind": "no_equivalent", "top1": "", "confidence": 40},
    }
    answers: dict[str, dict[str, Any]] = {}
    for line in boq.lines:
        if line.kind == LineKind.ITEM:
            transport = transport_id(line)
            answers[transport] = {
                **default_answer(transport),
                "confidence": 95,
                "top1": "",
                **by_position.get(line.position, {}),
            }
    return answers


async def _result(boq: BoqFile | None = None) -> RunResult:
    boq = boq or read_boq(SMALL_BOQ)
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=_answers(boq)))
    deps = WrapperDeps(sink=MemoryCallSink(), now=lambda: FIXED_NOW)
    wrapper = LLMWrapper(fake, PRICING, BudgetLedger(100.0), None, deps)
    return await SERVICE.match(
        boq, "global", profile=RunProfile.B3, llm=wrapper, options=RunOptions(run_id="w")
    )


def _rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))


def test_output_columns_follow_the_input_columns_in_order() -> None:
    assert OUTPUT_COLUMNS == EXPECTED_COLUMNS
    header = ("Item No.", "Short", "x")
    assert output_header(header) == (*header, *EXPECTED_COLUMNS)


async def test_csv_bytes_utf8_no_bom_crlf_and_raw_cells_unchanged() -> None:
    result = await _result()
    data = render_csv(result)
    assert not data.startswith(BOM)
    assert data.endswith(b"\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")
    source_lines = SMALL_BOQ.read_bytes().split(b"\r\n")
    output_lines = data.split(b"\r\n")
    assert len(output_lines) == len(source_lines)
    for source, output in zip(source_lines[:-1], output_lines[:-1], strict=True):
        assert output.startswith(source + b",")
    rows = _rows(data)
    assert tuple(rows[0]) == output_header(result.input_header)
    width = len(result.input_header)
    for item, row in zip(result.lines, rows[1:], strict=True):
        assert tuple(row[:width]) == item.line.raw_row


async def test_excel_bom_is_optional_and_only_prefixes_the_bytes(tmp_path: Path) -> None:
    result = await _result()
    plain = render_csv(result)
    assert render_csv(result, excel_bom=True) == BOM + plain
    target = tmp_path / "out.csv"
    write_csv(target, result)
    assert target.read_bytes() == plain


async def test_rule_rows_and_label_columns() -> None:
    result = await _result()
    rows = _rows(render_csv(result))
    names = rows[0]
    records = [dict(zip(names, row, strict=True)) for row in rows[1:]]
    for item, record in zip(result.lines, records, strict=True):
        assert record["decision"] == item.decision.decision.value
        assert record["reason"] == item.decision.reason
        if item.line.kind != LineKind.ITEM:
            assert record["cost_usd"] == "0.000000"
            assert record["latency_ms"] == "0"
            assert record["model"] == RULES_MODEL
            assert record["call_ids"] == ""
        else:
            assert record["model"] == HAIKU
            assert record["call_ids"] == ";".join(item.call_ids)
            assert len(record["cost_usd"].split(".")[1]) == 6
            assert record["latency_ms"].isdigit()
        labels = (record["material_type"], record["material_usage"], record["material_subtype"])
        if item.decision.decision != Decision.MATCHED:
            assert labels == ("", "", "")
        else:
            row = item.decision.row
            assert row is not None
            assert labels == (row.material_type, row.material_usage, row.material_subtype)
            assert record["library_row_id"] == row.row_id


async def test_suggestions_and_second_suggestion() -> None:
    result = await _result()
    rows = _rows(render_csv(result))
    records = [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]
    library = SERVICE.library("global")
    concrete = records[2]
    top1 = library.by_code[_code("C30/37", "footing")]
    top2 = library.by_code[_code("C25/30", "footing")]
    assert concrete["suggested_subtype"] == top1.material_subtype
    assert concrete["suggested_usage"] == top1.material_usage
    assert concrete["library_row_id"] == top1.row_id
    assert concrete["suggested2_subtype"] == top2.material_subtype
    assert concrete["suggested2_type"] == top2.material_type
    rebar = records[5]
    assert rebar["reason"] == "INVALID_ROW_ID"
    assert rebar["suggested_type"] == rebar["library_row_id"] == ""
    assert rebar["suggested2_type"] == ""


async def test_attributed_costs_in_the_csv_sum_to_the_run_total() -> None:
    result = await _result(read_boq(ROOT / "input" / "boq_dataset_input_en.csv"))
    rows = _rows(render_csv(result))
    index = rows[0].index("cost_usd")
    written = sum(float(row[index]) for row in rows[1:])
    total = sum(record.cost_usd for record in result.calls)
    assert abs(written - total) <= 0.5e-6 * len(rows)
