"""The shipped config/policy.yaml: Haiku on the global library runs E-08 and E-01 (A67, A68)."""

import hashlib
import json

from oris_matcher.service import MatchService, PolicyQuery, PolicyResolution, resolve_policy
from oris_matcher.settings import Settings, load_policy
from test_service import CONFIG, GPT, HAIKU, LIBRARIES, ROOT

SELECTION_E01 = ROOT / "eval" / "selection_v1_e01.json"


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


def test_the_shipped_entry_is_backed_by_a_re_selection_meeting_the_dev_bar() -> None:
    """A67.2, A68.6: the shipped id is the re-selection over the kept configuration's runs."""
    payload = json.loads(SELECTION_E01.read_text(encoding="utf-8"))
    entry = load_policy(CONFIG / "policy.yaml").lookup(HAIKU, payload["inputs"]["library_sha256"])
    assert entry is not None
    assert entry.verifier_adopted is True
    assert payload["inputs"]["model"] == HAIKU
    assert payload["inputs"]["ledger_ids"] == {"en": "E-01-C2", "fr": "E-01-C2"}
    assert payload["selected"]["dev_bar_met"] is True
    assert payload["selected"]["threshold_id"] == entry.policy_id
    entries = {entry["id"]: entry for entry in payload["thresholds"]}
    assert entries[entry.policy_id]["measured"] is True
    assert entries[entry.policy_id]["qualifies"] is True


def test_the_shipped_entry_binds_the_enrichment_it_was_measured_with() -> None:
    """A68.4: the entry names data/enrichment/global.yaml with the SHA-256 the E-01-C2 runs used."""
    entry = load_policy(CONFIG / "policy.yaml").lookup(HAIKU, _global_sha())
    assert entry is not None
    assert entry.enrichment == "data/enrichment/global.yaml"
    file_sha = hashlib.sha256((ROOT / entry.enrichment).read_bytes()).hexdigest()
    assert entry.enrichment_sha256 == file_sha
    for run in ("20261007T135938Z-aff2738a", "20261007T140156Z-cec6ab8e"):
        recorded = ROOT / "runs" / "submission" / run / "manifest.json"
        manifest = json.loads(recorded.read_text(encoding="utf-8"))
        assert manifest["enrichment_sha256"] == file_sha
