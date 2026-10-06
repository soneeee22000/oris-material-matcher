"""The shipped config/policy.yaml: Haiku on the global library runs the E-08 verifier (A65, A67)."""

import json

from oris_matcher.service import MatchService, PolicyQuery, PolicyResolution, resolve_policy
from oris_matcher.settings import Settings, load_policy
from test_service import CONFIG, GPT, HAIKU, LIBRARIES, ROOT

SELECTION_E08 = ROOT / "eval" / "selection_v1_e08.json"


def _global_sha() -> str:
    values = {"config_dir": CONFIG, "libraries": LIBRARIES}
    settings = Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]
    return MatchService.from_settings(settings).library("global").sha256


def test_the_shipped_policy_adopts_the_verifier_only_for_haiku_on_the_global_library() -> None:
    config, sha = load_policy(CONFIG / "policy.yaml"), _global_sha()
    haiku = resolve_policy(config, PolicyQuery(HAIKU, sha, 2))
    gpt = resolve_policy(config, PolicyQuery(GPT, sha, 2))
    assert haiku.resolution == PolicyResolution.EXACT
    assert haiku.verifier_adopted is True
    assert gpt.verifier_adopted is False


def test_the_shipped_verifier_claim_is_backed_by_a_re_selection_meeting_the_dev_bar() -> None:
    """A65.1: E-08 is kept only because its re-selection met the dev bar at the shipped id."""
    payload = json.loads(SELECTION_E08.read_text(encoding="utf-8"))
    entry = load_policy(CONFIG / "policy.yaml").lookup(HAIKU, payload["inputs"]["library_sha256"])
    assert entry is not None
    assert entry.verifier_adopted is True
    assert payload["inputs"]["model"] == HAIKU
    assert payload["selected"]["dev_bar_met"] is True
    assert payload["selected"]["threshold_id"] == entry.policy_id
    entries = {entry["id"]: entry for entry in payload["thresholds"]}
    assert entries[entry.policy_id]["measured"] is True
    assert entries[entry.policy_id]["qualifies"] is True
