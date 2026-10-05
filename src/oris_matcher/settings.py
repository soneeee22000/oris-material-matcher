"""Runtime settings and the typed loaders for every file in ``config/``.

Settings come from the environment and ``.env`` (prefix ``ORIS_``; the vendor API keys use
their unprefixed standard names). Each config file is validated by a closed Pydantic model,
and any invalid file raises ``ConfigError`` at load time.
"""

import fnmatch
import re
import tomllib
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

MODELS_FILE = "models.toml"
PRICING_FILE = "pricing.toml"
POLICY_FILE = "policy.yaml"
NEVER_MATCH_FILE = "never_match.yaml"
SERVICE_UNITS_FILE = "service_units.yaml"
UNIT_ALIASES_FILE = "unit_aliases.yaml"
SUPPLY_MARKERS_FILE = "supply_markers.yaml"

CONFIG_ENCODING = "utf-8"
DEFAULT_CONCURRENCY = 4
DEFAULT_BATCH_SIZE = 10
DEFAULT_PASSES_K = 2
DEFAULT_PER_CALL_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_LINE_BUDGET_CALLS = 6
DEFAULT_LINE_BUDGET_SECONDS = 90.0
DEFAULT_BUDGET_USD_PER_100_LINES = 1.80
DEFAULT_BREAKER_CONSECUTIVE_FAILURES = 10
DEFAULT_LIBRARIES = {
    "global": Path("data/oris_materials_global.csv"),
    "fr": Path("data/oris_materials_fr.csv"),
}

LOCAL_PATTERN_RE = re.compile(r"local:<=(?P<limit>\d+(?:\.\d+)?)B")
LOCAL_MODEL_RE = re.compile(r"local:[^:\s]+:(?P<size>\d+(?:\.\d+)?)[bB]")
SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"
PRICING_META_KEYS = frozenset({"price_date", "currency", "per_tokens", "providers"})

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
LibrarySha256 = Annotated[str, StringConstraints(pattern=SHA256_HEX_PATTERN)]
NonNegativePrice = Annotated[float, Field(ge=0)]
LibraryField = Literal["material_type", "material_usage", "material_subtype"]
Certifier = Literal["dev_selection", "smoke_A10"]


class ConfigError(Exception):
    """A config file is missing, unparsable, or fails validation."""


class Settings(BaseSettings):
    """Process-wide runtime settings (DESIGN.md §11.3, §11.10).

    ``primary_model`` and ``fallback_model`` are optional overrides; the pinned models live only
    in ``models.toml``, and ``resolve_models`` checks any override against its allowlist.
    """

    model_config = SettingsConfigDict(
        env_prefix="ORIS_",
        env_file=".env",
        env_file_encoding=CONFIG_ENCODING,
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY")
    )
    api_token: SecretStr | None = None
    config_dir: Path = Path("config")
    libraries: dict[str, Path] = Field(default_factory=lambda: dict(DEFAULT_LIBRARIES))
    primary_model: NonEmptyStr | None = None
    fallback_model: NonEmptyStr | None = None
    concurrency: int = Field(default=DEFAULT_CONCURRENCY, ge=1)
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1)
    passes_k: int = Field(default=DEFAULT_PASSES_K, ge=1)
    per_call_timeout_s: float = Field(default=DEFAULT_PER_CALL_TIMEOUT_S, gt=0)
    max_retries: int = Field(default=DEFAULT_MAX_RETRIES, ge=0)
    line_budget_calls: int = Field(default=DEFAULT_LINE_BUDGET_CALLS, ge=1)
    line_budget_seconds: float = Field(default=DEFAULT_LINE_BUDGET_SECONDS, gt=0)
    budget_usd_per_100_lines: float = Field(default=DEFAULT_BUDGET_USD_PER_100_LINES, gt=0)
    breaker_consecutive_failures: int = Field(default=DEFAULT_BREAKER_CONSECUTIVE_FAILURES, ge=1)

    def config_file(self, name: str) -> Path:
        """Return the path of one config file inside ``config_dir``.

        Args:
            name: File name, e.g. ``POLICY_FILE``.

        Returns:
            ``config_dir / name``.

        """
        return self.config_dir / name


class _Config(BaseModel):
    """Base for config-file models: closed, and frozen against attribute assignment.

    Frozen means attribute assignment only. Models with ``dict`` fields (``PricingTable``,
    ``PolicyConfig``, ``UnitAliasesConfig``) are not hashable and their dicts are not deep-frozen;
    callers treat them as read-only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class Allowlist(_Config):
    """Model allowlist patterns from ``models.toml``."""

    patterns: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("patterns")
    @classmethod
    def _local_patterns_parse(cls, patterns: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a ``local:`` pattern that is not of the form ``local:<=NB``."""
        for pattern in patterns:
            if pattern.startswith("local:") and LOCAL_PATTERN_RE.fullmatch(pattern) is None:
                raise ValueError(f"local pattern must look like 'local:<=8B', got {pattern!r}")
        return patterns


class PinnedModels(_Config):
    """The pinned primary and fallback model snapshots."""

    primary: NonEmptyStr
    fallback: NonEmptyStr


class ModelsConfig(_Config):
    """Contents of ``models.toml``."""

    allowlist: Allowlist
    pinned: PinnedModels

    @model_validator(mode="after")
    def _pinned_models_allowed(self) -> Self:
        """Refuse a pinned model that the allowlist does not admit."""
        for model_id in (self.pinned.primary, self.pinned.fallback):
            if not is_model_allowed(model_id, self.allowlist.patterns):
                raise ValueError(f"pinned model {model_id!r} is not on the allowlist")
        return self


class ModelPrice(_Config):
    """USD prices per ``per_tokens`` tokens for one model."""

    input: NonNegativePrice
    output: NonNegativePrice
    cache_write: NonNegativePrice
    cache_read: NonNegativePrice


class PricingTable(_Config):
    """Contents of ``pricing.toml``: dated prices keyed by provider, then model."""

    price_date: date
    currency: Literal["USD"]
    per_tokens: int = Field(gt=0)
    providers: dict[NonEmptyStr, dict[NonEmptyStr, ModelPrice]] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _collect_providers(cls, data: object) -> object:
        """Gather the top-level provider tables of the TOML file under ``providers``."""
        if not isinstance(data, dict) or "providers" in data:
            return data
        meta = {key: value for key, value in data.items() if key in PRICING_META_KEYS}
        tables = {key: value for key, value in data.items() if key not in PRICING_META_KEYS}
        return {**meta, "providers": tables}

    def lookup(self, provider: str, model: str) -> ModelPrice:
        """Return the price of one model.

        Args:
            provider: Provider key, e.g. ``anthropic``.
            model: Exact model snapshot id.

        Returns:
            The model's prices.

        Raises:
            ConfigError: The pair has no price, so its cost cannot be computed.

        """
        price = self.providers.get(provider, {}).get(model)
        if price is None:
            raise ConfigError(f"no price for {provider}:{model} in {PRICING_FILE}")
        return price


class PolicyEntry(_Config):
    """A certified operating threshold for one (model, library) pair."""

    policy_id: NonEmptyStr
    certified_by: Certifier


class PolicyConfig(_Config):
    """Contents of ``policy.yaml``: model_id -> library_sha256 -> entry (DESIGN.md §10.6)."""

    policies: dict[NonEmptyStr, dict[LibrarySha256, PolicyEntry]]

    def lookup(self, model_id: str, library_sha256: str) -> PolicyEntry | None:
        """Return the exact-hit entry, or None when the pair must fall back to strictest.

        Args:
            model_id: Served model snapshot id.
            library_sha256: SHA-256 of the loaded library file.

        Returns:
            The certified entry, or None.

        """
        return self.policies.get(model_id, {}).get(library_sha256)


class NeverMatchPattern(_Config):
    """One regular expression searched in one library field."""

    field: LibraryField
    regex: NonEmptyStr
    note: str = ""

    @field_validator("regex")
    @classmethod
    def _regex_compiles(cls, regex: str) -> str:
        """Reject a pattern that is not a valid Python regular expression."""
        try:
            re.compile(regex)
        except re.error as error:
            raise ValueError(f"invalid regex {regex!r}: {error}") from error
        return regex

    @property
    def compiled(self) -> re.Pattern[str]:
        """The compiled regular expression."""
        return re.compile(self.regex)


class NeverMatchConfig(_Config):
    """Contents of ``never_match.yaml``."""

    patterns: tuple[NeverMatchPattern, ...]


class ServiceUnitsConfig(_Config):
    """Contents of ``service_units.yaml``: raw units compared exactly."""

    units: frozenset[NonEmptyStr] = Field(min_length=1)


class UnitAliasesConfig(_Config):
    """Contents of ``unit_aliases.yaml``: canonical unit -> aliases, compared exactly."""

    aliases: dict[NonEmptyStr, tuple[NonEmptyStr, ...]]

    @model_validator(mode="after")
    def _aliases_unambiguous(self) -> Self:
        """Refuse an alias listed twice or equal to a canonical unit."""
        seen: set[str] = set()
        for canonical, aliases in self.aliases.items():
            for alias in aliases:
                if alias in seen or (alias in self.aliases and alias != canonical):
                    raise ValueError(f"ambiguous unit alias {alias!r}")
                seen.add(alias)
        return self

    def canonical_of(self, unit: str) -> str:
        """Map a unit to its canonical form.

        Args:
            unit: A unit string, compared exactly.

        Returns:
            The canonical unit, or ``unit`` unchanged when it is not a known alias.

        """
        for canonical, aliases in self.aliases.items():
            if unit in aliases:
                return canonical
        return unit


class SupplyMarkersConfig(_Config):
    """Contents of ``supply_markers.yaml``."""

    markers: tuple[NonEmptyStr, ...] = Field(min_length=1)


def is_model_allowed(model_id: str, allowlist: Iterable[str]) -> bool:
    """Tell whether a model id is admitted by the allowlist (DESIGN.md §11.3, A33).

    Args:
        model_id: Model snapshot id, e.g. ``claude-haiku-4-5-20251001`` or ``local:llama3.1:8b``.
        allowlist: Case-sensitive glob patterns, plus ``local:<=NB`` size limits.

    Returns:
        True when at least one pattern admits the id.

    """
    return any(_pattern_admits(model_id, pattern) for pattern in allowlist)


def _pattern_admits(model_id: str, pattern: str) -> bool:
    """Tell whether one allowlist pattern admits a model id."""
    local_limit = LOCAL_PATTERN_RE.fullmatch(pattern)
    if local_limit is None:
        return fnmatch.fnmatchcase(model_id, pattern)
    local_model = LOCAL_MODEL_RE.fullmatch(model_id)
    return local_model is not None and float(local_model["size"]) <= float(local_limit["limit"])


def resolve_models(settings: Settings, config: ModelsConfig) -> PinnedModels:
    """Return the effective primary and fallback models.

    ``models.toml`` is the single source of the pinned models; a ``Settings`` override replaces
    one only when the allowlist admits it.

    Args:
        settings: Runtime settings, possibly carrying overrides.
        config: The loaded ``models.toml``.

    Returns:
        The effective models.

    Raises:
        ConfigError: An override is not on the allowlist.

    """
    primary = settings.primary_model or config.pinned.primary
    fallback = settings.fallback_model or config.pinned.fallback
    for model_id in (primary, fallback):
        if not is_model_allowed(model_id, config.allowlist.patterns):
            raise ConfigError(f"model {model_id!r} is not on the allowlist in {MODELS_FILE}")
    return PinnedModels(primary=primary, fallback=fallback)


def _read_text(path: Path) -> str:
    """Read a config file as UTF-8, raising ConfigError when it cannot be read."""
    try:
        return path.read_text(encoding=CONFIG_ENCODING)
    except OSError as error:
        raise ConfigError(f"{path}: cannot read config file ({error})") from error


def _parse_yaml(path: Path) -> object:
    """Parse a YAML config file with the safe loader."""
    try:
        return yaml.safe_load(_read_text(path))
    except yaml.YAMLError as error:
        raise ConfigError(f"{path}: invalid YAML ({error})") from error


def _parse_toml(path: Path) -> object:
    """Parse a TOML config file."""
    try:
        return tomllib.loads(_read_text(path))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML ({error})") from error


def _validate[ConfigT: BaseModel](model: type[ConfigT], data: object, path: Path) -> ConfigT:
    """Validate parsed config data against its model, raising ConfigError on failure."""
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise ConfigError(f"{path}: invalid config\n{error}") from error


def load_models_config(path: Path) -> ModelsConfig:
    """Load ``models.toml``.

    Args:
        path: Path to the file.

    Returns:
        The validated allowlist and pinned models.

    Raises:
        ConfigError: The file is missing or invalid.

    """
    return _validate(ModelsConfig, _parse_toml(path), path)


def load_pricing(path: Path) -> PricingTable:
    """Load ``pricing.toml``.

    Args:
        path: Path to the file.

    Returns:
        The validated, dated price table.

    Raises:
        ConfigError: The file is missing or invalid.

    """
    return _validate(PricingTable, _parse_toml(path), path)


def load_policy(path: Path) -> PolicyConfig:
    """Load ``policy.yaml``.

    Args:
        path: Path to the file.

    Returns:
        The validated policy map.

    Raises:
        ConfigError: The file is missing or invalid.

    """
    return _validate(PolicyConfig, _parse_yaml(path), path)


def load_never_match(path: Path) -> NeverMatchConfig:
    """Load ``never_match.yaml``.

    Args:
        path: Path to the file.

    Returns:
        The validated never-match patterns.

    Raises:
        ConfigError: The file is missing or invalid.

    """
    return _validate(NeverMatchConfig, _parse_yaml(path), path)


def load_service_units(path: Path) -> ServiceUnitsConfig:
    """Load ``service_units.yaml``.

    Args:
        path: Path to the file.

    Returns:
        The validated service units.

    Raises:
        ConfigError: The file is missing or invalid.

    """
    return _validate(ServiceUnitsConfig, _parse_yaml(path), path)


def load_unit_aliases(path: Path) -> UnitAliasesConfig:
    """Load ``unit_aliases.yaml``.

    Args:
        path: Path to the file.

    Returns:
        The validated alias map.

    Raises:
        ConfigError: The file is missing or invalid.

    """
    return _validate(UnitAliasesConfig, _parse_yaml(path), path)


def load_supply_markers(path: Path) -> SupplyMarkersConfig:
    """Load ``supply_markers.yaml``.

    Args:
        path: Path to the file.

    Returns:
        The validated supply markers.

    Raises:
        ConfigError: The file is missing or invalid.

    """
    return _validate(SupplyMarkersConfig, _parse_yaml(path), path)
