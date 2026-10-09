"""MatchService: the only orchestrator, shared by the CLI, the API and the UI (DESIGN.md §11.1).

One run goes gate -> plan batches -> k passes through the LLM wrapper -> validate -> decide ->
``RunResult``; reading the file is the caller's job, and so is writing the CSV and the run
folder (``io/writer.py``, ``io/audit.py``). Profile B3 runs the full decision table at the
resolved policy threshold (§10.6); profile B2 is the raw single-pass rung of the baseline
ladder (§10.5); profile B0 is its rules-only floor, which plans no batch and so makes no call
(§7.1, §10.5). Inside a batch the model sees transport ids ``L<position>``, so the wrapper and
every call record carry those ids; the service maps them back to lines. With the E-08 verifier
adopted, a B3 run then asks the sibling verifier about every line the table would match
(``verification.py``) and decides with its answers (A65.2). A B3 run may render an arm C
enrichment (A68): the forced setting's file, else the deciding entry's after its SHA-256 is
checked; the file must belong to the run's library, which is checked before any call. Its hash
enters each pass's prompt version and the manifest's ``enrichment_sha256``. The lines the A33
fallback re-runs render the fallback entry's enrichment instead (as A67 binds the verifier), or
the forced one; that is resolved before any call too, and recorded once the fallback rescued
the run as ``fallback_enrichment_sha256`` and ``fallback_prompt_version``.
"""

import asyncio
import dataclasses
import functools
import hashlib
import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from oris_matcher.candidates.whole_library import WholeLibrary
from oris_matcher.domain.attributes import Attributes, AttrResult, compare, extract
from oris_matcher.domain.batching import Batch, plan_batches, transport_id
from oris_matcher.domain.boq import BoqFile, BoqLine, LineKind, PathMode
from oris_matcher.domain.decision import (
    DEFAULT_PROFILE,
    ConfidenceBucket,
    Decision,
    DecisionInput,
    DecisionProfile,
    LineDecision,
    LLMFailureKind,
    PassOutcome,
    ReasonCode,
    Rule,
    SignalName,
    Signals,
    Threshold,
    candidate_thresholds,
    decide,
    decide_b2,
    flagged_top1,
    is_service_unit,
    llm_failure_reason,
    low_signal_reason,
    make_haystack,
    strictest_threshold,
)
from oris_matcher.domain.library import Library, LibraryRow, load_library
from oris_matcher.domain.normalize import normalize
from oris_matcher.domain.validator import is_valid_code
from oris_matcher.enrichment import (
    LoadedEnrichment,
    is_enrichment_off,
    read_enrichment,
    require_enrichment_sha256,
    require_library,
)
from oris_matcher.io.boq_reader import capped_for_prompt
from oris_matcher.llm.base import HASH_ENCODING, LLMPort, LLMRequest, SystemBlock, canonical_json
from oris_matcher.llm.fake_llm import FakeLLM
from oris_matcher.llm.recording import (
    CallRecord,
    DeclinedAttempt,
    LineAttribution,
    attribute_costs,
    system_blocks_sha256,
)
from oris_matcher.llm.replay_llm import ReplayLLM
from oris_matcher.llm.routing import RoutingLLM
from oris_matcher.llm.wrapper import (
    VERIFIER_ANSWERS,
    BatchOutcome,
    LineOutcome,
    LLMWrapper,
    RequestBuilder,
)
from oris_matcher.prompts.v1.enriched import (
    build_enriched_request,
    render_enriched_system_blocks,
)
from oris_matcher.prompts.v1.render import (
    B2,
    CANONICAL_V1,
    REVERSE_V1,
    TEMPERATURE,
    PromptVariant,
)
from oris_matcher.prompts.v1.verifier import build_verifier_request, render_verifier_system
from oris_matcher.prompts.v1.version import prompt_version, verifier_prompt_version
from oris_matcher.settings import (
    MODELS_FILE,
    NEVER_MATCH_FILE,
    POLICY_FILE,
    SERVICE_UNITS_FILE,
    SUPPLY_MARKERS_FILE,
    ConfigError,
    NeverMatchPattern,
    PolicyConfig,
    PricingTable,
    Settings,
    load_models_config,
    load_never_match,
    load_policy,
    load_service_units,
    load_supply_markers,
    resolve_models,
)
from oris_matcher.verification import (
    BATCHING,
    NOT_FLAGGED,
    VerifierGroup,
    VerifierLine,
    verifier_groups,
    verifier_line,
)

LOGGER = logging.getLogger(__name__)

RULES_MODEL = "rules"
EXIT_OK = 0
EXIT_LLM_UNAVAILABLE = 3
DEFAULT_MAX_TOKENS = 4096
CACHE_MIN_TOKENS = 4096
LINES_PER_BUDGET_UNIT = 100
MIN_RUN_BUDGET_USD = 0.10
V1_PASS_VARIANTS: tuple[PromptVariant, ...] = (CANONICAL_V1, REVERSE_V1)
B2_POLICY_ID = "B2"
B2_THRESHOLD = Threshold(
    B2_POLICY_ID, Signals(0, AttrResult.NO_EVIDENCE, ConfidenceBucket.BELOW_70)
)
B0_POLICY_ID = "B0"
B0_THRESHOLD = Threshold(
    B0_POLICY_ID, Signals(0, AttrResult.NO_EVIDENCE, ConfidenceBucket.BELOW_70)
)
B0_REVIEW_REASON = low_signal_reason(SignalName)
STRUCTURAL_RULES = frozenset({Rule.D0, Rule.D0A, Rule.D0B})
RUN_ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
RUN_ID_HASH_LENGTH = 8
PROMPT_VERSION_SEPARATOR = ";"
SOURCE_RUN_SEPARATOR = ";"
WALL_CLOCK_DIGITS = 3
TRUNCATED_FLAG = "TRUNCATED"
CONTEXT_PATH = "section_path"
CONTEXT_NONE = "none"
TEXT_SEPARATOR = " "
DECISION_PROFILE_KEY = "decision_profile"
DROP_CONFLICTING_VOTES_KEY = "drop_conflicting_votes"
VERIFIER_ADOPTED_KEY = "verifier_adopted"
VERIFIER_KEY = "verifier"
UNAVAILABLE_REASONS = frozenset(
    {ReasonCode.LLM_UNAVAILABLE.value, llm_failure_reason(LLMFailureKind.REPLAY_MISS)}
)
BUCKET_LABELS: Mapping[ConfidenceBucket, str] = MappingProxyType(
    {
        ConfidenceBucket.FROM_90: ">=90",
        ConfidenceBucket.FROM_80: "80-89",
        ConfidenceBucket.FROM_70: "70-79",
        ConfidenceBucket.BELOW_70: "<70",
    }
)

JobKey = tuple[int, int]


class RunProfile(StrEnum):
    """Which rung a run is (§10.5).

    ``b0``: rules only, no model call; ``b2``: the raw single-pass baseline; ``b3``: the full
    system.
    """

    B0 = "b0"
    B2 = "b2"
    B3 = "b3"


class PolicyResolution(StrEnum):
    """How the run's threshold was chosen (§10.6)."""

    EXACT = "exact"
    FALLBACK_STRICTEST = "fallback_strictest"
    NO_PATH_STRICTEST = "no_path_strictest"
    OVERRIDE = "override"
    B2_MATCH_ALL = "b2_match_all"
    B0_RULES_ONLY = "b0_rules_only"


class RunMode(StrEnum):
    """Where the responses came from (§9.6).

    ``live``: every response came from the adapter; ``cached``: at least one came from the
    response cache of an earlier live run; ``replay``: the run copies a recorded run; ``fake``:
    the offline FakeLLM answered, so no number of the run is a measurement; ``rules``: a B0
    run, which sends nothing to any adapter, so its numbers are measurements whatever
    ``--llm`` names.
    """

    LIVE = "live"
    CACHED = "cached"
    REPLAY = "replay"
    FAKE = "fake"
    RULES = "rules"


@dataclass(frozen=True)
class ResolvedPolicy:
    """The threshold a run decides with, and how it was resolved.

    Attributes:
        threshold: The operating point on score s.
        policy_id: The policy's id; the threshold id for a fallback or an override.
        resolution: How the threshold was chosen.
        certified_by: ``dev_selection`` or ``smoke_A10`` for an exact hit of ``config/``, else
            None.
        claimed_certified_by: What a ``--policy`` file claims as certification; never verified.
        verifier_adopted: The entry's E-08 verifier (A67); False for every resolution that is
            not an entry of a policy file.
        enrichment: The entry's arm C enrichment file, repository-relative (A68.4); None for
            every resolution that is not an entry naming one.
        enrichment_sha256: The SHA-256 the entry certifies for that file.

    """

    threshold: Threshold
    policy_id: str
    resolution: PolicyResolution
    certified_by: str | None = None
    claimed_certified_by: str | None = None
    verifier_adopted: bool = False
    enrichment: str | None = None
    enrichment_sha256: str | None = None


def as_override(policy: ResolvedPolicy) -> ResolvedPolicy:
    """Mark an exact hit of a ``--policy`` file as an override whose certification is unverified.

    Args:
        policy: The policy resolved from the override file.

    Returns:
        An ``override`` resolution keeping the policy id, threshold and the entry's verifier
        and enrichment, with the file's ``certified_by`` moved to ``claimed_certified_by``;
        any other resolution unchanged.

    """
    if policy.resolution != PolicyResolution.EXACT:
        return policy
    return dataclasses.replace(
        policy,
        resolution=PolicyResolution.OVERRIDE,
        certified_by=None,
        claimed_certified_by=policy.certified_by,
    )


def _threshold_named(threshold_id: str, passes: int) -> Threshold:
    """Return the candidate threshold with this id, refusing an unknown id."""
    for threshold in candidate_thresholds(passes):
        if threshold.threshold_id == threshold_id:
            return threshold
    raise ValueError(f"policy names threshold {threshold_id!r}, not a candidate at k={passes}")


def _strictest(
    passes: int, resolution: PolicyResolution, why: str, *, warn: bool = True
) -> ResolvedPolicy:
    """Return the strictest threshold, logging a WARNING that names the resolution."""
    threshold = strictest_threshold(passes)
    if warn:
        LOGGER.warning(
            "policy_resolution=%s: %s; deciding at the strictest threshold %s",
            resolution.value,
            why,
            threshold.threshold_id,
        )
    return ResolvedPolicy(threshold, threshold.threshold_id, resolution)


@dataclass(frozen=True)
class PolicyQuery:
    """What policy resolution is keyed on, and what can override it.

    Attributes:
        model_id: The requested model snapshot id.
        library_sha256: SHA-256 of the loaded library file.
        passes: k, the passes per line.
        path_derived: Whether the input has section paths; without them the strictest
            threshold runs until the E-04 no-path ablation certifies one (§9.1).
        override: A threshold forced by the caller (``--policy``), recorded as an override.

    """

    model_id: str
    library_sha256: str
    passes: int
    path_derived: bool = True
    override: Threshold | None = None


def resolve_policy(
    config: PolicyConfig, query: PolicyQuery, *, warn: bool = True
) -> ResolvedPolicy:
    """Resolve the B3 threshold from ``policy.yaml`` (DESIGN.md §10.6, A33, A38).

    Args:
        config: The loaded policy map.
        query: The (model, library) key, the pass count, path mode and any override.
        warn: Log the WARNING of a strictest resolution; off when a run only looks ahead.

    Returns:
        The override, else the strictest threshold for a file without paths, else the exact
        hit, else the strictest threshold as ``fallback_strictest``.

    Raises:
        ValueError: The exact hit names a threshold that is not a candidate.

    """
    override, passes = query.override, query.passes
    if override is not None:
        return ResolvedPolicy(override, override.threshold_id, PolicyResolution.OVERRIDE)
    if not query.path_derived:
        why = "no section path derives"
        return _strictest(passes, PolicyResolution.NO_PATH_STRICTEST, why, warn=warn)
    entry = config.lookup(query.model_id, query.library_sha256)
    if entry is None:
        why = f"no certified policy for ({query.model_id}, {query.library_sha256[:12]})"
        return _strictest(passes, PolicyResolution.FALLBACK_STRICTEST, why, warn=warn)
    threshold = _threshold_named(entry.policy_id, passes)
    return ResolvedPolicy(
        threshold,
        entry.policy_id,
        PolicyResolution.EXACT,
        entry.certified_by,
        verifier_adopted=entry.verifier_adopted,
        enrichment=entry.enrichment,
        enrichment_sha256=entry.enrichment_sha256,
    )


def _under(root: Path, path: Path) -> Path:
    """Resolve a relative path against the repository root; an absolute one is kept."""
    return path if path.is_absolute() else root / path


def _forced_enrichment(forced: Path, root: Path) -> LoadedEnrichment | None:
    """Load a forced enrichment file; ``none`` loads nothing."""
    if is_enrichment_off(forced):
        return None
    return read_enrichment(_under(root, forced))


def _entry_enrichment(policy: ResolvedPolicy, root: Path) -> LoadedEnrichment | None:
    """Load the deciding entry's enrichment file after checking its certified SHA-256."""
    if policy.enrichment is None:
        return None
    loaded = read_enrichment(_under(root, Path(policy.enrichment)))
    require_enrichment_sha256(loaded, policy.enrichment_sha256)
    return loaded


def resolve_enrichment(
    forced: Path | None, policy: ResolvedPolicy, library: Library, root: Path
) -> LoadedEnrichment | None:
    """Choose and check the arm C enrichment a B3 run renders (A68.4).

    Args:
        forced: The setting: a file, ``none``, or None to follow the policy entry.
        policy: The run's resolved policy; an entry may name a file and its SHA-256.
        library: The run's library; the file must have been generated from it.
        root: The repository root a relative path resolves against.

    Returns:
        The forced file, else the entry's file, else None; ``none`` forces None.

    Raises:
        ConfigError: The file cannot be read or is invalid, its SHA-256 is not the one the
            entry certifies, or it was generated from another library.

    """
    if forced is not None:
        loaded = _forced_enrichment(forced, root)
    else:
        loaded = _entry_enrichment(policy, root)
    if loaded is not None:
        require_library(loaded, library)
    return loaded


@functools.cache
def _marker_pattern(marker: str) -> re.Pattern[str]:
    """Compile one normalised supply marker as a whole word or phrase."""
    return re.compile(rf"(?<!\w){re.escape(marker)}(?!\w)")


def has_supply_marker(text: str, markers: Iterable[str]) -> bool:
    """Tell whether a line's text holds a supply marker (gate G2, D-04).

    Args:
        text: The line's raw short and long descriptions.
        markers: Markers from ``supply_markers.yaml``.

    Returns:
        True when a normalised marker occurs in the normalised text as a whole word or phrase.

    """
    haystack = normalize(text)
    return any(_marker_pattern(normalize(marker)).search(haystack) for marker in markers)


def make_run_id(moment: datetime, seed: str) -> str:
    """Derive a run id from a UTC moment and a short hash; replays pass their own id instead.

    Args:
        moment: A timezone-aware moment.
        seed: Text that distinguishes runs started in the same second.

    Returns:
        E.g. ``20261005T100000Z-1a2b3c4d``.

    """
    stamp = moment.astimezone(UTC).strftime(RUN_ID_TIME_FORMAT)
    digest = hashlib.sha256(seed.encode(HASH_ENCODING)).hexdigest()[:RUN_ID_HASH_LENGTH]
    return f"{stamp}-{digest}"


def budget_cap_usd(settings: Settings, line_count: int) -> float:
    """Return a run's spend cap: ``budget_usd_per_100_lines`` scaled to its lines (§11.3, A57).

    The scaled cap has a per-run floor of ``MIN_RUN_BUDGET_USD``, so a one-line run can still
    dispatch a call whose cached prefix is cold.

    Args:
        settings: The effective settings.
        line_count: Lines in the run.

    Returns:
        The cap in USD: max(per-100-lines cap scaled to the lines, ``MIN_RUN_BUDGET_USD``).

    """
    scaled = settings.budget_usd_per_100_lines * line_count / LINES_PER_BUDGET_UNIT
    return max(scaled, MIN_RUN_BUDGET_USD)


def provider_for(model: str, pricing: PricingTable) -> str:
    """Return the provider whose price table lists a model.

    Args:
        model: Model snapshot id.
        pricing: The dated price table.

    Returns:
        The provider key, e.g. ``anthropic``.

    Raises:
        ConfigError: No provider prices the model.

    """
    for provider, models in sorted(pricing.providers.items()):
        if model in models:
            return provider
    raise ConfigError(f"no provider prices model {model!r}")


def load_catalogue(
    library_id: str,
    settings: Settings,
    never_match: Sequence[NeverMatchPattern] | None = None,
) -> Library:
    """Load a configured library with the real extractor and its A2 sibling post-pass.

    Args:
        library_id: An id from ``Settings.libraries``, e.g. ``global`` or ``fr``; it is the
            catalogue id, so the implicit-zero rule runs only where it was checked.
        settings: The effective settings.
        never_match: Never-match patterns; read from ``config/`` when None.

    Returns:
        The library.

    Raises:
        KeyError: The id is not configured.

    """
    path = settings.libraries.get(library_id)
    if path is None:
        raise KeyError(
            f"unknown library id {library_id!r}; configured: {sorted(settings.libraries)}"
        )
    patterns = never_match
    if patterns is None:
        patterns = load_never_match(settings.config_file(NEVER_MATCH_FILE)).patterns
    return load_library(path.read_bytes(), patterns, catalogue=library_id)


@dataclass(frozen=True)
class RunOptions:
    """Optional inputs of one run.

    Attributes:
        threshold: A forced B3 threshold, recorded as an override.
        select: Line ids to route and output; lines outside it are neither, so the caller
            passes the headers it wants decided too. None means every line.
        run_id: The run id; derived from the clock and the inputs when None.
        source_run_id: The run a replay copies.
        cache_sources: Call id -> run id of every cached response, so a cached run names the
            runs its hits came from.
        fallback: The fallback adapter that takes over the pending lines once the breaker
            trips (§11.3, A33); None when no fallback is configured.

    """

    threshold: Threshold | None = None
    select: Collection[str] | None = None
    run_id: str | None = None
    source_run_id: str | None = None
    cache_sources: Mapping[str, str] = dataclasses.field(default_factory=dict)
    fallback: LLMPort | None = None


DEFAULT_OPTIONS = RunOptions()


@dataclass(frozen=True)
class ServiceResources:
    """Everything a run reads besides the BoQ, the library and the LLM wrapper.

    Attributes:
        settings: The effective settings.
        service_units: Units from ``service_units.yaml`` (G2).
        supply_markers: Markers from ``supply_markers.yaml`` (G2).
        policy: The policy map from ``policy.yaml``.
        never_match: Never-match patterns from ``never_match.yaml``.
        requested_model: The primary model every request names.
        fallback_model: The pinned fallback, recorded in the manifest.
        max_tokens: Output token cap per request.
        policy_override: Whether ``policy`` came from a ``--policy`` file rather than
            ``config/policy.yaml``; its exact hits are then ``override`` resolutions (§10.6).
        root: The repository root a relative enrichment path resolves against (A68.4); the
            working directory by default, as for the configured libraries.
        fallback_enrichment: Forces the enrichment the A33 fallback's lines render, as a
            replay does with the one its recorded fallback rendered; None follows the setting,
            then the fallback's policy entry (A68.4).

    """

    settings: Settings
    service_units: frozenset[str]
    supply_markers: tuple[str, ...]
    policy: PolicyConfig
    never_match: tuple[NeverMatchPattern, ...]
    requested_model: str
    fallback_model: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    policy_override: bool = False
    root: Path = Path()
    fallback_enrichment: Path | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "ServiceResources":
        """Load every config file the service reads.

        Args:
            settings: The effective settings.

        Returns:
            The resources.

        Raises:
            ConfigError: A config file is missing or invalid, or a model is not allowed.

        """
        models_config = load_models_config(settings.config_file(MODELS_FILE))
        models = resolve_models(settings, models_config)
        return cls(
            settings=settings,
            service_units=load_service_units(settings.config_file(SERVICE_UNITS_FILE)).units,
            supply_markers=load_supply_markers(settings.config_file(SUPPLY_MARKERS_FILE)).markers,
            policy=load_policy(settings.config_file(POLICY_FILE)),
            never_match=load_never_match(settings.config_file(NEVER_MATCH_FILE)).patterns,
            requested_model=models.primary,
            fallback_model=models.fallback,
        )

    def decision_profile(self, policy: ResolvedPolicy) -> DecisionProfile:
        """Return the decision table's flags for a run deciding under this policy (A64, A67).

        Args:
            policy: The run's resolved policy.

        Returns:
            ``drop_conflicting_votes`` from the settings; ``verifier_adopted`` from the
            settings when they set it, else from the policy entry.

        """
        settings = self.settings
        verifier = settings.verifier_adopted
        return DecisionProfile(
            verifier_adopted=policy.verifier_adopted if verifier is None else verifier,
            drop_conflicting_votes=settings.drop_conflicting_votes,
        )


def decision_profile_fields(profile: DecisionProfile) -> dict[str, Any]:
    """Return the manifest's ``decision_profile`` field.

    ``verifier_adopted`` is written only when it is on, so the manifest of every run decided
    without the verifier keeps the bytes it had before E-08, and reads back as off like a run
    recorded before it.

    Args:
        profile: The profile a run decided with.

    Returns:
        ``{"decision_profile": {"drop_conflicting_votes": bool}}``, with
        ``"verifier_adopted": true`` added when the verifier decided the run.

    """
    fields: dict[str, bool] = {DROP_CONFLICTING_VOTES_KEY: profile.drop_conflicting_votes}
    if profile.verifier_adopted:
        fields[VERIFIER_ADOPTED_KEY] = True
    return {DECISION_PROFILE_KEY: fields}


def recorded_decision_profile(manifest: Mapping[str, Any]) -> DecisionProfile:
    """Read back the profile a recorded run decided with, so a replay re-decides as it did.

    Args:
        manifest: A run manifest.

    Returns:
        The recorded profile; a flag a run did not record (before A64, before E-08, or off)
        reads back as off.

    """
    recorded = manifest.get(DECISION_PROFILE_KEY) or {}
    return DecisionProfile(
        verifier_adopted=bool(recorded.get(VERIFIER_ADOPTED_KEY)),
        drop_conflicting_votes=bool(recorded.get(DROP_CONFLICTING_VOTES_KEY)),
    )


@dataclass(frozen=True)
class LineResult:
    """One output line: its decision and everything the writer and the audit record need.

    Attributes:
        line: The input line, raw.
        decision: The decision table's verdict.
        model: Served model, ``rules`` for a rule-decided line, else the requested model.
        prompt_version: The run's prompt versions, one per pass, ``;``-joined.
        cost_usd: Attributed cost, sum over attempts a that touched the line of cost(a) / n_a.
        latency_ms: Attributed latency, round(sum of elapsed(a) / n_a).
        call_ids: Every attempt that touched the line, in record order.
        suggested: The library row of a valid top-1, else None.
        suggested2: The library row of a valid top-2, else None.
        attribute_result: Signal (b) of the suggested row against the line, B3 only.
        raw_line_responses: The line's verbatim answer object from each pass that gave one.
        context: ``section_path`` when the prompt carried one, else ``none``.
        flags: Audit flags such as ``TRUNCATED``.
        verifier: What E-08 recorded for the line; None when the run did not adopt it.

    """

    line: BoqLine
    decision: LineDecision
    model: str
    prompt_version: str
    cost_usd: float
    latency_ms: int
    call_ids: tuple[str, ...]
    suggested: LibraryRow | None
    suggested2: LibraryRow | None
    attribute_result: AttrResult | None
    raw_line_responses: tuple[str, ...]
    context: str
    flags: tuple[str, ...]
    verifier: VerifierLine | None = None


@dataclass(frozen=True)
class RunResult:
    """Everything one run produced, in input order.

    Attributes:
        run_id: The run id.
        profile: B2 or B3.
        library_id: The configured library id.
        library: The loaded library.
        input_header: The input's column names, in order.
        lines: One result per output line, in input order.
        calls: Every attempt, in (pass, batch) order, then attempt order.
        audit: One ``audit.jsonl`` record per output line.
        manifest: The run's manifest fields known to the service (§9.6).
        policy: The resolved policy.
        prompt_versions: One prompt version per pass.
        system_prompts: ``system_blocks_sha256`` -> the canonical JSON text it hashes.
        exit_code: 3 when a line is ``LLM_UNAVAILABLE`` or a replay miss, else 0.
        attributed_cost_usd: The sum of the lines' attributed costs.
        enrichment_path: The arm C enrichment file the run rendered, or None (A68.4).
        fallback_enrichment_path: The enrichment file the A33 fallback's lines rendered once
            it rescued the run, or None (A68.4).

    """

    run_id: str
    profile: RunProfile
    library_id: str
    library: Library
    input_header: tuple[str, ...]
    lines: tuple[LineResult, ...]
    calls: tuple[CallRecord, ...]
    audit: tuple[dict[str, Any], ...]
    manifest: Mapping[str, Any]
    policy: ResolvedPolicy
    prompt_versions: tuple[str, ...]
    system_prompts: Mapping[str, str]
    exit_code: int
    attributed_cost_usd: float
    enrichment_path: Path | None = None
    fallback_enrichment_path: Path | None = None


@dataclass(frozen=True)
class _Target:
    """What every request of a run names: the library it renders, the model and the provider."""

    library: Library
    model: str
    provider: str


@dataclass(frozen=True)
class _RunPlan:
    """What a run decided before any call: lines, batches, passes, policy, model, enrichment."""

    boq: BoqFile
    library_id: str
    profile: RunProfile
    variants: tuple[PromptVariant, ...]
    policy: ResolvedPolicy
    lines: tuple[BoqLine, ...]
    batches: tuple[Batch, ...]
    target: _Target
    select_count: int | None
    enrichment: LoadedEnrichment | None = None
    fallback_enrichment: LoadedEnrichment | None = None

    @property
    def library(self) -> Library:
        """The run's library."""
        return self.target.library

    @property
    def enrichment_sha256(self) -> str | None:
        """The SHA-256 of the enrichment file the run renders, or None."""
        return self.enrichment.sha256 if self.enrichment else None

    @property
    def fallback_enrichment_sha256(self) -> str | None:
        """The SHA-256 of the enrichment file the fallback's lines render, or None."""
        return self.fallback_enrichment.sha256 if self.fallback_enrichment else None

    @property
    def model(self) -> str:
        """The requested primary model."""
        return self.target.model

    @property
    def is_rules_only(self) -> bool:
        """Whether the run is B0, decided by the structural gates alone."""
        return self.profile == RunProfile.B0

    @property
    def is_full(self) -> bool:
        """Whether the run is the full B3 system, with extractors and vetoes."""
        return self.profile == RunProfile.B3


@dataclass(frozen=True)
class _Rescue:
    """Every job's outcome after the fallback took over, if it did (§11.3, A33).

    Attributes:
        outcomes: Each (pass, batch) job's outcome, fallback answers merged in.
        served: Transport ids of the lines the fallback re-ran.
        policy: The policy the fallback's lines are decided with, when it ran.
        declined: The attempts the fallback wrapper declined.
        rerun: The transport ids the fallback re-ran, per (pass, batch) job.

    """

    outcomes: Mapping[JobKey, BatchOutcome]
    served: frozenset[str] = frozenset()
    policy: ResolvedPolicy | None = None
    declined: tuple[DeclinedAttempt, ...] = ()
    rerun: Mapping[JobKey, tuple[str, ...]] = dataclasses.field(default_factory=dict)

    def served_in(self, job: JobKey, transport: str) -> bool:
        """Tell whether the fallback re-ran this line in this (pass, batch) job."""
        return transport in self.rerun.get(job, ())


@dataclass(frozen=True)
class _Verified:
    """The E-08 stage of a run: each flagged line's answer and every verifier attempt.

    Attributes:
        adopted: Whether the run decided with the verifier (B3 with the setting on).
        lines: Transport id -> the flagged line's verifier record.
        records: Every verifier attempt, in request order.

    """

    adopted: bool
    lines: Mapping[str, VerifierLine] = dataclasses.field(default_factory=dict)
    records: tuple[CallRecord, ...] = ()


NOT_VERIFIED = _Verified(adopted=False)


@dataclass(frozen=True)
class _RunInfo:
    """What the manifest records about a run beyond its plan and calls."""

    run_id: str
    source_run_id: str | None
    wall_clock_s: float


@dataclass(frozen=True)
class _LineCalls:
    """One routed line's outcome from every pass, in pass order."""

    outcomes: tuple[LineOutcome, ...]

    def pass_outcomes(self) -> tuple[PassOutcome, ...]:
        """Return the domain outcomes of the passes that reached the model."""
        outcomes = (outcome.pass_outcome() for outcome in self.outcomes)
        return tuple(outcome for outcome in outcomes if outcome is not None)

    def line_failure(self) -> ReasonCode | None:
        """Return the first ``LLM_UNAVAILABLE`` or ``BUDGET_CAP`` of any pass, or None."""
        failures = (outcome.line_failure for outcome in self.outcomes)
        return next((failure for failure in failures if failure is not None), None)

    def raw_responses(self) -> tuple[str, ...]:
        """Return the line's verbatim JSON object from each pass that returned one, valid or not."""
        raws = (outcome.raw_line_response for outcome in self.outcomes)
        return tuple(raw for raw in raws if raw)


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def _selected(boq: BoqFile, select: Collection[str] | None) -> tuple[BoqLine, ...]:
    """Return the selected lines in input order; every line when nothing is selected."""
    if select is None:
        return boq.lines
    chosen = frozenset(select)
    return tuple(line for line in boq.lines if line.line_id in chosen)


def _prompt_line(line: BoqLine) -> BoqLine:
    """Return the line as rendered in the prompt, its long fields cut to their caps (A51)."""
    capped = capped_for_prompt(line)
    if not capped.truncated:
        return line
    return dataclasses.replace(line, item_no=capped.item_no, short=capped.short, long=capped.long)


def _line_text(line: BoqLine) -> str:
    """Return the line's short and long descriptions as one text."""
    return TEXT_SEPARATOR.join((line.short, line.long))


def system_blocks_document(blocks: Sequence[SystemBlock]) -> str:
    """Return the canonical JSON text of system blocks, which ``system_blocks_sha256`` hashes.

    Args:
        blocks: A request's system blocks.

    Returns:
        The text stored at ``prompts/<sha256>.txt``.

    """
    return canonical_json([{"text": block.text, "cache": block.cache} for block in blocks])


def run_mode(adapter: LLMPort, calls: Iterable[CallRecord]) -> RunMode:
    """Tell where a run's responses came from (§9.6).

    Args:
        adapter: The primary adapter behind the run's wrapper.
        calls: Every attempt of the run.

    Returns:
        ``replay`` for a ReplayLLM, ``fake`` for a FakeLLM or a routing port with a FakeLLM
        route, ``cached`` when at least one attempt was a cache hit, else ``live``.

    """
    if isinstance(adapter, ReplayLLM):
        return RunMode.REPLAY
    if isinstance(adapter, FakeLLM):
        return RunMode.FAKE
    if isinstance(adapter, RoutingLLM) and any(isinstance(r, FakeLLM) for r in adapter.routes):
        return RunMode.FAKE
    return RunMode.CACHED if any(record.cache_hit for record in calls) else RunMode.LIVE


def cache_source_run_id(calls: Iterable[CallRecord], sources: Mapping[str, str]) -> str | None:
    """Name the earlier runs a cached run's hits were copied from.

    Args:
        calls: Every attempt of the run.
        sources: Call id -> run id of every response in the cache.

    Returns:
        The distinct source run ids, sorted and ``;``-joined, or None when none is known.

    """
    hits = (record.source_call_id for record in calls if record.cache_hit)
    runs = sorted(
        {sources[call_id] for call_id in hits if call_id is not None and call_id in sources}
    )
    return SOURCE_RUN_SEPARATOR.join(runs) or None


def _unavailable(outcomes: Mapping[JobKey, BatchOutcome]) -> dict[JobKey, tuple[str, ...]]:
    """Return, per job, the transport ids the breaker left ``LLM_UNAVAILABLE``."""
    pending: dict[JobKey, tuple[str, ...]] = {}
    for job, outcome in sorted(outcomes.items()):
        ids = tuple(
            transport
            for transport, line in outcome.lines.items()
            if line.line_failure == ReasonCode.LLM_UNAVAILABLE
        )
        if ids:
            pending[job] = ids
    return pending


def _merged(primary: BatchOutcome, rescue: BatchOutcome) -> BatchOutcome:
    """Overlay a fallback re-run's lines and records on the primary outcome of a job."""
    lines = {**primary.lines, **rescue.lines}
    return BatchOutcome(lines=lines, records=(*primary.records, *rescue.records))


class MatchService:
    """The async orchestrator every driving adapter calls (DESIGN.md §5.2, §11.1)."""

    def __init__(
        self,
        resources: ServiceResources,
        libraries: Mapping[str, Library] | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Bind the service to its resources.

        Args:
            resources: Config the runs read.
            libraries: Pre-loaded libraries by id; others load on first use.
            clock: UTC clock, used only to derive a run id when none is given.
            timer: Monotonic seconds, for the run's wall clock (RQ8).

        """
        self.resources = resources
        self._libraries = dict(libraries or {})
        self._clock = clock
        self._timer = timer

    @classmethod
    def from_settings(cls, settings: Settings) -> "MatchService":
        """Build a service from settings and the files in ``config/``.

        Args:
            settings: The effective settings.

        Returns:
            The service.

        """
        return cls(ServiceResources.from_settings(settings))

    def check_enrichment(self, library_id: str) -> None:
        """Load the enrichment a default B3 run on this library would render (A68.4).

        Args:
            library_id: A configured library id.

        Raises:
            ConfigError: The enrichment the setting or the primary's entry names cannot be
                read, has another SHA-256 than the entry certifies, or is another library's.

        """
        library = self.library(library_id)
        resources = self.resources
        query = PolicyQuery(resources.requested_model, library.sha256, resources.settings.passes_k)
        policy = resolve_policy(resources.policy, query, warn=False)
        if resources.policy_override:
            policy = as_override(policy)
        resolve_enrichment(resources.settings.enrichment, policy, library, resources.root)

    def library(self, library_id: str) -> Library:
        """Return a configured library, loading it once with ``load_catalogue``.

        Args:
            library_id: An id from ``Settings.libraries``.

        Returns:
            The library.

        Raises:
            KeyError: The id is not configured.

        """
        if library_id not in self._libraries:
            settings = self.resources.settings
            loaded = load_catalogue(library_id, settings, self.resources.never_match)
            self._libraries[library_id] = loaded
        return self._libraries[library_id]

    async def match(
        self,
        boq: BoqFile,
        library_id: str,
        *,
        profile: RunProfile,
        llm: LLMWrapper,
        options: RunOptions = DEFAULT_OPTIONS,
    ) -> RunResult:
        """Decide every line of a parsed BoQ, or the selected ones.

        Args:
            boq: The parsed input; headers and section paths were computed on the whole file.
            library_id: An id from ``Settings.libraries``.
            profile: B2 (raw single pass, every valid answer matched) or B3 (full table).
            llm: The run's LLM wrapper; a ``ReplayLLM`` adapter makes the run a replay.
            options: Threshold override, line selection, run id and source run id.

        Returns:
            The run's decisions, call records, audit records and manifest fields.

        Raises:
            KeyError: The library id is not configured.
            ValueError: More passes are configured than there are pre-registered renderings.

        """
        started = self._timer()
        provider = provider_for(self.resources.requested_model, llm.pricing)
        plan = self._plan(boq, library_id, profile, options, provider)
        outcomes = await self._call_passes(plan, llm)
        rescue = await self._rescue(plan, llm, outcomes, options)
        verified = await self._verify(plan, llm, _Inputs(self.resources, plan, rescue))
        identity = options.run_id or make_run_id(self._clock(), self._run_seed(plan))
        assembly = _Assembly(self.resources, plan, rescue, llm, verified)
        source = options.source_run_id
        if assembly.mode == RunMode.CACHED:
            source = cache_source_run_id(assembly.calls, options.cache_sources)
        return assembly.result(_RunInfo(identity, source, self._timer() - started))

    def _variants(self, profile: RunProfile) -> tuple[PromptVariant, ...]:
        """Return one prompt variant per pass: none for B0, B2 alone, or canonical then reverse."""
        if profile == RunProfile.B0:
            return ()
        if profile == RunProfile.B2:
            return (B2,)
        passes = self.resources.settings.passes_k
        if passes > len(V1_PASS_VARIANTS):
            raise ValueError(
                f"passes_k={passes}, but only {len(V1_PASS_VARIANTS)} pre-registered renderings "
                "exist, one per pass"
            )
        return V1_PASS_VARIANTS[:passes]

    def _policy(
        self,
        boq: BoqFile,
        target: _Target,
        profile: RunProfile,
        threshold: Threshold | None,
        *,
        warn: bool = True,
    ) -> ResolvedPolicy:
        """Resolve the threshold for the target's model; B2 always matches every valid answer.

        B0 never scores a line, so it records its own ``b0_rules_only`` resolution. An exact hit
        of a ``--policy`` file is recorded as an ``override`` (§10.6). ``warn`` off resolves a
        policy the run may never decide under, so a strictest one logs nothing.
        """
        if profile == RunProfile.B0:
            return ResolvedPolicy(B0_THRESHOLD, B0_POLICY_ID, PolicyResolution.B0_RULES_ONLY)
        if profile == RunProfile.B2:
            return ResolvedPolicy(B2_THRESHOLD, B2_POLICY_ID, PolicyResolution.B2_MATCH_ALL)
        query = PolicyQuery(
            model_id=target.model,
            library_sha256=target.library.sha256,
            passes=self.resources.settings.passes_k,
            path_derived=boq.path_mode == PathMode.DERIVED,
            override=threshold,
        )
        resolved = resolve_policy(self.resources.policy, query, warn=warn)
        return as_override(resolved) if self.resources.policy_override else resolved

    def _plan(
        self, boq: BoqFile, library_id: str, profile: RunProfile, options: RunOptions, provider: str
    ) -> _RunPlan:
        """Fix the output lines, batches, passes, policy, model and enrichment before any call."""
        target = _Target(self.library(library_id), self.resources.requested_model, provider)
        lines = _selected(boq, options.select)
        policy = self._policy(boq, target, profile, options.threshold)
        fallback = self._fallback_enrichment(boq, target, profile, options)
        return _RunPlan(
            boq=boq,
            library_id=library_id,
            profile=profile,
            variants=self._variants(profile),
            policy=policy,
            lines=lines,
            batches=self._batches(profile, lines),
            target=target,
            select_count=None if options.select is None else len(lines),
            enrichment=self._enrichment(profile, policy, target.library),
            fallback_enrichment=fallback,
        )

    def _enrichment(
        self,
        profile: RunProfile,
        policy: ResolvedPolicy,
        library: Library,
        forced: Path | None = None,
    ) -> LoadedEnrichment | None:
        """Return the enrichment a B3 run renders; B0 and B2 never render one (A68.3, A68.4).

        ``forced`` wins over the setting, which wins over the policy entry.
        """
        if profile != RunProfile.B3:
            return None
        resources = self.resources
        chosen = forced or resources.settings.enrichment
        return resolve_enrichment(chosen, policy, library, resources.root)

    def _fallback_enrichment(
        self, boq: BoqFile, target: _Target, profile: RunProfile, options: RunOptions
    ) -> LoadedEnrichment | None:
        """Return the enrichment the lines the A33 fallback re-runs render (A68.4, as in A67).

        It follows the fallback's own policy entry, so a fallback certified without an
        enrichment never inherits the primary's; a forced value applies to both, and a replay
        forces the one its recorded fallback rendered. It is resolved and checked before any
        call, whether or not the breaker later trips.
        """
        if options.fallback is None or profile != RunProfile.B3:
            return None
        resources = self.resources
        fallback = dataclasses.replace(target, model=resources.fallback_model)
        policy = self._policy(boq, fallback, profile, options.threshold, warn=False)
        return self._enrichment(profile, policy, target.library, resources.fallback_enrichment)

    def _batches(self, profile: RunProfile, lines: Sequence[BoqLine]) -> tuple[Batch, ...]:
        """Plan the routed batches; B0 routes nothing, so it makes no call."""
        if profile == RunProfile.B0:
            return ()
        return tuple(plan_batches(lines, self.resources.settings.batch_size))

    def _run_seed(self, plan: _RunPlan) -> str:
        """Return the text a derived run id hashes: library, profile and output lines."""
        line_ids = [line.line_id for line in plan.lines]
        return canonical_json([plan.library.sha256, plan.profile.value, line_ids])

    async def _call_passes(self, plan: _RunPlan, llm: LLMWrapper) -> dict[JobKey, BatchOutcome]:
        """Run every (pass, batch) job: each pass's first batch alone, then the rest fan out.

        Sending the first batch of each rendering alone writes its cached prefix once before
        the fan-out reads it (§11.3); the semaphore caps calls in flight.
        """
        jobs = [(p, b) for p in range(len(plan.variants)) for b in range(len(plan.batches))]
        semaphore = asyncio.Semaphore(self.resources.settings.concurrency)
        results: dict[JobKey, BatchOutcome] = {}
        for job in (job for job in jobs if job[1] == 0):
            results[job] = await self._guarded(semaphore, plan, llm, (job, None))
        rest = [job for job in jobs if job[1] != 0]
        gathered = await asyncio.gather(
            *(self._guarded(semaphore, plan, llm, (job, None)) for job in rest)
        )
        results.update(zip(rest, gathered, strict=True))
        return results

    async def _guarded(
        self,
        semaphore: asyncio.Semaphore,
        plan: _RunPlan,
        llm: LLMWrapper,
        work: tuple[JobKey, tuple[str, ...] | None],
    ) -> BatchOutcome:
        """Run one job, or some of its transport ids, under the concurrency semaphore."""
        (pass_index, batch_index), ids = work
        batch = plan.batches[batch_index]
        build = self._builder(plan, batch, plan.variants[pass_index])
        async with semaphore:
            return await llm.run_batch(batch.transport_ids if ids is None else ids, build)

    async def _rescue(
        self,
        plan: _RunPlan,
        llm: LLMWrapper,
        outcomes: Mapping[JobKey, BatchOutcome],
        options: RunOptions,
    ) -> _Rescue:
        """Re-run the lines the tripped breaker left pending on the fallback adapter (A33).

        The fallback wrapper shares the run's ledger, limits, recording and cache; its lines
        are decided at the fallback model's policy, the strictest threshold unless certified.
        A line is ``LLM_UNAVAILABLE`` only once the breaker tripped (live) or where the
        recorded run declined it (replay), so pending lines alone decide the take-over: a
        replay's own breaker sees the attempts in another order and may not have tripped.
        """
        pending = _unavailable(outcomes)
        if options.fallback is None or not pending:
            return _Rescue(outcomes)
        model = self.resources.fallback_model
        target = _Target(plan.library, model, provider_for(model, llm.pricing))
        LOGGER.warning("circuit breaker tripped; the fallback %s takes over", model)
        backup = LLMWrapper(options.fallback, llm.pricing, llm.ledger, llm.policy, llm.deps)
        semaphore = asyncio.Semaphore(self.resources.settings.concurrency)
        rescue_plan = dataclasses.replace(plan, target=target, enrichment=plan.fallback_enrichment)
        reruns = await asyncio.gather(
            *(self._guarded(semaphore, rescue_plan, backup, work) for work in pending.items())
        )
        merged = dict(outcomes)
        for job, rerun in zip(pending, reruns, strict=True):
            merged[job] = _merged(merged[job], rerun)
        served = frozenset(transport for ids in pending.values() for transport in ids)
        policy = self._policy(plan.boq, target, plan.profile, options.threshold)
        return _Rescue(merged, served, policy, tuple(backup.declined), dict(pending))

    async def _verify(self, plan: _RunPlan, llm: LLMWrapper, inputs: "_Inputs") -> _Verified:
        """Ask the sibling verifier about every flagged line of a B3 run that adopted E-08.

        The flagged lines are those the table matches with the verifier off (A65.2). Their
        requests go through the run's wrapper, so retries, the budget and its waits (A61), the
        breaker, recording, the cache and replay apply as to any call.

        Whether the stage runs follows the policy the run decides under (A67): the primary's
        entry, or the fallback's once the fallback rescued the main passes. The stage always
        uses the primary wrapper, never the A33 fallback: E-08 is measured on the primary
        model. When the settings force it on after the fallback rescued the main passes, the
        primary breaker is open, so every flagged line fails closed as D1b ``partial_signal``
        with ``LLM_UNAVAILABLE`` and the run exits 3; the same holds when the verifier calls
        trip the breaker themselves.
        """
        profile = self.resources.decision_profile(inputs.policy)
        if not plan.is_full or not profile.verifier_adopted:
            return NOT_VERIFIED
        flagged = self._flagged(plan, inputs, profile)
        groups = verifier_groups(flagged, plan.library, self.resources.settings.batch_size)
        semaphore = asyncio.Semaphore(self.resources.settings.concurrency)
        outcomes = await asyncio.gather(
            *(self._verify_group(semaphore, plan, llm, group) for group in groups)
        )
        lines = {
            transport: verifier_line(outcome.lines[transport], group.codes)
            for group, outcome in zip(groups, outcomes, strict=True)
            for transport in group.transport_ids
        }
        records = tuple(record for outcome in outcomes for record in outcome.records)
        return _Verified(adopted=True, lines=lines, records=records)

    def _flagged(
        self, plan: _RunPlan, inputs: "_Inputs", profile: DecisionProfile
    ) -> list[tuple[BoqLine, str]]:
        """Return (line, top1) for every routed line the table matches with the verifier off."""
        flagged: list[tuple[BoqLine, str]] = []
        for line in plan.lines:
            calls = inputs.line_calls(line)
            if calls is None:
                continue
            top1 = flagged_top1(inputs.decision_input(line, calls), plan.library, profile)
            if top1 is not None:
                flagged.append((line, top1))
        return flagged

    async def _verify_group(
        self, semaphore: asyncio.Semaphore, plan: _RunPlan, llm: LLMWrapper, group: VerifierGroup
    ) -> BatchOutcome:
        """Run one verifier request, for lines of one material type, under the semaphore."""
        by_transport = {transport_id(line): _prompt_line(line) for line in group.lines}
        target = plan.target

        def build(ids: tuple[str, ...]) -> LLMRequest:
            """Render the verifier request for these transport ids, in this order."""
            request = build_verifier_request(
                [by_transport[transport] for transport in ids],
                group.rows,
                model=target.model,
                max_tokens=self.resources.max_tokens,
            )
            return dataclasses.replace(request, provider=target.provider, line_ids=tuple(ids))

        async with semaphore:
            return await llm.run_batch(group.transport_ids, build, VERIFIER_ANSWERS)

    def _builder(self, plan: _RunPlan, batch: Batch, variant: PromptVariant) -> RequestBuilder:
        """Return the wrapper's request builder for any subset of one batch's transport ids."""
        by_transport = {
            transport: _prompt_line(line)
            for transport, line in zip(batch.transport_ids, batch.lines, strict=True)
        }
        target = plan.target
        enrichment = plan.enrichment.content if plan.enrichment else None

        def build(ids: tuple[str, ...]) -> LLMRequest:
            """Render the request for these transport ids, in this order."""
            request = build_enriched_request(
                [by_transport[transport] for transport in ids],
                target.library,
                variant,
                enrichment,
                model=target.model,
                max_tokens=self.resources.max_tokens,
            )
            return dataclasses.replace(request, provider=target.provider, line_ids=tuple(ids))

        return build


class _Inputs:
    """Builds what the decision table reads for each line from a run's pass outcomes."""

    def __init__(self, resources: ServiceResources, plan: _RunPlan, rescue: _Rescue) -> None:
        """Index the outcomes by transport id.

        Args:
            resources: The service's resources.
            plan: The run's plan.
            rescue: Every job's outcome, with any fallback re-run merged in.

        """
        self.resources = resources
        self.plan = plan
        self.policy = rescue.policy or plan.policy
        self.outcomes = rescue.outcomes
        self.batch_of = {
            transport: index
            for index, batch in enumerate(plan.batches)
            for transport in batch.transport_ids
        }

    def line_calls(self, line: BoqLine) -> _LineCalls | None:
        """Return a routed line's outcome from every pass, or None for a rule-decided line."""
        transport = transport_id(line)
        batch_index = self.batch_of.get(transport)
        if batch_index is None:
            return None
        passes = range(len(self.plan.variants))
        return _LineCalls(tuple(self.outcomes[p, batch_index].lines[transport] for p in passes))

    def decision_input(
        self, line: BoqLine, calls: _LineCalls | None, verifier_top1: str | None = None
    ) -> DecisionInput:
        """Build what the decision table reads for one line."""
        full = self.plan.is_full
        text = _line_text(line)
        return DecisionInput(
            line=line,
            haystack=make_haystack(line),
            attributes=extract(text) if full else Attributes(),
            has_supply_marker=full and has_supply_marker(text, self.resources.supply_markers),
            is_service_unit=is_service_unit(line.unit, self.resources.service_units),
            passes=calls.pass_outcomes() if calls else (),
            threshold=self.policy.threshold,
            line_failure=calls.line_failure() if calls else None,
            verifier_top1=verifier_top1,
        )


class _Assembly:
    """Turns a run's batch outcomes into ordered line results, audit records and a manifest."""

    def __init__(
        self,
        resources: ServiceResources,
        plan: _RunPlan,
        rescue: _Rescue,
        llm: LLMWrapper,
        verified: _Verified = NOT_VERIFIED,
    ) -> None:
        """Index the outcomes by transport id and attribute every attempt to its lines.

        Args:
            resources: The service's resources.
            plan: The run's plan.
            rescue: Every job's outcome, with any fallback re-run merged in.
            llm: The primary wrapper the run used.
            verified: The E-08 stage; its attempts follow the passes' in ``calls``.

        """
        self.resources = resources
        self.plan = plan
        self.rescue = rescue
        self.verified = verified
        self.inputs = _Inputs(resources, plan, rescue)
        self.policy = self.inputs.policy
        outcomes = rescue.outcomes
        passes = (record for job in sorted(outcomes) for record in outcomes[job].records)
        self.calls = (*passes, *verified.records)
        self.mode = RunMode.RULES if plan.is_rules_only else run_mode(llm.adapter, self.calls)
        self.attribution: dict[str, LineAttribution] = attribute_costs(self.calls)
        sha = plan.enrichment_sha256
        self.versions = tuple(prompt_version(variant, sha) for variant in plan.variants)
        self.fallback_versions: tuple[str, ...] | None = None
        if rescue.policy is not None:
            fallback_sha = plan.fallback_enrichment_sha256
            versions = (prompt_version(variant, fallback_sha) for variant in plan.variants)
            self.fallback_versions = tuple(versions)
        self.declined = (*llm.declined, *rescue.declined)

    def result(self, info: _RunInfo) -> RunResult:
        """Build the run result."""
        lines = tuple(self._line_result(line) for line in self.plan.lines)
        attributed = sum(item.cost_usd for item in lines)
        exit_code = _exit_code(lines)
        return RunResult(
            run_id=info.run_id,
            profile=self.plan.profile,
            library_id=self.plan.library_id,
            library=self.plan.library,
            input_header=self.plan.boq.header,
            lines=lines,
            calls=self.calls,
            audit=tuple(audit_record(item) for item in lines),
            manifest=self._manifest(info, lines, attributed, exit_code),
            policy=self.policy,
            prompt_versions=self.versions,
            system_prompts=self._system_prompts(),
            exit_code=exit_code,
            attributed_cost_usd=attributed,
            enrichment_path=self.plan.enrichment.path if self.plan.enrichment else None,
            fallback_enrichment_path=self._fallback_enrichment_path(),
        )

    def _fallback_enrichment_path(self) -> Path | None:
        """Return the enrichment file the fallback's lines rendered, once it rescued the run."""
        enrichment = self.plan.fallback_enrichment
        return enrichment.path if enrichment and self.rescue.policy else None

    def _line_versions(self, line: BoqLine) -> str:
        """Return the prompt version of each pass that answered a line, the fallback's included."""
        transport = transport_id(line)
        batch_index = self.inputs.batch_of.get(transport)
        fallback = self.fallback_versions
        if fallback is None or batch_index is None or transport not in self.rescue.served:
            return PROMPT_VERSION_SEPARATOR.join(self.versions)
        versions = (
            fallback[index] if self.rescue.served_in((index, batch_index), transport) else own
            for index, own in enumerate(self.versions)
        )
        return PROMPT_VERSION_SEPARATOR.join(versions)

    def _verifier(self, line: BoqLine) -> VerifierLine | None:
        """Return the line's E-08 record when the run adopted the verifier, else None."""
        if not self.verified.adopted:
            return None
        return self.verified.lines.get(transport_id(line), NOT_FLAGGED)

    @property
    def decision_profile(self) -> DecisionProfile:
        """The profile that decides the run: B3 follows its deciding policy (A67); B0, B2 none."""
        if not self.plan.is_full:
            return DEFAULT_PROFILE
        return self.resources.decision_profile(self.policy)

    def _decide(self, decision_input: DecisionInput) -> LineDecision:
        """Apply the profile's decision table."""
        if self.plan.is_rules_only:
            return decide_b0(decision_input, self.plan.library)
        if self.plan.is_full:
            return decide(decision_input, self.plan.library, self.decision_profile)
        return decide_b2(decision_input, self.plan.library)

    def _line_result(self, line: BoqLine) -> LineResult:
        """Decide one line and attach its attribution, suggestions and audit fields."""
        calls = self.inputs.line_calls(line)
        verifier = self._verifier(line)
        verifier_top1 = verifier.top1 if verifier else None
        decision_input = self.inputs.decision_input(line, calls, verifier_top1)
        decision = self._decide(decision_input)
        attribution = self.attribution.get(transport_id(line))
        suggested = self._row(decision.top1)
        return LineResult(
            line=line,
            decision=decision,
            model=self._model(calls, attribution),
            prompt_version=self._line_versions(line),
            cost_usd=attribution.cost_usd if attribution else 0.0,
            latency_ms=attribution.latency_ms if attribution else 0,
            call_ids=attribution.call_ids if attribution else (),
            suggested=suggested,
            suggested2=self._row(decision.top2),
            attribute_result=self._attribute_result(decision_input, suggested),
            raw_line_responses=calls.raw_responses() if calls else (),
            context=CONTEXT_PATH if self.plan.is_full and line.section_path else CONTEXT_NONE,
            flags=(TRUNCATED_FLAG,) if calls and capped_for_prompt(line).truncated else (),
            verifier=verifier,
        )

    def _row(self, code: str) -> LibraryRow | None:
        """Return the library row of a valid code, else None."""
        library = self.plan.library
        return library.by_code[code] if is_valid_code(code, library) else None

    def _attribute_result(
        self, decision_input: DecisionInput, row: LibraryRow | None
    ) -> AttrResult | None:
        """Return signal (b) of the suggested row, B3 only."""
        if row is None or not self.plan.is_full:
            return None
        return compare(decision_input.attributes, row.attributes)

    def _model(self, calls: _LineCalls | None, attribution: LineAttribution | None) -> str:
        """Return the model column: the last served model, ``rules``, or the requested model."""
        if calls is None:
            return RULES_MODEL
        call_ids = set(attribution.call_ids) if attribution else set()
        served = [
            r.response_model for r in self.calls if r.call_id in call_ids and r.response_model
        ]
        return served[-1] if served else self.plan.model

    def _system_prompts(self) -> dict[str, str]:
        """Return each pass's system blocks as stored text, keyed by their hash."""
        prompts: dict[str, str] = {}
        plan = self.plan
        rendered = [plan.enrichment]
        if self.rescue.policy is not None:
            rendered.append(plan.fallback_enrichment)
        for loaded in rendered:
            enrichment = loaded.content if loaded else None
            for variant in plan.variants:
                blocks = render_enriched_system_blocks(plan.library, variant, enrichment)
                prompts[system_blocks_sha256(blocks)] = system_blocks_document(blocks)
        if self.verified.adopted:
            verifier = render_verifier_system()
            prompts[system_blocks_sha256(verifier)] = system_blocks_document(verifier)
        return prompts

    def _fallback_enrichment_fields(self) -> dict[str, Any]:
        """Return the enrichment and prompt versions the fallback's lines rendered (A68.4)."""
        engaged = self.rescue.policy is not None
        return {
            "fallback_enrichment_sha256": (
                self.plan.fallback_enrichment_sha256 if engaged else None
            ),
            "fallback_prompt_version": (
                list(self.fallback_versions) if self.fallback_versions is not None else None
            ),
        }

    def _manifest(
        self, info: _RunInfo, lines: Sequence[LineResult], attributed: float, exit_code: int
    ) -> dict[str, Any]:
        """Return the manifest fields the service knows (§9.6)."""
        plan, resources = self.plan, self.resources
        return {
            "run_id": info.run_id,
            "mode": self.mode.value,
            "source_run_id": info.source_run_id,
            "wall_clock_s": round(info.wall_clock_s, WALL_CLOCK_DIGITS),
            "profile": plan.profile.value,
            "requested_model": plan.model,
            "fallback_model": resources.fallback_model,
            **_fallback_fields(self.rescue),
            **self._fallback_enrichment_fields(),
            "served_models": sorted({r.response_model for r in self.calls if r.response_model}),
            **_library_fields(plan),
            "prompt_version": list(self.versions),
            "passes_k": len(plan.variants),
            "enrichment_sha256": plan.enrichment_sha256,
            **_candidate_fields(plan),
            **_policy_fields(self.policy),
            **decision_profile_fields(self.decision_profile),
            **_verifier_fields(self.verified, resources.settings.batch_size),
            "path_mode": plan.boq.path_mode.value,
            "encoding": plan.boq.encoding,
            "delimiter": plan.boq.delimiter,
            "temperature": TEMPERATURE,
            "max_tokens": resources.max_tokens,
            **_usage_fields(self.calls, attributed),
            **_count_fields(plan, lines),
            "declined_attempts": declined_records(self.declined),
            "exit_code": exit_code,
        }


def decide_b0(decision_input: DecisionInput, library: Library) -> LineDecision:
    """Decide one line as rung B0 of the baseline ladder: rules only (§7.1, §10.5).

    D0, D0a and D0b fire exactly as in B3, so G1 headers are ``not_a_material``; every other
    line is D10 ``needs_review`` with ``LOW_SIGNAL:v+b+confidence``, because no signal exists
    without a model (A58 item 1). There is no unit-only skip: D2 needs a model's ``kind``.

    Args:
        decision_input: The line's input; B0 gives it no pass outcome.
        library: The loaded library.

    Returns:
        The line's decision.

    """
    gated = decide(decision_input, library)
    if gated.rule in STRUCTURAL_RULES:
        return gated
    return LineDecision(
        rule=Rule.D10,
        decision=Decision.NEEDS_REVIEW,
        reason=B0_REVIEW_REASON,
        line_id=decision_input.line.line_id,
    )


def _exit_code(lines: Iterable[LineResult]) -> int:
    """Return 3 when any line or its verifier is ``LLM_UNAVAILABLE`` or a replay miss (§11.3)."""
    unavailable = any(
        item.decision.reason in UNAVAILABLE_REASONS
        or (item.verifier is not None and item.verifier.is_unavailable)
        for item in lines
    )
    return EXIT_LLM_UNAVAILABLE if unavailable else EXIT_OK


def _verifier_fields(verified: _Verified, batch_size: int) -> dict[str, Any]:
    """Return the manifest's ``verifier`` field for a run that adopted E-08, else nothing."""
    if not verified.adopted:
        return {}
    answered = [line for line in verified.lines.values() if line.top1 is not None]
    return {
        VERIFIER_KEY: {
            "prompt_version": verifier_prompt_version(),
            "batching": BATCHING,
            "batch_size": batch_size,
            "flagged_count": len(verified.lines),
            "answered_count": len(answered),
            "call_count": len(verified.records),
        }
    }


def _library_fields(plan: _RunPlan) -> dict[str, Any]:
    """Return the manifest's library fields, with the measured or estimated rendered size."""
    library = plan.library
    by_variant = dict(library.rendered_tokens_by_variant)
    tokens = library.rendered_tokens
    return {
        "library_id": plan.library_id,
        "library_sha256": library.sha256,
        "library_rows": len(library.rows),
        "library_rendered_tokens": {
            "tokens": tokens,
            "by_variant": by_variant,
            "estimated": library.rendered_tokens_estimated,
            "cache_eligible": tokens is not None and tokens >= CACHE_MIN_TOKENS,
        },
    }


def _candidate_fields(plan: _RunPlan) -> dict[str, Any]:
    """Return the candidate provider and its inclusion; the whole library is the default."""
    provider = WholeLibrary(plan.library)
    return {
        "candidate_provider": provider.name,
        "candidate_inclusion": provider.inclusion(plan.lines),
    }


def _policy_fields(policy: ResolvedPolicy) -> dict[str, Any]:
    """Return the manifest's policy fields."""
    return {
        "policy_id": policy.policy_id,
        "policy_resolution": policy.resolution.value,
        "policy_certified_by": policy.certified_by,
        "policy_claimed_certified_by": policy.claimed_certified_by,
        "threshold_id": policy.threshold.threshold_id,
    }


def declined_records(declined: Iterable[DeclinedAttempt]) -> list[dict[str, Any]]:
    """Return the manifest's ``declined_attempts``, sorted so the manifest is stable.

    Args:
        declined: Every attempt the run's wrappers declined.

    Returns:
        One record per attempt, by request hash, parent, attempt number and reason.

    """
    records = [attempt.to_json() for attempt in declined]
    return sorted(records, key=lambda r: canonical_json([r[k] for k in sorted(r)]))


def _fallback_fields(rescue: _Rescue) -> dict[str, Any]:
    """Return whether the fallback took over, for how many lines, and at which policy."""
    policy = rescue.policy
    return {
        "fallback_engaged": policy is not None,
        "fallback_line_count": len(rescue.served),
        "fallback_policy_id": policy.policy_id if policy else None,
        "fallback_policy_resolution": policy.resolution.value if policy else None,
        "fallback_threshold_id": policy.threshold.threshold_id if policy else None,
    }


def _usage_fields(calls: Sequence[CallRecord], attributed: float) -> dict[str, Any]:
    """Return spend, attributed cost, cache hits and cache tokens over a run's attempts."""
    return {
        "spend_usd": sum(record.cost_usd for record in calls if not record.cache_hit),
        "attributed_cost_usd": attributed,
        "call_count": len(calls),
        "cache_hits": sum(record.cache_hit for record in calls),
        "cache_read_tokens": sum(record.cache_read for record in calls),
        "cache_write_tokens": sum(record.cache_creation for record in calls),
    }


def _count_fields(plan: _RunPlan, lines: Sequence[LineResult]) -> dict[str, Any]:
    """Return the line counts, decision shares and the reason-code histogram (§11.6)."""
    decisions = Counter(item.decision.decision.value for item in lines)
    reasons = Counter(str(item.decision.reason) for item in lines)
    return {
        "line_count": len(lines),
        "routed_count": sum(line.kind == LineKind.ITEM for line in plan.lines),
        "n_routed": sum(len(batch.lines) for batch in plan.batches),
        "select_count": plan.select_count,
        "decision_counts": dict(sorted(decisions.items())),
        "reason_counts": dict(sorted(reasons.items())),
    }


def _signals_record(signals: Signals | None) -> dict[str, Any] | None:
    """Return score s as an audit object, or None when the line was not scored."""
    if signals is None:
        return None
    return {
        "v": signals.votes,
        "b": signals.attributes.value,
        "confidence_bucket": BUCKET_LABELS[signals.confidence],
    }


def _verifier_record(verifier: VerifierLine | None) -> dict[str, Any]:
    """Return a line's E-08 audit fields when the run adopted the verifier, else nothing."""
    if verifier is None:
        return {}
    return {
        "verifier_flagged": verifier.flagged,
        "verifier_top1": verifier.top1,
        "verifier_raw": verifier.raw,
        "verifier_call_ids": list(verifier.call_ids),
        "verifier_failure": verifier.failure,
    }


def audit_record(item: LineResult) -> dict[str, Any]:
    """Build one ``audit.jsonl`` record (§9.6).

    Args:
        item: One output line.

    Returns:
        The rule fired, reason, signals, top-1/top-2, attribute result, raw line responses,
        call ids, context and flags, keyed by the line id; with the verifier adopted, also its
        flag, validated answer, raw answer, call ids and failure.

    """
    decision = item.decision
    return {
        **_verifier_record(item.verifier),
        "line_id": item.line.line_id,
        "position": item.line.position,
        "item_no": item.line.item_no,
        "transport_id": transport_id(item.line),
        "decision": decision.decision.value,
        "rule": decision.rule.value,
        "reason": str(decision.reason),
        "signals": _signals_record(decision.signals),
        "top1": decision.top1,
        "top2": decision.top2,
        "suggested_row_id": item.suggested.row_id if item.suggested else None,
        "attribute_result": item.attribute_result.value if item.attribute_result else None,
        "raw_line_response": list(item.raw_line_responses),
        "call_ids": list(item.call_ids),
        "model": item.model,
        "cost_usd": item.cost_usd,
        "latency_ms": item.latency_ms,
        "context": item.context,
        "flags": list(item.flags),
    }
