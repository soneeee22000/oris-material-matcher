"""Runtime wiring for every driving adapter, and ``oris doctor`` (DESIGN.md §11.3, §12, A33, A39).

The wiring builds what a run talks to: the ``--llm`` spec, the adapter behind the port and the
LLM wrapper. A live adapter is constructed only when it is selected, and a missing key is a
``WiringError`` naming the variable, never a traceback. The CLI, the API and the experiment
runner all use it.

The doctor checks the environment. Offline: config, allowlist, the pinned model, prices, which
keys are present (never their values) and that every library loads. With ``live``: one call of
two synthetic lines (never lines of a BoQ input) with the production schema through the real
wrapper, recorded in a doctor run folder; ``count_tokens`` of each library's rendered prompt;
the ``anthropic-ratelimit-*`` headers and the tier they imply; and the fallback adapter, only
when its key is present. The report goes to ``evidence/doctor_<YYYY-MM-DD>.json``.
"""

import asyncio
import dataclasses
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import anthropic
import httpx2
from anthropic.types import MessageParam

from oris_matcher.domain.boq import BoqLine, LineKind
from oris_matcher.domain.decision import LLM_FAILURE_PREFIX, ReasonCode
from oris_matcher.domain.library import Library
from oris_matcher.io import audit
from oris_matcher.io.audit import (
    CALLS_FILE,
    MANIFEST_FILE,
    GitRunner,
    ManifestContext,
    build_manifest,
    code_version,
    write_run,
)
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.llm.anthropic_llm import AnthropicLLM, system_param
from oris_matcher.llm.base import AdapterOptions, LLMPort, LLMRequest, LLMResult, LLMStatus
from oris_matcher.llm.fake_llm import FakeLLM
from oris_matcher.llm.openai_llm import OpenAIChatLLM
from oris_matcher.llm.recording import DeclinedAttempt
from oris_matcher.llm.replay_llm import RecordedRun, ReplayLLM, ResponseCache
from oris_matcher.llm.wrapper import BudgetLedger, LLMWrapper, WrapperDeps, WrapperPolicy
from oris_matcher.prompts.v1.render import CANONICAL_V1, VARIANTS, PromptVariant, build_request
from oris_matcher.prompts.v1.version import prompt_version
from oris_matcher.service import (
    CACHE_MIN_TOKENS,
    DEFAULT_MAX_TOKENS,
    MatchService,
    RunOptions,
    RunProfile,
    RunResult,
    ServiceResources,
    load_catalogue,
    make_run_id,
    provider_for,
)
from oris_matcher.settings import (
    MODELS_FILE,
    PRICING_FILE,
    ConfigError,
    ModelsConfig,
    PinnedModels,
    PricingTable,
    Settings,
    load_models_config,
    load_pricing,
    resolve_models,
)

TEXT_ENCODING = "utf-8"
JSON_INDENT = 2
JSON_NEWLINE = "\n"
SPEC_SEPARATOR = ":"
SNAPSHOT_SEPARATOR = "-"
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
OPENAI_KEY_ENV = "OPENAI_API_KEY"
DOCTOR_RUN_PREFIX = "doctor-"
DOCTOR_BUDGET_USD = 0.25
DOCTOR_PASSES = 1
DOCTOR_RETRIES = 0
EVIDENCE_PREFIX = "doctor_"
EVIDENCE_SUFFIX = ".json"
DEFAULT_EVIDENCE_DIR = Path("evidence")
DEFAULT_RUNS_DIR = Path("runs")
SHA_DISPLAY_LENGTH = 12
REQUESTS_LIMIT_HEADER = "anthropic-ratelimit-requests-limit"
TIER_BY_REQUESTS_LIMIT: Mapping[int, str] = {50: "1", 1000: "2", 2000: "3", 4000: "4"}
TIER_ABOVE_PUBLISHED = "custom (above tier 4)"
TIER_UNKNOWN = "unknown"
RATE_LIMIT_FROM_CALL = "last_live_call"
RATE_LIMIT_NONE = "none"
RATE_LIMIT_CHECK = "rate_limit_tier"
DECLINED_FIELD = "declined_attempts"
COUNT_CHECK = "count_tokens"
RENDERED_CHECK = "rendered_tokens"
TOKEN_CHECKS = (COUNT_CHECK, RENDERED_CHECK)
SYNTHETIC_BOQ = (
    "Item No.,Short Description,Long Description,Unit,BoQ Qty\r\n"
    "D.1,Ready-mix concrete C30/37 for foundations,,m3,10\r\n"
    'D.2,"Site supervision, lump sum",,LS,1\r\n'
).encode(TEXT_ENCODING)


class WiringError(Exception):
    """A run cannot be wired: a bad ``--llm`` spec, a missing key or a missing run folder."""


class LLMKind(StrEnum):
    """What answers a run's requests."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    FAKE = "fake"
    REPLAY = "replay"


LIVE_KINDS = frozenset({LLMKind.ANTHROPIC, LLMKind.OPENAI})
KEY_ENV: Mapping[LLMKind, str] = {
    LLMKind.ANTHROPIC: ANTHROPIC_KEY_ENV,
    LLMKind.OPENAI: OPENAI_KEY_ENV,
}


@dataclass(frozen=True)
class LLMSpec:
    """A parsed ``--llm`` value.

    Attributes:
        kind: The adapter kind.
        model: The model named after ``anthropic:`` or ``openai:``, else None.
        run_dir: The run folder after ``replay:``, else None.

    """

    kind: LLMKind
    model: str | None = None
    run_dir: Path | None = None

    @property
    def text(self) -> str:
        """The spec as written on the command line."""
        detail = self.model or (self.run_dir.as_posix() if self.run_dir else "")
        return f"{self.kind.value}{SPEC_SEPARATOR}{detail}" if detail else self.kind.value


def parse_llm_spec(text: str) -> LLMSpec:
    """Parse ``anthropic:<model>``, ``openai:<model>``, ``fake`` or ``replay:<run_dir>``.

    Args:
        text: The ``--llm`` value; a bare provider name means its pinned model.

    Returns:
        The spec.

    Raises:
        WiringError: The kind is unknown, or ``replay`` names no folder.

    """
    head, _, tail = text.partition(SPEC_SEPARATOR)
    try:
        kind = LLMKind(head.strip())
    except ValueError as error:
        choices = ", ".join(kind.value for kind in LLMKind)
        raise WiringError(f"--llm {text!r}: unknown adapter; use one of {choices}") from error
    tail = tail.strip()
    if kind == LLMKind.REPLAY:
        if not tail:
            raise WiringError("--llm replay needs a run folder: replay:<run_dir>")
        return LLMSpec(kind, run_dir=Path(tail))
    if kind == LLMKind.FAKE and tail:
        raise WiringError("--llm fake takes no model")
    return LLMSpec(kind, model=tail or None)


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def _no_http_client() -> httpx2.AsyncClient | None:
    """Return no injected HTTP client, so the SDK builds its own."""
    return None


AdapterFactory = Callable[[LLMSpec, str], LLMPort]


@dataclass(frozen=True)
class Runtime:
    """Everything a command reads from its environment besides settings; injectable in tests.

    Attributes:
        root: The repository root; manifests are written relative to it.
        clock: UTC clock for run ids and record timestamps.
        sleep: Async sleep used for retry waits.
        http_client: Builds the HTTP client injected into live SDK clients, or None.
        git: Runs one read-only git command.
        adapter_factory: Replaces the built-in adapter construction when set.

    """

    root: Callable[[], Path] = Path.cwd
    clock: Callable[[], datetime] = _utc_now
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    http_client: Callable[[], httpx2.AsyncClient | None] = _no_http_client
    git: GitRunner = audit._run_git
    adapter_factory: AdapterFactory | None = None


def recorded_declines(run_dir: Path) -> list[DeclinedAttempt] | None:
    """Return the attempts a recorded run declined, from its manifest.

    Args:
        run_dir: The run folder.

    Returns:
        The declined attempts; None when the manifest predates them or cannot be read, so the
        replay re-derives breaker and budget refusals as before.

    Raises:
        WiringError: A declined record is malformed.

    """
    try:
        records = read_manifest(run_dir).get(DECLINED_FIELD)
    except WiringError:
        return None
    if not isinstance(records, list):
        return None
    try:
        return [DeclinedAttempt.from_json(record) for record in records]
    except (KeyError, TypeError, ValueError) as error:
        raise WiringError(f"{run_dir}: unreadable {DECLINED_FIELD} ({error})") from error


def read_manifest(run_dir: Path) -> dict[str, Any]:
    """Read a run folder's ``manifest.json``.

    Args:
        run_dir: The run folder.

    Returns:
        The manifest.

    Raises:
        WiringError: The manifest is missing or unreadable.

    """
    path = run_dir / MANIFEST_FILE
    try:
        manifest: dict[str, Any] = json.loads(path.read_text(encoding=TEXT_ENCODING))
    except (OSError, ValueError) as error:
        raise WiringError(f"{path}: cannot read the run manifest ({error})") from error
    return manifest


def priced_model(provider: str, model: str, pricing: PricingTable) -> str:
    """Resolve a model name to the one priced snapshot it names, e.g. ``gpt-4o-mini``.

    Args:
        provider: Provider key, e.g. ``openai``.
        model: An exact snapshot id, or a name that prefixes exactly one priced snapshot.
        pricing: The dated price table.

    Returns:
        The priced snapshot id.

    Raises:
        WiringError: No priced snapshot, or more than one, matches.

    """
    models = pricing.providers.get(provider, {})
    if model in models:
        return model
    matches = sorted(name for name in models if name.startswith(model + SNAPSHOT_SEPARATOR))
    if len(matches) != 1:
        raise WiringError(f"no single priced {provider} snapshot for {model!r} in {PRICING_FILE}")
    return matches[0]


def _pinned_for(kind: LLMKind, models: PinnedModels, pricing: PricingTable) -> str:
    """Return the effective primary or fallback model served by a provider."""
    for model in (models.primary, models.fallback):
        if provider_for(model, pricing) == kind.value:
            return model
    raise WiringError(f"no pinned model is served by {kind.value}")


def resolve_spec_model(
    spec: LLMSpec, settings: Settings, pricing: PricingTable, models_config: ModelsConfig
) -> str:
    """Return the model a run requests under a spec.

    Args:
        spec: The parsed ``--llm``.
        settings: The effective settings.
        pricing: The dated price table.
        models_config: The loaded ``models.toml``.

    Returns:
        A live spec's priced model (the provider's pinned model when none is named), the
        replayed run's requested model, or the effective primary for ``fake``.

    Raises:
        WiringError: The model is unpriced, served by another provider, or a replay has no
            readable manifest.

    """
    if spec.kind == LLMKind.REPLAY and spec.run_dir is not None:
        return str(read_manifest(spec.run_dir)["requested_model"])
    models = resolve_models(settings, models_config)
    if spec.kind not in LIVE_KINDS:
        return models.primary
    if spec.model is None:
        return _pinned_for(spec.kind, models, pricing)
    model = priced_model(spec.kind.value, spec.model, pricing)
    if provider_for(model, pricing) != spec.kind.value:
        raise WiringError(f"model {model!r} is not served by {spec.kind.value}")
    return model


def api_key(settings: Settings, kind: LLMKind) -> str:
    """Return a provider's API key.

    Args:
        settings: The effective settings.
        kind: A live adapter kind.

    Returns:
        The key's value.

    Raises:
        WiringError: The key is not set; the message names the variable, never a value.

    """
    secret = settings.anthropic_api_key if kind == LLMKind.ANTHROPIC else settings.openai_api_key
    if secret is None:
        raise WiringError(
            f"{KEY_ENV[kind]} is not set: export it (or put it in .env), or run offline with "
            "--llm fake or --llm replay:<run_dir>"
        )
    return secret.get_secret_value()


def live_adapter(
    kind: LLMKind, model: str, settings: Settings, allowlist: Sequence[str], runtime: Runtime
) -> LLMPort:
    """Construct the Anthropic or OpenAI adapter; called only when that adapter is selected.

    Args:
        kind: ``anthropic`` or ``openai``.
        model: The model snapshot id.
        settings: The effective settings, holding the key.
        allowlist: Patterns from ``models.toml``.
        runtime: Supplies the injected HTTP client.

    Returns:
        The adapter.

    Raises:
        WiringError: The key is missing.
        ModelNotAllowedError: The model is not on the allowlist.

    """
    key = api_key(settings, kind)
    options = AdapterOptions(
        timeout_s=settings.per_call_timeout_s, http_client=runtime.http_client()
    )
    if kind == LLMKind.ANTHROPIC:
        return AnthropicLLM(model, allowlist, key, options)
    return OpenAIChatLLM(model, allowlist, key, options)


def build_adapter(
    spec: LLMSpec, model: str, settings: Settings, allowlist: Sequence[str], runtime: Runtime
) -> LLMPort:
    """Construct the adapter a spec selects.

    Args:
        spec: The parsed ``--llm``.
        model: The model from ``resolve_spec_model``.
        settings: The effective settings.
        allowlist: Patterns from ``models.toml``.
        runtime: May replace the construction with its ``adapter_factory``.

    Returns:
        FakeLLM, a non-strict ReplayLLM over the run's ``calls.jsonl`` (a miss becomes
        ``LLM_FAILURE:replay_miss``, never a live call), or a live adapter.

    Raises:
        WiringError: A live key is missing, or the replayed run has no readable calls.

    """
    if runtime.adapter_factory is not None:
        return runtime.adapter_factory(spec, model)
    if spec.kind == LLMKind.FAKE:
        return FakeLLM(model, allowlist)
    if spec.kind == LLMKind.REPLAY and spec.run_dir is not None:
        calls = spec.run_dir / CALLS_FILE
        try:
            recorded = RecordedRun.from_calls_jsonl(calls)
        except (OSError, ValueError, KeyError) as error:
            raise WiringError(f"{calls}: cannot read the recorded calls ({error})") from error
        declined = recorded_declines(spec.run_dir)
        return ReplayLLM(recorded, model, allowlist, strict=False, declined=declined)
    return live_adapter(spec.kind, model, settings, allowlist, runtime)


@dataclass(frozen=True)
class WrapperSetup:
    """Per-run inputs of the LLM wrapper.

    Attributes:
        cap_usd: The run's spend cap.
        cache: The live response cache, or None.
        run_id: Mixed into call ids.
        policy: Retry and budget limits; from settings when None.

    """

    cap_usd: float
    cache: ResponseCache | None = None
    run_id: str = ""
    policy: WrapperPolicy | None = None


def make_wrapper(
    adapter: LLMPort,
    pricing: PricingTable,
    settings: Settings,
    setup: WrapperSetup,
    runtime: Runtime,
) -> LLMWrapper:
    """Wrap an adapter with retries, the budget ledger, the breaker, recording and the cache.

    Args:
        adapter: The adapter behind the port.
        pricing: The dated price table.
        settings: The effective settings, for the §11.3 limits.
        setup: Cap, cache, run id and an optional policy.
        runtime: Supplies the clock and the sleep.

    Returns:
        The wrapper.

    """
    deps = WrapperDeps(
        cache=setup.cache,
        sleep=runtime.sleep,
        now=runtime.clock,
        call_id_namespace=setup.run_id,
    )
    policy = setup.policy or WrapperPolicy.from_settings(settings)
    return LLMWrapper(adapter, pricing, BudgetLedger(setup.cap_usd), policy, deps)


class CheckStatus(StrEnum):
    """Outcome of one doctor check."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Check:
    """One row of the doctor table.

    Attributes:
        name: The check's name.
        status: Pass, fail or skip.
        detail: One line for the table; never a secret.
        data: Extra values for the evidence file.

    """

    name: str
    status: CheckStatus
    detail: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Return the check as a JSON object."""
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class DoctorOptions:
    """What the doctor runs and where it writes.

    Attributes:
        live: Run the paid checks.
        evidence_dir: Where ``doctor_<date>.json`` goes.
        runs_dir: Where the doctor's run folders go.

    """

    live: bool = False
    evidence_dir: Path = DEFAULT_EVIDENCE_DIR
    runs_dir: Path = DEFAULT_RUNS_DIR


@dataclass(frozen=True)
class DoctorReport:
    """The doctor's result.

    Attributes:
        generated_at: When the report was made.
        live: Whether the paid checks ran.
        checks: Every check, in table order.
        evidence_path: Where the report was written.

    """

    generated_at: datetime
    live: bool
    checks: tuple[Check, ...]
    evidence_path: Path

    @property
    def passed(self) -> bool:
        """Whether no check failed."""
        return all(check.status != CheckStatus.FAIL for check in self.checks)

    def to_json(self) -> dict[str, Any]:
        """Return the report as the evidence file's JSON object."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "date": self.generated_at.date().isoformat(),
            "live": self.live,
            "passed": self.passed,
            "checks": [check.to_json() for check in self.checks],
        }


@dataclass(frozen=True)
class _Env:
    """The loaded config the checks read."""

    models: ModelsConfig
    pricing: PricingTable
    resolved: PinnedModels


def _load_env(settings: Settings) -> _Env:
    """Load ``models.toml`` and ``pricing.toml`` and check the whole config through the service."""
    ServiceResources.from_settings(settings)
    models = load_models_config(settings.config_file(MODELS_FILE))
    pricing = load_pricing(settings.config_file(PRICING_FILE))
    return _Env(models, pricing, resolve_models(settings, models))


def _model_checks(env: _Env) -> list[Check]:
    """Check the allowlist, the pinned model and the prices."""
    resolved, pinned = env.resolved, env.models.pinned
    patterns = list(env.models.allowlist.patterns)
    checks = [
        Check("allowlist", CheckStatus.PASS, ", ".join(patterns), {"patterns": patterns}),
        Check(
            "pinned_model",
            CheckStatus.PASS if resolved.primary == pinned.primary else CheckStatus.FAIL,
            f"primary {resolved.primary} (pinned {pinned.primary}), fallback {resolved.fallback}",
            {"primary": resolved.primary, "fallback": resolved.fallback},
        ),
    ]
    try:
        providers = {
            model: provider_for(model, env.pricing) for model in resolved.model_dump().values()
        }
    except ConfigError as error:
        return [*checks, Check("pricing", CheckStatus.FAIL, str(error))]
    detail = f"price_date {env.pricing.price_date.isoformat()}"
    return [*checks, Check("pricing", CheckStatus.PASS, detail, {"providers": providers})]


def _key_checks(settings: Settings) -> list[Check]:
    """Report which keys are present, never their values."""
    anthropic_set = settings.anthropic_api_key is not None
    openai_set = settings.openai_api_key is not None
    return [
        Check(
            "anthropic_key",
            CheckStatus.PASS if anthropic_set else CheckStatus.FAIL,
            f"{ANTHROPIC_KEY_ENV} {'set' if anthropic_set else 'not set'}",
        ),
        Check(
            "openai_key",
            CheckStatus.PASS if openai_set else CheckStatus.SKIP,
            f"{OPENAI_KEY_ENV} {'set' if openai_set else 'not set: no fallback adapter'}",
        ),
    ]


def _library_checks(settings: Settings) -> list[Check]:
    """Load every configured library."""
    checks: list[Check] = []
    for library_id in settings.libraries:
        name = f"library:{library_id}"
        try:
            library = load_catalogue(library_id, settings)
        except (OSError, ValueError, KeyError, ConfigError) as error:
            checks.append(Check(name, CheckStatus.FAIL, str(error)))
            continue
        detail = f"{len(library.rows)} rows, sha256 {library.sha256[:SHA_DISPLAY_LENGTH]}"
        data = {"sha256": library.sha256, "rows": len(library.rows)}
        checks.append(Check(name, CheckStatus.PASS, detail, data))
    return checks


def offline_checks(settings: Settings) -> tuple[list[Check], _Env | None]:
    """Run every check that needs no network.

    Args:
        settings: The effective settings.

    Returns:
        The checks, and the loaded config when it loads.

    """
    try:
        env = _load_env(settings)
    except (ConfigError, ValueError) as error:
        failed = Check("config", CheckStatus.FAIL, str(error))
        return [failed, *_key_checks(settings)], None
    config = Check("config", CheckStatus.PASS, f"{settings.config_dir.as_posix()} loads")
    checks = [config, *_model_checks(env), *_key_checks(settings), *_library_checks(settings)]
    return checks, env


class CapturingPort:
    """Passes calls through to an adapter and keeps every result, for the rate-limit headers."""

    def __init__(self, inner: LLMPort) -> None:
        """Wrap an adapter.

        Args:
            inner: The adapter.

        """
        self.inner = inner
        self.results: list[LLMResult] = []

    async def complete(self, req: LLMRequest) -> LLMResult:
        """Forward one call and keep its result.

        Args:
            req: The request.

        Returns:
            The adapter's result.

        """
        result = await self.inner.complete(req)
        self.results.append(result)
        return result

    def rate_limit_headers(self, *, successful_only: bool = False) -> dict[str, str]:
        """Return the rate-limit headers of the latest call that carried any, else {}.

        Args:
            successful_only: Consider only calls whose status is ``ok`` (a run's manifest);
                the doctor's tier check reads any response, a 429 included.

        Returns:
            The headers, or {} when no considered call carried them.

        """
        found = (
            dict(result.rate_limit_headers)
            for result in reversed(self.results)
            if not successful_only or result.status == LLMStatus.OK
        )
        return next((headers for headers in found if headers), {})


@dataclass(frozen=True)
class _Probe:
    """One synthetic call's run, or why it could not run."""

    kind: LLMKind
    result: RunResult | None = None
    results: tuple[LLMResult, ...] = ()
    folder: Path | None = None
    error: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)


def _synthetic_lines() -> tuple[BoqLine, ...]:
    """Return the two hard-coded synthetic item lines."""
    return tuple(line for line in read_boq(SYNTHETIC_BOQ).lines if line.kind == LineKind.ITEM)


def _probe_settings(settings: Settings, model: str) -> Settings:
    """Return settings for one call: one pass, no retries, the given model."""
    update = {"primary_model": model, "passes_k": DOCTOR_PASSES, "max_retries": DOCTOR_RETRIES}
    return settings.model_copy(update=update)


async def _probe(
    kind: LLMKind, settings: Settings, env: _Env, options: DoctorOptions, runtime: Runtime
) -> _Probe:
    """Send the two synthetic lines once through the real wrapper and record the run."""
    model = _pinned_for(kind, env.resolved, env.pricing)
    probe_settings = _probe_settings(settings, model)
    try:
        allowlist = env.models.allowlist.patterns
        capture = CapturingPort(live_adapter(kind, model, probe_settings, allowlist, runtime))
    except (WiringError, ValueError) as error:
        return _Probe(kind, error=str(error))
    run_id = DOCTOR_RUN_PREFIX + make_run_id(runtime.clock(), kind.value)
    setup = WrapperSetup(cap_usd=DOCTOR_BUDGET_USD, run_id=run_id)
    wrapper = make_wrapper(capture, env.pricing, probe_settings, setup, runtime)
    service = MatchService(ServiceResources.from_settings(probe_settings))
    result = await service.match(
        read_boq(SYNTHETIC_BOQ),
        next(iter(settings.libraries)),
        profile=RunProfile.B3,
        llm=wrapper,
        options=RunOptions(run_id=run_id),
    )
    context = _probe_context(probe_settings, env.pricing, capture, runtime)
    folder = write_run(result, options.runs_dir, build_manifest(result, context))
    headers = capture.rate_limit_headers()
    return _Probe(kind, result, tuple(capture.results), folder, headers=headers)


def _probe_context(
    settings: Settings, pricing: PricingTable, capture: CapturingPort, runtime: Runtime
) -> ManifestContext:
    """Return a doctor run's manifest context: the tier its last successful call implies."""
    root = runtime.root()
    headers = capture.rate_limit_headers(successful_only=True)
    return ManifestContext(
        root,
        settings,
        pricing,
        code_version(root, runtime.git),
        rate_limit_tier=infer_tier(headers),
        rate_limit_headers=headers,
        rate_limit_source=RATE_LIMIT_FROM_CALL if headers else RATE_LIMIT_NONE,
    )


NO_ANSWER_REASONS = frozenset({ReasonCode.LLM_UNAVAILABLE.value, ReasonCode.BUDGET_CAP.value})


def _is_answer(reason: str) -> bool:
    """Tell whether a line's reason means the model gave it a schema-valid answer."""
    return not reason.startswith(LLM_FAILURE_PREFIX) and reason not in NO_ANSWER_REASONS


def _answered(result: RunResult) -> bool:
    """Tell whether every synthetic line got a schema-valid answer.

    A raw response alone is not enough: since per-line validation (DESIGN.md §16 A58),
    a line whose answer failed the schema keeps its raw JSON and is ``LLM_FAILURE:malformed``.
    """
    lines = result.lines
    return bool(lines) and all(_is_answer(str(item.decision.reason)) for item in lines)


def _call_check(name: str, probe: _Probe) -> Check:
    """Turn a probe into its pass/fail row."""
    if probe.result is None:
        return Check(name, CheckStatus.FAIL, probe.error)
    reasons = sorted({item.decision.reason for item in probe.result.lines})
    folder = probe.folder.as_posix() if probe.folder else ""
    data = {"run_folder": folder, "reasons": reasons, "served_models": _served(probe)}
    if not _answered(probe.result):
        return Check(name, CheckStatus.FAIL, f"no valid answer: {', '.join(reasons)}", data)
    return Check(name, CheckStatus.PASS, f"2 synthetic lines answered; recorded in {folder}", data)


def _served(probe: _Probe) -> list[str]:
    """Return the distinct served models of a probe's calls."""
    return sorted({result.served_model for result in probe.results if result.served_model})


def _served_check(probe: _Probe, pinned: str) -> Check:
    """Check that the pinned model served the call."""
    served = _served(probe)
    ok = served == [pinned]
    detail = f"served {', '.join(served) or 'nothing'} (pinned {pinned})"
    return Check("pinned_model_served", CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


def infer_tier(headers: Mapping[str, str]) -> str | None:
    """Infer the Anthropic usage tier from the requests-per-minute limit header.

    Args:
        headers: The ``anthropic-ratelimit-*`` headers of one response.

    Returns:
        ``1`` to ``4``, a note for a limit above tier 4, or None when the limit is absent or
        matches no published tier.

    """
    try:
        limit = int(headers[REQUESTS_LIMIT_HEADER])
    except (KeyError, ValueError):
        return None
    if limit > max(TIER_BY_REQUESTS_LIMIT):
        return TIER_ABOVE_PUBLISHED
    return TIER_BY_REQUESTS_LIMIT.get(limit)


@dataclass(frozen=True)
class TierEvidence:
    """The account tier a doctor evidence file recorded.

    Attributes:
        tier: The tier, e.g. ``2``.
        path: The evidence file it came from.

    """

    tier: str
    path: Path


def _evidence_tier(path: Path) -> str | None:
    """Return the tier one evidence file's ``rate_limit_tier`` check recorded, else None."""
    try:
        report = json.loads(path.read_text(encoding=TEXT_ENCODING))
    except (OSError, ValueError):
        return None
    checks = report.get("checks") if isinstance(report, dict) else None
    for check in checks if isinstance(checks, list) else []:
        data = check.get("data") if isinstance(check, dict) else None
        if check.get("name") == RATE_LIMIT_CHECK and isinstance(data, dict) and data.get("tier"):
            return str(data["tier"])
    return None


def newest_tier_evidence(evidence_dir: Path) -> TierEvidence | None:
    """Return the tier of the newest ``doctor_<date>.json`` that recorded one (§11.3, A39).

    Args:
        evidence_dir: The evidence folder.

    Returns:
        The tier and its file, or None when no evidence file records a tier.

    """
    pattern = f"{EVIDENCE_PREFIX}*{EVIDENCE_SUFFIX}"
    for path in sorted(evidence_dir.glob(pattern), reverse=True):
        tier = _evidence_tier(path)
        if tier is not None:
            return TierEvidence(tier, path)
    return None


def live_context(context: ManifestContext, headers: Mapping[str, str]) -> ManifestContext:
    """Add what a run that reached a live model records: tier evidence and rate-limit headers.

    Args:
        context: The run's manifest context.
        headers: The rate-limit headers of its last successful live call, or {}.

    Returns:
        The context with the tier of the newest doctor evidence (``unknown`` without one) and
        the headers.

    """
    evidence = newest_tier_evidence(context.root / DEFAULT_EVIDENCE_DIR)
    return dataclasses.replace(
        context,
        rate_limit_tier=evidence.tier if evidence else TIER_UNKNOWN,
        rate_limit_tier_source=evidence.path if evidence else None,
        rate_limit_headers=headers,
        rate_limit_source=RATE_LIMIT_FROM_CALL if headers else RATE_LIMIT_NONE,
    )


def _rate_limit_check(probe: _Probe) -> Check:
    """Report the rate-limit headers and the tier they imply."""
    headers = dict(probe.headers)
    if not headers:
        return Check("rate_limit_tier", CheckStatus.FAIL, "no anthropic-ratelimit-* headers seen")
    tier = infer_tier(headers)
    status = CheckStatus.PASS if tier else CheckStatus.FAIL
    detail = f"tier {tier or TIER_UNKNOWN}"
    return Check("rate_limit_tier", status, detail, {"tier": tier, "headers": headers})


async def _count_rendering(
    client: anthropic.AsyncAnthropic, library: Library, variant: PromptVariant, model: str
) -> int:
    """Count one rendering's system blocks plus the 2-line synthetic user message.

    Raises:
        anthropic.AnthropicError: The count failed.

    """
    request = build_request(
        _synthetic_lines(), library, variant, model=model, max_tokens=DEFAULT_MAX_TOKENS
    )
    counted = await client.messages.count_tokens(
        model=model,
        system=system_param(request),
        messages=[MessageParam(role="user", content=request.user_payload)],
    )
    return counted.input_tokens


async def _count_library(
    client: anthropic.AsyncAnthropic, library_id: str, settings: Settings, model: str
) -> list[Check]:
    """Count every pre-registered rendering of one library with the SDK's ``count_tokens``.

    Returns:
        ``count_tokens:<id>`` (the canonical rendering and its cache eligibility) and
        ``rendered_tokens:<id>`` (every rendering by prompt version, with the library SHA and
        the model), which runs reuse as their measured size (§9.6, A28).

    """
    library = load_catalogue(library_id, settings)
    try:
        counts = {
            prompt_version(variant): await _count_rendering(client, library, variant, model)
            for variant in VARIANTS
        }
    except anthropic.AnthropicError as error:
        failed = f"{type(error).__name__}: {error}"
        return [Check(f"{name}:{library_id}", CheckStatus.FAIL, failed) for name in TOKEN_CHECKS]
    tokens = counts[prompt_version(CANONICAL_V1)]
    eligible = tokens >= CACHE_MIN_TOKENS
    detail = f"{tokens} tokens, cache-eligible {'yes' if eligible else 'no'}"
    data = {"library_sha256": library.sha256, "model": model, "by_prompt_version": counts}
    by_version = ", ".join(f"{version} {count}" for version, count in counts.items())
    return [
        Check(f"{COUNT_CHECK}:{library_id}", CheckStatus.PASS, detail, _eligibility(tokens)),
        Check(f"{RENDERED_CHECK}:{library_id}", CheckStatus.PASS, by_version, data),
    ]


def _eligibility(tokens: int) -> dict[str, Any]:
    """Return a count and whether it reaches the prompt cache's minimum."""
    return {"tokens": tokens, "cache_eligible": tokens >= CACHE_MIN_TOKENS}


@dataclass(frozen=True)
class RenderedTokens:
    """A doctor's measured size of each rendering of one library, for one model.

    Attributes:
        by_version: Prompt version -> counted input tokens.
        path: The evidence file it came from.

    """

    by_version: Mapping[str, int]
    path: Path


def _rendered_checks(path: Path) -> list[dict[str, Any]]:
    """Return the ``rendered_tokens:*`` check data of one evidence file; [] when unreadable."""
    try:
        report = json.loads(path.read_text(encoding=TEXT_ENCODING))
    except (OSError, ValueError):
        return []
    checks = report.get("checks") if isinstance(report, dict) else None
    return [
        check["data"]
        for check in (checks if isinstance(checks, list) else [])
        if isinstance(check, dict)
        and str(check.get("name", "")).startswith(f"{RENDERED_CHECK}:")
        and isinstance(check.get("data"), dict)
    ]


def newest_rendered_tokens(
    evidence_dir: Path, library_sha256: str, model: str, versions: Sequence[str]
) -> RenderedTokens | None:
    """Return the newest doctor measurement of every given rendering of a library and model.

    Args:
        evidence_dir: The evidence folder.
        library_sha256: The run's library SHA-256.
        model: The run's requested model.
        versions: The run's prompt versions; each must have been counted.

    Returns:
        The counts of those versions and their file, or None when no file has them all.

    """
    pattern = f"{EVIDENCE_PREFIX}*{EVIDENCE_SUFFIX}"
    for path in sorted(evidence_dir.glob(pattern), reverse=True):
        for data in _rendered_checks(path):
            counts = data.get("by_prompt_version")
            same = data.get("library_sha256") == library_sha256 and data.get("model") == model
            if same and isinstance(counts, dict) and all(v in counts for v in versions):
                return RenderedTokens({v: int(counts[v]) for v in versions}, path)
    return None


def with_measured_tokens(
    manifest: Mapping[str, Any], evidence_dir: Path, root: Path
) -> dict[str, Any]:
    """Replace a manifest's estimated library size with the doctor's ``count_tokens`` (§9.6).

    Args:
        manifest: A run's manifest; its library SHA, requested model and prompt versions key
            the lookup.
        evidence_dir: The evidence folder.
        root: The repository root, for the source path.

    Returns:
        The manifest with the measured size, per rendering, its cache eligibility and its
        source file; unchanged when no doctor evidence measured these renderings.

    """
    versions = [str(version) for version in manifest.get("prompt_version") or ()]
    found = newest_rendered_tokens(
        evidence_dir,
        str(manifest.get("library_sha256")),
        str(manifest.get("requested_model")),
        versions,
    )
    if not versions or found is None:
        return dict(manifest)
    tokens = max(found.by_version.values())
    measured = {
        **_eligibility(tokens),
        "by_variant": dict(found.by_version),
        "estimated": False,
        "source": audit.portable_path(found.path, root),
    }
    return {**manifest, "library_rendered_tokens": measured}


async def _token_checks(settings: Settings, env: _Env, runtime: Runtime) -> list[Check]:
    """Count the rendered prompt of every configured library."""
    model = _pinned_for(LLMKind.ANTHROPIC, env.resolved, env.pricing)
    try:
        key = api_key(settings, LLMKind.ANTHROPIC)
    except WiringError as error:
        return [Check("count_tokens", CheckStatus.FAIL, str(error))]
    client = anthropic.AsyncAnthropic(
        api_key=key,
        max_retries=DOCTOR_RETRIES,
        timeout=settings.per_call_timeout_s,
        http_client=runtime.http_client(),
    )
    checks: list[Check] = []
    for library_id in settings.libraries:
        checks += await _count_library(client, library_id, settings, model)
    return checks


async def _fallback_check(
    settings: Settings, env: _Env, options: DoctorOptions, runtime: Runtime
) -> Check:
    """Send the synthetic lines to the fallback adapter, only when its key is present."""
    if settings.openai_api_key is None:
        return Check("fallback_call", CheckStatus.SKIP, f"{OPENAI_KEY_ENV} not set")
    probe = await _probe(LLMKind.OPENAI, settings, env, options, runtime)
    return _call_check("fallback_call", probe)


async def live_checks(
    settings: Settings, env: _Env, options: DoctorOptions, runtime: Runtime
) -> list[Check]:
    """Run the paid checks: one primary call, count_tokens, rate limits and the fallback.

    Args:
        settings: The effective settings.
        env: The loaded config.
        options: Where the doctor run folders go.
        runtime: Clock, HTTP client and git runner.

    Returns:
        The live checks.

    """
    probe = await _probe(LLMKind.ANTHROPIC, settings, env, options, runtime)
    checks = [_call_check("primary_call", probe)]
    if probe.result is not None:
        checks += [_served_check(probe, env.models.pinned.primary), _rate_limit_check(probe)]
    checks += await _token_checks(settings, env, runtime)
    checks.append(await _fallback_check(settings, env, options, runtime))
    return checks


def evidence_path(evidence_dir: Path, moment: datetime) -> Path:
    """Return ``evidence_dir/doctor_<YYYY-MM-DD>.json`` for a moment.

    Args:
        evidence_dir: The evidence folder.
        moment: When the doctor ran.

    Returns:
        The file path.

    """
    stamp = moment.astimezone(UTC).date().isoformat()
    return evidence_dir / f"{EVIDENCE_PREFIX}{stamp}{EVIDENCE_SUFFIX}"


def write_evidence(report: DoctorReport) -> None:
    """Write the report as sorted, indented UTF-8 JSON.

    Args:
        report: The report.

    """
    report.evidence_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report.to_json(), sort_keys=True, indent=JSON_INDENT, ensure_ascii=False)
    report.evidence_path.write_bytes((text + JSON_NEWLINE).encode(TEXT_ENCODING))


def run_doctor(settings: Settings, options: DoctorOptions, runtime: Runtime) -> DoctorReport:
    """Run the doctor and write its evidence file.

    Args:
        settings: The effective settings.
        options: Live or offline, and the output folders.
        runtime: Clock, HTTP client and git runner.

    Returns:
        The report.

    """
    moment = runtime.clock()
    checks, env = offline_checks(settings)
    if options.live and env is not None:
        checks += asyncio.run(live_checks(settings, env, options, runtime))
    elif options.live:
        checks.append(Check("live", CheckStatus.FAIL, "config does not load; live checks skipped"))
    report = DoctorReport(
        moment, options.live, tuple(checks), evidence_path(options.evidence_dir, moment)
    )
    write_evidence(report)
    return report


def render_table(report: DoctorReport) -> str:
    """Render the one-screen pass/fail table.

    Args:
        report: The report.

    Returns:
        One row per check, then the overall verdict and the evidence path.

    """
    width = max((len(check.name) for check in report.checks), default=0)
    rows = [
        f"{check.status.value.upper():<4}  {check.name:<{width}}  {check.detail}"
        for check in report.checks
    ]
    verdict = "PASS" if report.passed else "FAIL"
    mode = "live" if report.live else "offline"
    rows.append(f"doctor ({mode}): {verdict}; evidence {report.evidence_path.as_posix()}")
    return JSON_NEWLINE.join(rows)
