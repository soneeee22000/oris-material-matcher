import csv
import hashlib
import json
import os
from datetime import date
from pathlib import Path

import pytest
from pydantic import SecretStr

from oris_matcher.settings import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_BREAKER_CONSECUTIVE_FAILURES,
    DEFAULT_BUDGET_USD_PER_100_LINES,
    DEFAULT_CONCURRENCY,
    DEFAULT_LINE_BUDGET_CALLS,
    DEFAULT_LINE_BUDGET_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_PASSES_K,
    DEFAULT_PER_CALL_TIMEOUT_S,
    MODELS_FILE,
    NEVER_MATCH_FILE,
    POLICY_FILE,
    PRICING_FILE,
    SERVICE_UNITS_FILE,
    SUPPLY_MARKERS_FILE,
    UNIT_ALIASES_FILE,
    ConfigError,
    Settings,
    is_model_allowed,
    load_models_config,
    load_never_match,
    load_policy,
    load_pricing,
    load_service_units,
    load_supply_markers,
    load_unit_aliases,
    resolve_models,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
LIBRARY_FILES = (
    REPO_ROOT / "data" / "oris_materials_global.csv",
    REPO_ROOT / "data" / "oris_materials_fr.csv",
)
VENDOR_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
MEASURED_UNITS = (
    "t",
    "T",
    "m",
    "m²",
    "m2",
    "m³",
    "m3",
    "kg",
    "l",
    "L",
    "pcs",
    "U",
    "u",
    "nr",
    "no",
    "ft",
    "FT",
    "LF",
    "SF",
    "SY",
    "CY",
    "EA",
)
SERVICE_CANONICALS = frozenset({"LS", "day", "week", "month", "h"})
PLACEHOLDER_PREFIX = "Custom material (Carbon impact in "
PLACEHOLDER_ROWS = 8


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in list(os.environ):
        if key.startswith("ORIS_") or key in VENDOR_KEYS:
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_settings_defaults(clean_env: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key is None
    assert settings.openai_api_key is None
    assert settings.api_token is None
    assert settings.config_dir == Path("config")
    assert settings.libraries == {
        "global": Path("data/oris_materials_global.csv"),
        "fr": Path("data/oris_materials_fr.csv"),
    }
    assert settings.primary_model is None
    assert settings.fallback_model is None
    assert settings.concurrency == DEFAULT_CONCURRENCY == 4
    assert settings.batch_size == DEFAULT_BATCH_SIZE == 10
    assert settings.passes_k == DEFAULT_PASSES_K == 2
    assert settings.per_call_timeout_s == DEFAULT_PER_CALL_TIMEOUT_S == 30
    assert settings.max_retries == DEFAULT_MAX_RETRIES == 2
    assert settings.line_budget_calls == DEFAULT_LINE_BUDGET_CALLS == 6
    assert settings.line_budget_seconds == DEFAULT_LINE_BUDGET_SECONDS == 90
    assert settings.budget_usd_per_100_lines == pytest.approx(1.80)
    assert pytest.approx(1.80) == DEFAULT_BUDGET_USD_PER_100_LINES
    assert settings.breaker_consecutive_failures == DEFAULT_BREAKER_CONSECUTIVE_FAILURES == 10


def test_resolve_models_uses_pinned_by_default(clean_env: pytest.MonkeyPatch) -> None:
    config = load_models_config(CONFIG_DIR / MODELS_FILE)
    resolved = resolve_models(Settings(_env_file=None), config)
    assert resolved == config.pinned
    assert resolved.primary == "claude-haiku-4-5-20251001"
    assert resolved.fallback == "gpt-4o-mini-2024-07-18"


def test_resolve_models_applies_allowed_override(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ORIS_PRIMARY_MODEL", "claude-haiku-4-5-20260101")
    config = load_models_config(CONFIG_DIR / MODELS_FILE)
    resolved = resolve_models(Settings(_env_file=None), config)
    assert resolved.primary == "claude-haiku-4-5-20260101"
    assert resolved.fallback == config.pinned.fallback


@pytest.mark.parametrize("variable", ["ORIS_PRIMARY_MODEL", "ORIS_FALLBACK_MODEL"])
def test_resolve_models_refuses_override_off_allowlist(
    clean_env: pytest.MonkeyPatch, variable: str
) -> None:
    clean_env.setenv(variable, "claude-opus-4")
    settings = Settings(_env_file=None)
    with pytest.raises(ConfigError):
        resolve_models(settings, load_models_config(CONFIG_DIR / MODELS_FILE))


def test_settings_reads_unprefixed_vendor_keys(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    clean_env.setenv("ORIS_API_TOKEN", "tok")
    clean_env.setenv("ORIS_CONCURRENCY", "2")
    settings = Settings(_env_file=None)
    assert isinstance(settings.anthropic_api_key, SecretStr)
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test"
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test"
    assert settings.api_token is not None
    assert settings.api_token.get_secret_value() == "tok"
    assert settings.concurrency == 2
    assert "sk-ant-test" not in repr(settings)


def test_settings_ignores_prefixed_vendor_key(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ORIS_ANTHROPIC_API_KEY", "wrong")
    assert Settings(_env_file=None).anthropic_api_key is None


def test_settings_reads_env_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\nORIS_BATCH_SIZE=5\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "from-file"
    assert settings.batch_size == 5


def test_settings_config_file_path(clean_env: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, config_dir=Path("elsewhere"))
    assert settings.config_file(POLICY_FILE) == Path("elsewhere") / "policy.yaml"


def test_load_models_config() -> None:
    config = load_models_config(CONFIG_DIR / MODELS_FILE)
    assert config.pinned.primary == "claude-haiku-4-5-20251001"
    assert config.pinned.fallback == "gpt-4o-mini-2024-07-18"
    assert set(config.allowlist.patterns) == {
        "claude-haiku-*",
        "gpt-4o-mini*",
        "gemini-*-flash*",
        "local:<=8B",
    }


def test_load_pricing() -> None:
    pricing = load_pricing(CONFIG_DIR / PRICING_FILE)
    assert pricing.price_date == date(2026, 10, 5)
    haiku = pricing.lookup("anthropic", "claude-haiku-4-5-20251001")
    assert (haiku.input, haiku.output, haiku.cache_write, haiku.cache_read) == (
        1.00,
        5.00,
        1.25,
        0.10,
    )
    mini = pricing.lookup("openai", "gpt-4o-mini-2024-07-18")
    assert (mini.input, mini.output, mini.cache_write, mini.cache_read) == (
        0.15,
        0.60,
        0.15,
        0.075,
    )


def test_pricing_lookup_unknown_model_fails() -> None:
    pricing = load_pricing(CONFIG_DIR / PRICING_FILE)
    with pytest.raises(ConfigError):
        pricing.lookup("anthropic", "claude-opus-4")


def test_shipped_policy_has_the_dev_selection_entry() -> None:
    """config/policy.yaml holds the G2 dev selection for Haiku on the global library only."""
    policy = load_policy(CONFIG_DIR / POLICY_FILE)
    library = CONFIG_DIR.parent / "data" / "oris_materials_global.csv"
    sha = hashlib.sha256(library.read_bytes()).hexdigest()
    selection = json.loads(
        (CONFIG_DIR.parent / "eval" / "selection_v1.json").read_text(encoding="utf-8")
    )
    entry = policy.lookup("claude-haiku-4-5-20251001", sha)
    assert entry is not None
    assert entry.policy_id == selection["selected"]["threshold_id"]
    assert entry.certified_by == "dev_selection"
    assert policy.lookup("claude-haiku-4-5-20251001", "0" * 64) is None


def test_policy_entry_lookup(tmp_path: Path) -> None:
    sha = "a" * 64
    path = tmp_path / "policy.yaml"
    path.write_text(
        f"policies:\n  m1:\n    '{sha}':\n      policy_id: T3\n      certified_by: smoke_A10\n",
        encoding="utf-8",
    )
    entry = load_policy(path).lookup("m1", sha)
    assert entry is not None
    assert entry.policy_id == "T3"
    assert entry.certified_by == "smoke_A10"


def test_policy_rejects_bad_sha_and_certifier(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "policies:\n  m1:\n    abc:\n      policy_id: T3\n      certified_by: smoke_A10\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_policy(path)
    path.write_text(
        f"policies:\n  m1:\n    '{'a' * 64}':\n      policy_id: T3\n      certified_by: me\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_policy(path)


def _library_usages() -> list[str]:
    usages: list[str] = []
    for path in LIBRARY_FILES:
        with path.open(encoding="utf-8", newline="") as handle:
            usages.extend(row["material_usage"] for row in csv.DictReader(handle))
    return usages


def test_never_match_hits_exactly_the_placeholder_rows() -> None:
    pattern = load_never_match(CONFIG_DIR / NEVER_MATCH_FILE).patterns[0].compiled
    usages = _library_usages()
    hits = [usage for usage in usages if pattern.search(usage)]
    assert len(hits) == PLACEHOLDER_ROWS
    assert all(usage.startswith(PLACEHOLDER_PREFIX) for usage in hits)
    family = [usage for usage in usages if usage.startswith(PLACEHOLDER_PREFIX)]
    assert sorted(family) == sorted(hits)


def test_load_never_match_matches_placeholders_only() -> None:
    config = load_never_match(CONFIG_DIR / NEVER_MATCH_FILE)
    assert config.patterns
    pattern = config.patterns[0]
    assert pattern.field == "material_usage"
    assert pattern.compiled.search("Custom material (Carbon impact in tonne)")
    assert pattern.compiled.search("Custom material (Carbon impact in m3)")
    assert not pattern.compiled.search("for use in concrete mixtures")


def test_never_match_invalid_regex_fails(tmp_path: Path) -> None:
    path = tmp_path / "never_match.yaml"
    path.write_text("patterns:\n  - field: material_usage\n    regex: '(['\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_never_match(path)


def test_load_service_units() -> None:
    units = load_service_units(CONFIG_DIR / SERVICE_UNITS_FILE).units
    for unit in ("LS", "Ft", "month", "mois", "day", "j", "h", "Forfait", "semaine"):
        assert unit in units
    for measured in ("t", "m", "m²", "m³", "kg", "pcs", "U", "nr", "ft", "FT"):
        assert measured not in units


def test_service_units_never_overlap_measured_units() -> None:
    units = load_service_units(CONFIG_DIR / SERVICE_UNITS_FILE).units
    aliases = load_unit_aliases(CONFIG_DIR / UNIT_ALIASES_FILE).aliases
    measured_aliases = {
        alias
        for canonical, names in aliases.items()
        if canonical not in SERVICE_CANONICALS
        for alias in (canonical, *names)
    }
    assert units.isdisjoint(MEASURED_UNITS)
    assert units.isdisjoint(measured_aliases)


def test_service_units_agree_with_unit_aliases() -> None:
    units = load_service_units(CONFIG_DIR / SERVICE_UNITS_FILE).units
    config = load_unit_aliases(CONFIG_DIR / UNIT_ALIASES_FILE)
    assert set(config.aliases) >= SERVICE_CANONICALS
    for unit in units:
        assert config.canonical_of(unit) in SERVICE_CANONICALS, unit
    for canonical in SERVICE_CANONICALS:
        for alias in config.aliases[canonical]:
            assert alias in units, alias


def test_load_unit_aliases() -> None:
    config = load_unit_aliases(CONFIG_DIR / UNIT_ALIASES_FILE)
    assert "m2" in config.aliases["m²"]
    assert "m3" in config.aliases["m³"]
    assert "U" in config.aliases["pcs"]
    assert "Ft" in config.aliases["LS"]
    assert "mois" in config.aliases["month"]
    assert "j" in config.aliases["day"]
    assert "no" in config.aliases["pcs"]


@pytest.mark.parametrize(
    ("unit", "canonical"),
    [
        ("m2", "m²"),
        ("m²", "m²"),
        ("m3", "m³"),
        ("Ft", "LS"),
        ("ft", "ft"),
        ("FT", "FT"),
        ("U", "pcs"),
        ("u", "pcs"),
        ("T", "t"),
        ("mois", "month"),
        ("Day", "day"),
        ("L.S.", "LS"),
        ("zz", "zz"),
        ("", ""),
    ],
)
def test_canonical_of(unit: str, canonical: str) -> None:
    config = load_unit_aliases(CONFIG_DIR / UNIT_ALIASES_FILE)
    assert config.canonical_of(unit) == canonical


def test_unit_aliases_reject_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "unit_aliases.yaml"
    path.write_text("aliases:\n  m: [ml]\n  t: [ml]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_unit_aliases(path)
    path.write_text("aliases:\n  m: [t]\n  t: [T]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_unit_aliases(path)


def test_load_supply_markers() -> None:
    markers = load_supply_markers(CONFIG_DIR / SUPPLY_MARKERS_FILE).markers
    for marker in ("supply", "provide", "furnish", "fourniture", "fournir", "y compris fourniture"):
        assert marker in markers


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("service_units.yaml", "units: [LS, \n"),
        ("service_units.yaml", "units: [LS]\nextra_key: 1\n"),
        ("service_units.yaml", "units: [LS, true]\n"),
        ("service_units.yaml", "- LS\n"),
        ("supply_markers.yaml", "markers: [supply]\nlang: en\n"),
        ("policy.yaml", "policies: {}\nunexpected: 1\n"),
        ("never_match.yaml", "patterns:\n  - field: colour\n    regex: x\n"),
        ("unit_aliases.yaml", "aliases: {m: [ml]}\nother: {}\n"),
    ],
)
def test_invalid_yaml_fails_loudly(tmp_path: Path, filename: str, content: str) -> None:
    loaders = {
        "service_units.yaml": load_service_units,
        "supply_markers.yaml": load_supply_markers,
        "policy.yaml": load_policy,
        "never_match.yaml": load_never_match,
        "unit_aliases.yaml": load_unit_aliases,
    }
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        loaders[filename](path)


@pytest.mark.parametrize(
    "content",
    [
        "[allowlist\n",
        '[allowlist]\npatterns = ["claude-haiku-*"]\n[pinned]\nprimary = "claude-haiku-4-5"\n'
        'fallback = "gpt-4o-mini"\nextra = 1\n',
        '[allowlist]\npatterns = ["claude-haiku-*"]\n[pinned]\nprimary = "claude-opus-4"\n'
        'fallback = "claude-haiku-4-5"\n',
    ],
)
def test_invalid_models_toml_fails(tmp_path: Path, content: str) -> None:
    path = tmp_path / "models.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_models_config(path)


def test_invalid_pricing_fails(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text(
        'price_date = "2026-10-05"\ncurrency = "USD"\nper_tokens = 1000000\n'
        '[anthropic."m"]\ninput = 1.0\noutput = 5.0\ncache_write = 1.25\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_pricing(path)
    path.write_text(
        'price_date = "2026-10-05"\ncurrency = "USD"\nper_tokens = 1000000\n'
        '[anthropic."m"]\ninput = -1.0\noutput = 5.0\ncache_write = 1.25\ncache_read = 0.1\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_pricing(path)


def test_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_service_units(tmp_path / "absent.yaml")


@pytest.fixture
def allowlist() -> tuple[str, ...]:
    return load_models_config(CONFIG_DIR / MODELS_FILE).allowlist.patterns


@pytest.mark.parametrize(
    "model_id",
    [
        "claude-haiku-4-5-20251001",
        "gpt-4o-mini-2024-07-18",
        "gpt-4o-mini",
        "gemini-2.5-flash",
        "gemini-2.0-flash-lite",
        "local:llama3.1:8b",
        "local:qwen2.5:7B",
        "local:phi3:3.8b",
    ],
)
def test_allowlist_accepts(allowlist: tuple[str, ...], model_id: str) -> None:
    assert is_model_allowed(model_id, allowlist)


@pytest.mark.parametrize(
    "model_id",
    [
        "claude-opus-4",
        "claude-sonnet-4-5",
        "gpt-4o",
        "gpt-4o-2024-08-06",
        "gemini-2.5-pro",
        "local:llama3.1:70b",
        "local:mixtral:8.5b",
        "local:llama3.1",
        "local:llama3.1:70b:8b",
        "",
        "CLAUDE-HAIKU-4-5",
    ],
)
def test_allowlist_rejects(allowlist: tuple[str, ...], model_id: str) -> None:
    assert not is_model_allowed(model_id, allowlist)
