"""Golden replay of a recorded live B2 dev slice (DESIGN.md §9.6, §11.2, §11.7).

``tests/fixtures/golden/`` holds three complete batches (20 dev items) of the live B2 dev EN run
``20261005T151020Z-33c38c05``: their ``calls.jsonl`` records copied byte for byte, the item ids
in ``selection.json`` and the output CSV the replay must reproduce. Complete batches keep every
request byte unchanged (same lines, same ``L<position>`` transport ids, same order), so a strict
``ReplayLLM`` serves each one and a miss raises instead of reaching a model. The expected CSV
was rendered from this replay under the 25-word evidence tolerance (A59.1) and is checked row
for row against the run's replayed output ``runs/b2-dev-en-w25.csv``; it is compared as bytes
(CRLF as the writer emits), so the test holds on Linux and Windows alike.
"""

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from oris_matcher.doctor import Runtime, WrapperSetup, make_wrapper
from oris_matcher.domain.batching import plan_batches
from oris_matcher.domain.boq import BoqFile, LineKind
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.io.writer import render_csv
from oris_matcher.llm.recording import read_calls_jsonl
from oris_matcher.llm.replay_llm import RecordedRun, ReplayLLM
from oris_matcher.service import MatchService, RunOptions, RunProfile, RunResult, budget_cap_usd
from oris_matcher.settings import Settings, load_models_config, load_pricing

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
GOLDEN = ROOT / "tests" / "fixtures" / "golden"
CALLS = GOLDEN / "calls.jsonl"
SELECTION = GOLDEN / "selection.json"
EXPECTED = GOLDEN / "expected_output.csv"
EN_INPUT = ROOT / "input" / "boq_dataset_input_en.csv"
SPLIT = ROOT / "eval" / "split_v1.json"
LIBRARIES = {
    "global": ROOT / "data" / "oris_materials_global.csv",
    "fr": ROOT / "data" / "oris_materials_fr.csv",
}
ENCODING = "utf-8"
GOLDEN_RUN_ID = "golden-replay"
FIXED_NOW = datetime(2026, 10, 5, 15, 10, 20, tzinfo=UTC)
GOLDEN_ITEM_COUNT = 20
GOLDEN_REASONS = {"SIGNAL:B2", "NM_UNCONFIRMED"}
MALFORMED_REASON = "LLM_FAILURE:malformed"
ITEM_COLUMN = "Item No."
LINES_REJECTED_AT_12_WORDS = frozenset(
    {"00.03.0030.", "00.03.0040.", "01.02.0160.", "01.02.0200.", "01.02.0210."}
)
REASON_COLUMN = "reason"


def _selection() -> dict[str, Any]:
    """Return the fixture's ``selection.json``."""
    payload: dict[str, Any] = json.loads(SELECTION.read_text(encoding=ENCODING))
    return payload


def _settings() -> Settings:
    """Return repository settings that read no ``.env`` file and so no key."""
    return Settings(_env_file=None, config_dir=CONFIG, libraries=LIBRARIES)


def _select(boq: BoqFile, item_ids: frozenset[str]) -> frozenset[str]:
    """Return the rule-decided rows plus the chosen item lines, as the experiment runner does."""
    rule_rows = [line for line in boq.lines if line.kind != LineKind.ITEM]
    chosen = [line for line in boq.lines if line.kind == LineKind.ITEM and line.item_no in item_ids]
    return frozenset(line.line_id for line in (*rule_rows, *chosen))


async def _no_sleep(seconds: float) -> None:
    """Skip retry waits; a replay never needs one."""
    del seconds


async def replay_golden() -> RunResult:
    """Replay the golden slice through the service with a strict ``ReplayLLM``.

    Returns:
        The replayed run.

    """
    selection = _selection()
    settings = _settings()
    boq = read_boq(EN_INPUT)
    select = _select(boq, frozenset(selection["item_ids"]))
    allowlist = load_models_config(CONFIG / "models.toml").allowlist.patterns
    model = str(selection["requested_model"])
    replay = ReplayLLM(RecordedRun.from_calls_jsonl(CALLS), model, allowlist, strict=True)
    runtime = Runtime(root=lambda: ROOT, clock=lambda: FIXED_NOW, sleep=_no_sleep)
    setup = WrapperSetup(budget_cap_usd(settings, len(select)), run_id=GOLDEN_RUN_ID)
    wrapper = make_wrapper(replay, load_pricing(CONFIG / "pricing.toml"), settings, setup, runtime)
    options = RunOptions(
        select=select, run_id=GOLDEN_RUN_ID, source_run_id=str(selection["source_run_id"])
    )
    service = MatchService.from_settings(settings)
    return await service.match(
        boq, str(selection["library_id"]), profile=RunProfile.B2, llm=wrapper, options=options
    )


def _csv_rows(data: bytes) -> list[dict[str, str]]:
    """Parse output CSV bytes into rows keyed by column."""
    return list(csv.DictReader(io.StringIO(data.decode(ENCODING), newline="")))


def test_golden_items_are_dev_items_only() -> None:
    split = json.loads(SPLIT.read_text(encoding=ENCODING))
    items = set(_selection()["item_ids"])

    assert len(items) == GOLDEN_ITEM_COUNT
    assert items <= set(split["item_ids_dev"])
    assert not items & set(split["item_ids_lockbox"])


def test_golden_selection_plans_exactly_the_recorded_batches() -> None:
    boq = read_boq(EN_INPUT)
    select = _select(boq, frozenset(_selection()["item_ids"]))
    batches = plan_batches(line for line in boq.lines if line.line_id in select)
    recorded = [record.line_ids for record in read_calls_jsonl(CALLS)]

    assert [list(batch.transport_ids) for batch in batches] == [list(ids) for ids in recorded]


async def test_golden_replay_is_byte_identical_to_the_committed_output() -> None:
    result = await replay_golden()

    assert render_csv(result) == EXPECTED.read_bytes()
    assert result.manifest["mode"] == "replay"
    assert len(result.calls) == len(read_calls_jsonl(CALLS))
    assert all(record.cache_hit for record in result.calls)


def test_golden_output_covers_matched_and_unconfirmed_lines() -> None:
    reasons = {row[REASON_COLUMN] for row in _csv_rows(EXPECTED.read_bytes())}

    assert reasons >= GOLDEN_REASONS


def test_golden_lines_rejected_at_12_words_are_decided_at_25() -> None:
    rows = {row[ITEM_COLUMN]: row for row in _csv_rows(EXPECTED.read_bytes())}

    assert set(rows) >= LINES_REJECTED_AT_12_WORDS
    assert {rows[item][REASON_COLUMN] for item in LINES_REJECTED_AT_12_WORDS}.isdisjoint(
        {MALFORMED_REASON}
    )
    assert MALFORMED_REASON not in {row[REASON_COLUMN] for row in rows.values()}


def test_golden_output_rows_are_rows_of_the_recorded_run_output() -> None:
    source = ROOT / str(_selection()["source_output"])
    if not source.is_file():
        pytest.skip(f"{source.as_posix()} is a local run artifact and is not present")
    recorded = _csv_rows(source.read_bytes())
    golden = _csv_rows(EXPECTED.read_bytes())

    assert [row for row in recorded if row in golden] == golden
