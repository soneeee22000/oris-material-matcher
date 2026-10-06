"""A67: a policy entry binds the E-08 verifier to the (model, library) it was certified for."""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from oris_matcher.domain.decision import (
    DecisionProfile,
    candidate_thresholds,
    strictest_threshold,
)
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM
from oris_matcher.prompts.v1.verifier import is_verifier_request
from oris_matcher.service import (
    DECISION_PROFILE_KEY,
    MatchService,
    PolicyQuery,
    PolicyResolution,
    ResolvedPolicy,
    RunOptions,
    RunProfile,
    as_override,
    resolve_policy,
)
from oris_matcher.settings import PolicyConfig, PolicyEntry, Settings
from test_service import ALLOWLIST, CONFIG, GPT, HAIKU, LIBRARIES, answers_for
from test_verifier_service import BOQ, SELECT, _wrapper

ENV_FLAG = "ORIS_VERIFIER_ADOPTED"
T3 = candidate_thresholds(2)[2]


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"config_dir": CONFIG, "libraries": LIBRARIES, **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _config(*, verifier: bool) -> tuple[PolicyConfig, str]:
    sha = MatchService.from_settings(make_settings()).library("global").sha256
    entry = PolicyEntry(policy_id="T3", certified_by="dev_selection", verifier_adopted=verifier)
    return PolicyConfig(policies={HAIKU: {sha: entry}}), sha


def _policy(*, verifier: bool) -> ResolvedPolicy:
    return ResolvedPolicy(T3, "T3", PolicyResolution.EXACT, "dev_selection", None, verifier)


# The policy entry and its resolution


def test_an_entry_without_the_key_adopts_no_verifier() -> None:
    entry = PolicyEntry(policy_id="T3", certified_by="dev_selection")
    assert entry.verifier_adopted is False


def test_an_exact_hit_carries_the_entry_verifier() -> None:
    config, sha = _config(verifier=True)
    exact = resolve_policy(config, PolicyQuery(HAIKU, sha, 2))
    assert exact.resolution == PolicyResolution.EXACT
    assert exact.verifier_adopted is True


def test_no_resolution_but_an_entry_adopts_the_verifier() -> None:
    config, sha = _config(verifier=True)
    other = resolve_policy(config, PolicyQuery(GPT, sha, 2))
    no_path = resolve_policy(config, PolicyQuery(HAIKU, sha, 2, path_derived=False))
    forced = resolve_policy(config, PolicyQuery(HAIKU, sha, 2, override=T3))
    assert other.threshold == no_path.threshold == strictest_threshold(2)
    assert not (other.verifier_adopted or no_path.verifier_adopted or forced.verifier_adopted)


def test_a_policy_file_override_keeps_the_entry_verifier_as_claimed() -> None:
    config, sha = _config(verifier=True)
    overridden = as_override(resolve_policy(config, PolicyQuery(HAIKU, sha, 2)))
    assert overridden.resolution == PolicyResolution.OVERRIDE
    assert overridden.certified_by is None
    assert overridden.verifier_adopted is True


# The setting: unset follows the policy; set, it forces


def test_the_setting_is_unset_by_default_and_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert make_settings().verifier_adopted is None
    monkeypatch.setenv(ENV_FLAG, "false")
    assert make_settings().verifier_adopted is False
    monkeypatch.setenv(ENV_FLAG, "true")
    assert make_settings().verifier_adopted is True


@pytest.mark.parametrize(
    ("setting", "entry", "adopted"),
    [
        (None, True, True),
        (None, False, False),
        (False, True, False),
        (True, False, True),
    ],
)
def test_the_profile_follows_the_policy_unless_the_setting_forces_it(
    setting: bool | None, entry: bool, adopted: bool
) -> None:
    resources = MatchService.from_settings(make_settings(verifier_adopted=setting)).resources
    profile = resources.decision_profile(_policy(verifier=entry))
    assert profile == DecisionProfile(verifier_adopted=adopted)


# A run on a config whose entry adopts the verifier


def _config_with_verifier(tmp_path: Path) -> Path:
    """Copy config/ with one entry for (Haiku, global) at T8 that adopts the verifier."""
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    sha = MatchService.from_settings(make_settings()).library("global").sha256
    entry = {"policy_id": "T8", "certified_by": "dev_selection", "verifier_adopted": True}
    policy = {"policies": {HAIKU: {sha: entry}}}
    (config / "policy.yaml").write_text(json.dumps(policy), encoding="utf-8")
    return config


def _run_on(config: Path, **settings: Any) -> tuple[dict[str, Any], int]:
    service = MatchService.from_settings(make_settings(config_dir=config, **settings))
    answers = answers_for(BOQ, service.library("global"))
    fake = FakeLLM(HAIKU, ALLOWLIST, FakeBehaviour(answers=answers, verifier_answers={}))
    options = RunOptions(select=SELECT, run_id="a67")
    result = asyncio.run(
        service.match(BOQ, "global", profile=RunProfile.B3, llm=_wrapper(fake), options=options)
    )
    asked = sum(1 for request in fake.calls if is_verifier_request(request))
    return result.manifest, asked


def test_a_default_run_under_an_adopting_entry_runs_the_verifier(tmp_path: Path) -> None:
    manifest, asked = _run_on(_config_with_verifier(tmp_path))
    assert asked > 0
    assert manifest["policy_resolution"] == "exact"
    assert manifest[DECISION_PROFILE_KEY]["verifier_adopted"] is True


def test_a_run_forced_off_under_an_adopting_entry_asks_no_verifier(tmp_path: Path) -> None:
    manifest, asked = _run_on(_config_with_verifier(tmp_path), verifier_adopted=False)
    assert asked == 0
    assert "verifier_adopted" not in manifest[DECISION_PROFILE_KEY]
