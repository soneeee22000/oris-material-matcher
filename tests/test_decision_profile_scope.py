"""A64: only a B3 run decides with the profile, so only a B3 run records the arm as on."""

import asyncio
from pathlib import Path

import pytest
from test_drop_conflicting_votes_wiring import (
    FIXED_NOW,
    PRICING,
    _dev_fake,
    _en_split,
    _no_sleep,
    argv,
    isolated,
    ledger_rows,
    make_runtime,
    make_settings,
    runner,
)

from oris_matcher.doctor import read_manifest
from oris_matcher.llm.recording import MemoryCallSink
from oris_matcher.llm.wrapper import BudgetLedger, LLMWrapper, WrapperDeps
from oris_matcher.service import (
    DECISION_PROFILE_KEY,
    MatchService,
    RunOptions,
    RunProfile,
    RunResult,
)

__all__ = ["isolated"]

OFF = {"drop_conflicting_votes": False}
ON = {"drop_conflicting_votes": True}


def _run_profile(profile: RunProfile) -> RunResult:
    boq, chosen, _, fake = _en_split()
    service = MatchService.from_settings(make_settings(drop_conflicting_votes=True))
    deps = WrapperDeps(sink=MemoryCallSink(), sleep=_no_sleep, now=lambda: FIXED_NOW)
    wrapper = LLMWrapper(fake, PRICING, BudgetLedger(100.0), None, deps)
    options = RunOptions(select=frozenset(line.line_id for line in chosen), run_id="scope")
    return asyncio.run(service.match(boq, "global", profile=profile, llm=wrapper, options=options))


@pytest.mark.parametrize(
    ("profile", "recorded"),
    [
        pytest.param(RunProfile.B0, OFF, id="b0"),
        pytest.param(RunProfile.B2, OFF, id="b2"),
        pytest.param(RunProfile.B3, ON, id="b3"),
    ],
)
def test_the_manifest_records_the_profile_that_decided_the_run(
    profile: RunProfile, recorded: dict[str, bool]
) -> None:
    result = _run_profile(profile)
    assert result.manifest[DECISION_PROFILE_KEY] == recorded


def test_a_b2_runner_run_with_the_flag_records_it_off(isolated: Path) -> None:
    args = argv(isolated, "fake", "E-b2-flag", "--profile", "b2", "--drop-conflicting-votes")
    assert runner.main(args, make_runtime(isolated, _dev_fake())) == 0
    row = ledger_rows(isolated)[-1]
    assert row["profile"] == RunProfile.B2.value
    assert row[DECISION_PROFILE_KEY] == OFF
    assert read_manifest(isolated / "runs" / row["run_id"])[DECISION_PROFILE_KEY] == OFF
