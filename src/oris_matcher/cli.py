"""Command-line entry point: ``match`` (the default), ``score``, ``replay`` and ``doctor``.

``oris --input X --library Y --output Z`` is the brief's literal form of ``oris match``. A run
reads the BoQ, maps the library path to its ``Settings.libraries`` id (``custom`` otherwise),
calls ``MatchService``, writes ``runs/<run_id>/`` and the output CSV, and prints the §11.6
summary. Exit codes: 0 ok, 1 a ``replay --check`` mismatch or a failed doctor check, 2 a usage
or input error, 3 when any line is ``LLM_UNAVAILABLE`` or a replay miss (§11.3).
"""

import asyncio
import dataclasses
import hashlib
import io
import json
import logging
import math
import re
import secrets
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from typer import _click
from typer.core import TyperGroup

from oris_matcher.doctor import (
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_RUNS_DIR,
    LIVE_KINDS,
    RATE_LIMIT_FROM_CALL,
    RATE_LIMIT_NONE,
    TIER_UNKNOWN,
    CapturingPort,
    DoctorOptions,
    LLMKind,
    LLMSpec,
    Runtime,
    WiringError,
    WrapperSetup,
    build_adapter,
    make_wrapper,
    newest_tier_evidence,
    parse_llm_spec,
    read_manifest,
    render_table,
    resolve_spec_model,
    run_doctor,
)
from oris_matcher.domain.boq import BoqFile
from oris_matcher.io.audit import (
    AUDIT_FILE,
    CALLS_FILE,
    EXTERNAL_DIR,
    EXTERNAL_PATH_KEY,
    ManifestContext,
    build_manifest,
    code_version,
    is_committable,
    is_external,
    portable_path,
    repo_relative,
    require_inside_root,
    write_run,
)
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.io.writer import render_csv
from oris_matcher.llm.base import LLMPort, canonical_json
from oris_matcher.llm.recording import CallRecord, ReasonForCall, read_calls_jsonl
from oris_matcher.llm.replay_llm import ResponseCache
from oris_matcher.service import (
    EXIT_OK,
    MatchService,
    RunOptions,
    RunProfile,
    RunResult,
    ServiceResources,
    budget_cap_usd,
    make_run_id,
)
from oris_matcher.settings import (
    MODELS_FILE,
    PRICING_FILE,
    ConfigError,
    PricingTable,
    Settings,
    load_models_config,
    load_policy,
    load_pricing,
    resolve_models,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_COMMAND = "match"
HELP_FLAGS = frozenset({"--help", "-h"})
EXIT_FAILED_CHECK = 1
EXIT_USAGE = 2
STDOUT_ENCODING = "utf-8"
STDOUT_ERRORS = "replace"
TEXT_ENCODING = "utf-8"
CUSTOM_LIBRARY_ID = "custom"
OUTPUT_FILE = "output.csv"
SCORER_PATH = Path("eval") / "score.py"
RUN_NONCE_BYTES = 8
LINES_PER_COST_UNIT = 100
PERCENT = 100.0
P50 = 50
P95 = 95
SHARE_DIGITS = 1
COST_DIGITS = 6
USAGE_ERRORS = (WiringError, ConfigError, OSError, ValueError, KeyError)
LIVE_MODE = "live"
INPUT_ROLE = "input"
POLICY_ROLE = "policy"
LIBRARY_ROLE = "library"
FREEZE_TAG = "eval-freeze"
GIT_TAGS_AT_HEAD = ("git", "tag", "--points-at", "HEAD")
SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
EXERCISE_INPUTS_SHA256 = frozenset(
    {
        "ea19f4791cbb048d07bc3521fb806159a87560569099a5386d6208725a5c1bbc",
        "626003d156ea1de14ae8675eb79111aa6e034c46d1d8811019e6ee251d98383c",
    }
)
LLM_HELP = (
    "anthropic:<model> (default: the pinned primary), openai:<model> (e.g. gpt-4o-mini), "
    "fake, or replay:<run_dir>"
)

Selector = Callable[[BoqFile], Collection[str]]


class _DefaultCommandGroup(TyperGroup):
    """Routes an invocation that names no command to ``match``, the brief's literal form."""

    def parse_args(self, ctx: _click.Context, args: list[str]) -> list[str]:
        """Prefix ``match`` when the first argument is not a command or a help flag.

        Args:
            ctx: The click context.
            args: The raw arguments.

        Returns:
            The remaining arguments, as click's group parser returns them.

        """
        if args and args[0] not in self.commands and args[0] not in HELP_FLAGS:
            args = [DEFAULT_COMMAND, *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=_DefaultCommandGroup,
    help="Match BoQ lines to an ORIS material library.",
    no_args_is_help=True,
    add_completion=False,
)

RUNTIME = Runtime()


def _runtime() -> Runtime:
    """Return the module's runtime, which tests replace."""
    return RUNTIME


def configure_stdout(stream: object | None = None) -> None:
    """Print UTF-8 whatever the console code page is, replacing what cannot be encoded.

    Args:
        stream: The stream to reconfigure; ``sys.stdout`` when None.

    """
    target = sys.stdout if stream is None else stream
    if isinstance(target, io.TextIOWrapper):
        target.reconfigure(encoding=STDOUT_ENCODING, errors=STDOUT_ERRORS)


@app.callback()
def main() -> None:
    """Match BoQ lines to an ORIS material library."""
    configure_stdout()


def _fail(error: BaseException) -> NoReturn:
    """Report a usage or input error and exit with code 2."""
    typer.echo(f"error: {error}", err=True)
    raise typer.Exit(EXIT_USAGE)


@dataclass(frozen=True)
class MatchJob:
    """One match run's inputs.

    Attributes:
        input_path: The BoQ CSV.
        library_path: The library CSV, mapped to its configured id or ``custom``.
        llm: The parsed ``--llm``.
        output_path: The output CSV; ``runs/<run_id>/output.csv`` when None.
        profile: B2 or B3.
        policy_path: A ``policy.yaml`` replacing ``config/policy.yaml``, recorded as an override.
        use_cache: Serve repeated requests from earlier live runs' ``calls.jsonl``.
        runs_dir: Where the run folder goes.
        excel_bom: Prefix the output CSV with a UTF-8 BOM.
        selector: Picks the line ids to route and output, from the whole parsed file.
        split_sha256: Recorded in the manifest; src never reads the split file.
        budget_usd: A hard cap below the §11.3 per-100-lines cap.

    """

    input_path: Path
    library_path: Path
    llm: LLMSpec
    output_path: Path | None = None
    profile: RunProfile = RunProfile.B3
    policy_path: Path | None = None
    use_cache: bool = True
    runs_dir: Path = DEFAULT_RUNS_DIR
    excel_bom: bool = False
    selector: Selector | None = None
    split_sha256: str | None = None
    budget_usd: float | None = None


@dataclass(frozen=True)
class PreparedRun:
    """Everything a run needs before its first call.

    Attributes:
        settings: The effective settings, with the custom library and the requested model.
        library_id: The configured library id, or ``custom``.
        pricing: The dated price table.
        service: The service, with any ``--policy`` override applied.
        adapter: The adapter behind the port; a live one is wrapped in a ``CapturingPort``.
        boq: The parsed input.
        input_sha256: SHA-256 of the input bytes.
        spec: The effective ``--llm``, after a missing primary key moved it to the fallback.
        fallback: The adapter that takes over once the breaker trips, or None.

    """

    settings: Settings
    library_id: str
    pricing: PricingTable
    service: MatchService
    adapter: LLMPort
    boq: BoqFile
    input_sha256: str
    spec: LLMSpec
    fallback: LLMPort | None = None

    @property
    def rate_limit_headers(self) -> dict[str, str]:
        """The rate-limit headers of the live adapter's last successful call, else {}."""
        if isinstance(self.adapter, CapturingPort):
            return self.adapter.rate_limit_headers(successful_only=True)
        return {}


@dataclass(frozen=True)
class MatchOutcome:
    """A finished run and where it was written.

    Attributes:
        result: The run.
        folder: ``runs/<run_id>/``.
        output_path: The output CSV.
        manifest: The full manifest.

    """

    result: RunResult
    folder: Path
    output_path: Path
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class CacheIndex:
    """The live response cache and where each cached response was recorded.

    Attributes:
        cache: The cache, or None when no earlier live attempt was recorded.
        sources: Call id -> the run id whose folder recorded it.

    """

    cache: ResponseCache | None = None
    sources: Mapping[str, str] = dataclasses.field(default_factory=dict)


def _resolved(path: Path, root: Path) -> Path:
    """Resolve a path against the repository root."""
    return (path if path.is_absolute() else root / path).resolve()


def settings_for_library(base: Settings, library_path: Path, root: Path) -> tuple[Settings, str]:
    """Map a library path to its ``Settings.libraries`` id, adding it as ``custom`` otherwise.

    Args:
        base: The settings.
        library_path: The ``--library`` path.
        root: The repository root, against which relative paths resolve.

    Returns:
        The settings (with ``custom`` added when needed) and the library id.

    Raises:
        WiringError: The library file does not exist.

    """
    target = _resolved(library_path, root)
    if not target.is_file():
        raise WiringError(f"library file not found: {library_path}")
    for library_id, configured in base.libraries.items():
        if _resolved(configured, root) == target:
            return base, library_id
    libraries = {**base.libraries, CUSTOM_LIBRARY_ID: library_path}
    return base.model_copy(update={"libraries": libraries}), CUSTOM_LIBRARY_ID


def _service(settings: Settings, policy_path: Path | None) -> MatchService:
    """Build the service; a ``--policy`` file replaces ``policy.yaml`` and is an override."""
    resources = ServiceResources.from_settings(settings)
    if policy_path is not None:
        policy = load_policy(policy_path)
        resources = dataclasses.replace(resources, policy=policy, policy_override=True)
    return MatchService(resources)


def effective_spec(spec: LLMSpec, settings: Settings) -> LLMSpec:
    """Move an Anthropic run to the fallback adapter when only ``OPENAI_API_KEY`` is set (§12).

    Args:
        spec: The parsed ``--llm``.
        settings: The effective settings, holding the keys.

    Returns:
        ``openai`` (its pinned model) when the Anthropic key is missing and the OpenAI key is
        present, else the spec unchanged.

    """
    if spec.kind != LLMKind.ANTHROPIC or settings.anthropic_api_key is not None:
        return spec
    if settings.openai_api_key is None:
        return spec
    LOGGER.warning("%s is not set: running on the fallback adapter (openai)", "ANTHROPIC_API_KEY")
    return LLMSpec(LLMKind.OPENAI)


def require_committable_inputs(job: MatchJob, root: Path) -> None:
    """Refuse host paths in a run that lands under ``runs/submission/`` (§9.6).

    Args:
        job: The run's inputs.
        root: The repository root.

    Raises:
        ValueError: The run folder is committable and an input, library, policy or replayed
            run lies outside the repository.

    """
    if not is_committable(job.runs_dir, root):
        return
    paths = [job.input_path, job.library_path, job.policy_path, job.llm.run_dir]
    require_inside_root([path for path in paths if path is not None], root)


def check_exercise_input(job: MatchJob, spec: LLMSpec, input_sha256: str, runtime: Runtime) -> None:
    """Refuse a live run over a whole exercise input before the freeze (§10.3).

    The exercise inputs hold the lockbox items; before ``eval-freeze`` only the experiment
    runner's dev selection may reach a live model. Offline and replayed runs are allowed.

    Args:
        job: The run's inputs; a run with a line selector is the runner's dev selection.
        spec: The effective ``--llm``.
        input_sha256: SHA-256 of the input bytes.
        runtime: Runs the read-only ``git tag --points-at HEAD``.

    Raises:
        WiringError: A live spec over a whole exercise input while the tag is not at HEAD.

    """
    if spec.kind not in LIVE_KINDS or job.selector is not None:
        return
    if input_sha256 not in EXERCISE_INPUTS_SHA256:
        return
    try:
        tags = runtime.git(GIT_TAGS_AT_HEAD, runtime.root()).split()
    except (OSError, subprocess.SubprocessError):
        tags = []
    if FREEZE_TAG not in tags:
        raise WiringError(
            "this input is an exercise file holding lockbox items: a live run over the whole "
            f"file is refused until the {FREEZE_TAG} tag is at HEAD; use "
            "eval/run_experiment.py --side dev, or --llm fake / replay:<run_dir>"
        )


def fallback_adapter(
    spec: LLMSpec, settings: Settings, allowlist: Sequence[str], runtime: Runtime
) -> LLMPort | None:
    """Build the adapter that takes over once the breaker trips (§11.3, A33).

    Args:
        spec: The effective ``--llm``.
        settings: The effective settings, holding the fallback model and the keys.
        allowlist: Patterns from ``models.toml``.
        runtime: Supplies the adapter factory and the HTTP client.

    Returns:
        The OpenAI fallback for an Anthropic run when ``OPENAI_API_KEY`` is set, else None.

    """
    if spec.kind != LLMKind.ANTHROPIC or settings.openai_api_key is None:
        return None
    models = load_models_config(settings.config_file(MODELS_FILE))
    model = resolve_models(settings, models).fallback
    return build_adapter(LLMSpec(LLMKind.OPENAI, model=model), model, settings, allowlist, runtime)


def _replay_fallback(spec: LLMSpec, adapter: LLMPort) -> LLMPort | None:
    """Return the replay adapter as the fallback when the replayed run used one."""
    if spec.kind != LLMKind.REPLAY or spec.run_dir is None:
        return None
    return adapter if read_manifest(spec.run_dir).get("fallback_engaged") else None


def prepare_run(job: MatchJob, base: Settings, runtime: Runtime) -> PreparedRun:
    """Read the input and build the settings, service and adapters of a run.

    Args:
        job: The run's inputs.
        base: The settings before the library and model are applied.
        runtime: The environment.

    Returns:
        The prepared run.

    Raises:
        WiringError: Bad ``--llm``, a missing key, a missing library file, or a live run over
            an exercise input before the freeze.
        ConfigError: A config file is invalid or a model is not allowed.
        BoqFormatError: The input cannot be read as a BoQ.
        OSError: The input cannot be read.
        ValueError: A committable run reads a file outside the repository.

    """
    require_committable_inputs(job, runtime.root())
    data = job.input_path.read_bytes()
    boq = read_boq(data)
    settings, library_id = settings_for_library(base, job.library_path, runtime.root())
    spec = effective_spec(job.llm, settings)
    digest = hashlib.sha256(data).hexdigest()
    check_exercise_input(job, spec, digest, runtime)
    models_config = load_models_config(settings.config_file(MODELS_FILE))
    pricing = load_pricing(settings.config_file(PRICING_FILE))
    model = resolve_spec_model(spec, settings, pricing, models_config)
    settings = settings.model_copy(update={"primary_model": model})
    allowlist = models_config.allowlist.patterns
    adapter = build_adapter(spec, model, settings, allowlist, runtime)
    if spec.kind in LIVE_KINDS:
        adapter = CapturingPort(adapter)
    fallback = fallback_adapter(spec, settings, allowlist, runtime)
    service = _service(settings, job.policy_path)
    fallback = fallback or _replay_fallback(spec, adapter)
    return PreparedRun(settings, library_id, pricing, service, adapter, boq, digest, spec, fallback)


@dataclass(frozen=True)
class CacheKey:
    """Whose answers a run may be served from the cache: one live provider and model.

    Attributes:
        provider: The live adapter kind, ``anthropic`` or ``openai``.
        model: The model the run requests.

    """

    provider: str
    model: str

    def admits_run(self, folder: Path) -> bool:
        """Tell whether a run folder may seed the cache: a cold live run of this provider+model.

        Args:
            folder: A run folder.

        Returns:
            True only when its manifest's mode is ``live`` (never fake, replay or cached), its
            adapter kind is this provider and it requested this model.

        """
        try:
            manifest = read_manifest(folder)
            kind = parse_llm_spec(str(manifest.get("llm") or "")).kind
        except WiringError:
            return False
        return (
            manifest.get("mode") == LIVE_MODE
            and kind.value == self.provider
            and manifest.get("requested_model") == self.model
        )

    def admits_record(self, record: CallRecord) -> bool:
        """Tell whether one original attempt was answered by this provider and model."""
        return (
            not record.cache_hit
            and record.provider_name == self.provider
            and record.response_model == self.model
        )


def load_cache(runs_dir: Path, key: CacheKey) -> CacheIndex:
    """Build the live response cache from earlier cold live runs of the same provider and model.

    Only run folders whose manifest mode is ``live`` and whose adapter and requested model
    equal the current ones feed the cache, and within them only attempts the same provider and
    served model answered, so a FakeLLM, replayed or other model's answer is never served.

    Args:
        runs_dir: The ``runs/`` folder.
        key: The current adapter's provider and model.

    Returns:
        The cache and the run id of each cached call.

    """
    records: list[CallRecord] = []
    sources: dict[str, str] = {}
    for path in sorted(runs_dir.glob(f"*/{CALLS_FILE}")):
        if not key.admits_run(path.parent):
            continue
        try:
            fresh = [record for record in read_calls_jsonl(path) if key.admits_record(record)]
        except (OSError, ValueError, KeyError) as error:
            LOGGER.warning("cache: skipping %s (%s)", path, error)
            continue
        records += fresh
        sources.update((record.call_id, path.parent.name) for record in fresh)
    if not records:
        return CacheIndex()
    return CacheIndex(ResponseCache.from_records(records), sources)


def _cache_index(job: MatchJob, prepared: PreparedRun) -> CacheIndex:
    """Return the run's cache: empty for ``--no-cache`` and for every fake or replayed run."""
    kind = prepared.spec.kind
    if not job.use_cache or kind not in LIVE_KINDS:
        return CacheIndex()
    key = CacheKey(kind.value, prepared.service.resources.requested_model)
    return load_cache(job.runs_dir, key)


def _cap(job: MatchJob, settings: Settings, line_count: int) -> float:
    """Return the run's spend cap: the floored §11.3 cap, lowered by the job's hard budget."""
    cap = budget_cap_usd(settings, line_count)
    return cap if job.budget_usd is None else min(cap, job.budget_usd)


def _source_run_id(spec: LLMSpec) -> str | None:
    """Return the replayed run's id for a ``replay:`` spec."""
    if spec.kind != LLMKind.REPLAY or spec.run_dir is None:
        return None
    return str(read_manifest(spec.run_dir).get("run_id") or "") or None


def run_match(job: MatchJob, prepared: PreparedRun, runtime: Runtime) -> tuple[RunResult, float]:
    """Run the service over a prepared input.

    Args:
        job: The run's inputs.
        prepared: The prepared run.
        runtime: The environment.

    Returns:
        The run and its spend cap.

    """
    select = job.selector(prepared.boq) if job.selector else None
    line_count = len(prepared.boq.lines) if select is None else len(select)
    nonce = secrets.token_hex(RUN_NONCE_BYTES)
    seed = canonical_json([prepared.input_sha256, prepared.library_id, job.llm.text, nonce])
    run_id = make_run_id(runtime.clock(), seed)
    cap = _cap(job, prepared.settings, line_count)
    index = _cache_index(job, prepared)
    setup = WrapperSetup(cap, index.cache, run_id)
    wrapper = make_wrapper(prepared.adapter, prepared.pricing, prepared.settings, setup, runtime)
    options = RunOptions(
        select=select,
        run_id=run_id,
        source_run_id=_source_run_id(prepared.spec),
        cache_sources=index.sources,
        fallback=prepared.fallback,
    )
    coroutine = prepared.service.match(
        prepared.boq, prepared.library_id, profile=job.profile, llm=wrapper, options=options
    )
    return asyncio.run(coroutine), cap


def llm_text(spec: LLMSpec, root: Path) -> str:
    """Return ``--llm`` as recorded in the manifest, a replayed folder repo-relative.

    Args:
        spec: The effective spec.
        root: The repository root.

    Returns:
        The spec text, e.g. ``anthropic`` or ``replay:runs/<run_id>``.

    """
    if spec.kind == LLMKind.REPLAY and spec.run_dir is not None:
        return f"{LLMKind.REPLAY.value}:{repo_relative(spec.run_dir, root)}"
    return spec.text


def _context(job: MatchJob, prepared: PreparedRun, runtime: Runtime) -> ManifestContext:
    """Return the environment fields of a run's manifest.

    A live run records the headers of its last successful call and the tier of the newest
    doctor evidence (``unknown`` without one); a fake or replayed run records neither.
    """
    root = runtime.root()
    context = ManifestContext(
        root=root,
        settings=prepared.settings,
        pricing=prepared.pricing,
        code=code_version(root, runtime.git),
        split_sha256=job.split_sha256,
        excel_bom=job.excel_bom,
        input_path=job.input_path,
    )
    if prepared.spec.kind not in LIVE_KINDS:
        return context
    headers = prepared.rate_limit_headers
    evidence = newest_tier_evidence(root / DEFAULT_EVIDENCE_DIR)
    return dataclasses.replace(
        context,
        rate_limit_tier=evidence.tier if evidence else TIER_UNKNOWN,
        rate_limit_tier_source=evidence.path if evidence else None,
        rate_limit_headers=headers,
        rate_limit_source=RATE_LIMIT_FROM_CALL if headers else RATE_LIMIT_NONE,
    )


def _extra_fields(job: MatchJob, prepared: PreparedRun, cap: float, root: Path) -> dict[str, Any]:
    """Return the manifest fields only the CLI knows: adapter, cap, input and policy file."""
    policy, replayed = job.policy_path, prepared.spec.run_dir
    return {
        "llm": llm_text(prepared.spec, root),
        "llm_run_dir": portable_path(replayed, root) if replayed else None,
        "llm_kind": prepared.spec.kind.value,
        "fallback_configured": prepared.fallback is not None,
        "budget_cap_usd": cap,
        "input_sha256": prepared.input_sha256,
        "policy_path": portable_path(policy, root) if policy else None,
        "policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest() if policy else None,
    }


def external_copy(folder: Path, role: str, name: str) -> Path:
    """Return where a run folder keeps its copy of an input that lies outside the repository.

    Args:
        folder: The run folder.
        role: ``input``, ``policy`` or ``library``.
        name: The file name recorded in the manifest.

    Returns:
        ``<folder>/external/<role>/<name>``.

    """
    return folder / EXTERNAL_DIR / role / name


def keep_external_inputs(job: MatchJob, folder: Path, root: Path) -> None:
    """Copy an input, library or policy file outside the repository into the run folder.

    The manifest records such a file by its name only (§9.6), so the copy is what a replay
    reads when the environment has no such file. Committable runs never get here with an
    outside file (``require_committable_inputs``).

    Args:
        job: The run's inputs.
        folder: The run folder.
        root: The repository root.

    """
    roles = (
        (INPUT_ROLE, job.input_path),
        (LIBRARY_ROLE, job.library_path),
        (POLICY_ROLE, job.policy_path),
    )
    for role, path in roles:
        if path is None or not is_external(portable_path(path, root)):
            continue
        copy = external_copy(folder, role, path.name)
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(path.read_bytes())


def execute_match(job: MatchJob, base: Settings, runtime: Runtime) -> MatchOutcome:
    """Run a match and write its run folder and output CSV.

    Args:
        job: The run's inputs.
        base: The settings.
        runtime: The environment.

    Returns:
        The outcome.

    """
    prepared = prepare_run(job, base, runtime)
    result, cap = run_match(job, prepared, runtime)
    extra = _extra_fields(job, prepared, cap, runtime.root())
    manifest = {**build_manifest(result, _context(job, prepared, runtime)), **extra}
    folder = write_run(result, job.runs_dir, manifest)
    keep_external_inputs(job, folder, runtime.root())
    output_path = job.output_path or folder / OUTPUT_FILE
    output_path.write_bytes(render_csv(result, excel_bom=job.excel_bom))
    return MatchOutcome(result, folder, output_path, manifest)


def percentile(values: Sequence[int], percent: int) -> int | None:
    """Return a nearest-rank percentile.

    Args:
        values: The values.
        percent: 0 to 100.

    Returns:
        The percentile, or None for no values.

    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(percent / PERCENT * len(ordered)), 1)
    return ordered[rank - 1]


def run_summary(result: RunResult) -> dict[str, Any]:
    """Collect the §11.6 run summary.

    Args:
        result: The run.

    Returns:
        Decision counts and shares, the reason histogram, retries, cache hits and tokens, the
        policy resolution, p50/p95 attributed latency of the routed lines and $ per 100 lines.

    """
    manifest = result.manifest
    lines = len(result.lines)
    latencies = [item.latency_ms for item in result.lines if item.call_ids]
    retries = sum(record.reason_for_call == ReasonForCall.RETRY for record in result.calls)
    per_100 = result.attributed_cost_usd * LINES_PER_COST_UNIT / lines if lines else 0.0
    return {
        "run_id": result.run_id,
        "mode": manifest["mode"],
        "profile": result.profile.value,
        "library_id": result.library_id,
        "lines": lines,
        "decision_counts": dict(manifest["decision_counts"]),
        "reason_counts": dict(manifest["reason_counts"]),
        "calls": len(result.calls),
        "retries": retries,
        "cache_hits": manifest["cache_hits"],
        "cache_read_tokens": manifest["cache_read_tokens"],
        "cache_write_tokens": manifest["cache_write_tokens"],
        "policy_resolution": result.policy.resolution.value,
        "policy_id": result.policy.policy_id,
        "latency_p50_ms": percentile(latencies, P50),
        "latency_p95_ms": percentile(latencies, P95),
        "spend_usd": manifest["spend_usd"],
        "attributed_cost_usd": result.attributed_cost_usd,
        "usd_per_100_lines": per_100,
        "exit_code": result.exit_code,
    }


def _shares(counts: Mapping[str, int], total: int) -> str:
    """Render counts with their shares of a total."""
    parts = (
        f"{name} {count} ({count * PERCENT / total:.{SHARE_DIGITS}f}%)"
        for name, count in counts.items()
    )
    return " | ".join(parts) if total else "none"


def render_summary(summary: Mapping[str, Any]) -> str:
    """Render the run summary for stdout.

    Args:
        summary: From ``run_summary``.

    Returns:
        The summary text.

    """
    histogram = Counter(summary["reason_counts"]).most_common()
    p50, p95 = summary["latency_p50_ms"], summary["latency_p95_ms"]
    return "\n".join(
        [
            f"run {summary['run_id']}: mode {summary['mode']}, profile {summary['profile']}, "
            f"library {summary['library_id']}, {summary['lines']} lines",
            f"policy_resolution: {summary['policy_resolution']} (policy {summary['policy_id']})",
            f"decisions: {_shares(summary['decision_counts'], summary['lines'])}",
            "reasons: " + " | ".join(f"{name} {count}" for name, count in histogram),
            f"calls: {summary['calls']}, retries {summary['retries']}, cache hits "
            f"{summary['cache_hits']}, cache read/write tokens "
            f"{summary['cache_read_tokens']}/{summary['cache_write_tokens']}",
            f"latency per routed line: p50 {p50 if p50 is not None else 'n/a'} ms, "
            f"p95 {p95 if p95 is not None else 'n/a'} ms",
            f"cost: ${summary['attributed_cost_usd']:.{COST_DIGITS}f} attributed, "
            f"${summary['spend_usd']:.{COST_DIGITS}f} spent, "
            f"${summary['usd_per_100_lines']:.{COST_DIGITS}f} per 100 lines",
        ]
    )


def _split_sha256(text: str | None) -> str | None:
    """Check ``--split-sha256``: 64 lowercase hex characters, or absent."""
    if text is None:
        return None
    if SHA256_HEX_RE.fullmatch(text) is None:
        raise ValueError(f"--split-sha256 {text!r}: expected 64 lowercase hex characters")
    return text


def _parse_llm(text: str | None) -> LLMSpec:
    """Parse ``--llm``; None means the pinned Anthropic primary."""
    return parse_llm_spec(text) if text else LLMSpec(LLMKind.ANTHROPIC)


@app.command("match")
def match_command(  # noqa: PLR0913, PLR0917
    input_path: Annotated[Path, typer.Option("--input", help="BoQ CSV to match.")],
    library: Annotated[Path, typer.Option("--library", help="ORIS library CSV.")],
    output: Annotated[Path, typer.Option("--output", help="Output CSV to write.")],
    llm: Annotated[str | None, typer.Option("--llm", help=LLM_HELP)] = None,
    profile: Annotated[RunProfile, typer.Option("--profile")] = RunProfile.B3,
    policy: Annotated[Path | None, typer.Option("--policy", help="policy.yaml override.")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Never serve from cache.")] = False,
    run_dir: Annotated[Path, typer.Option("--run-dir")] = DEFAULT_RUNS_DIR,
    excel_bom: Annotated[bool, typer.Option("--excel-bom", help="UTF-8 BOM for Excel.")] = False,
    split_sha256: Annotated[
        str | None, typer.Option("--split-sha256", help="SHA-256 of the split, recorded only.")
    ] = None,
) -> None:
    """Match every line of a BoQ (the default command)."""
    try:
        job = MatchJob(
            split_sha256=_split_sha256(split_sha256),
            input_path=input_path,
            library_path=library,
            llm=_parse_llm(llm),
            output_path=output,
            profile=profile,
            policy_path=policy,
            use_cache=not no_cache,
            runs_dir=run_dir,
            excel_bom=excel_bom,
        )
        outcome = execute_match(job, Settings(), _runtime())
    except USAGE_ERRORS as error:
        _fail(error)
    typer.echo(render_summary(run_summary(outcome.result)))
    typer.echo(f"output: {outcome.output_path.as_posix()}; run folder {outcome.folder.as_posix()}")
    raise typer.Exit(outcome.result.exit_code)


def _recorded_setting(value: Any, current: Any) -> Any:
    """Return a recorded setting, with paths recorded as external taken from the environment."""
    if isinstance(value, Mapping) and not is_external(value):
        merged = dict(current) if isinstance(current, Mapping) else {}
        merged.update((key, item) for key, item in value.items() if not is_external(item))
        return merged
    return current if is_external(value) else value


def settings_from_manifest(manifest: Mapping[str, Any]) -> Settings:
    """Rebuild a run's non-secret settings from its manifest, keys from the environment.

    A path the manifest records as external (outside the repository) names no host directory,
    so it is taken from the current environment; ``replay_job`` checks the library's hash.

    Args:
        manifest: A run manifest.

    Returns:
        The settings the run used, with its requested model as the primary.

    """
    recorded = manifest.get("settings_effective") or {}
    current = Settings()
    known = {
        name: _recorded_setting(value, getattr(current, name))
        for name, value in recorded.items()
        if name in Settings.model_fields
    }
    known["primary_model"] = manifest["requested_model"]
    return Settings(**known)


def _recorded_file(value: Any, role: str, run_dir: Path, root: Path) -> Path | None:
    """Resolve a manifest's input or policy path: the run folder's copy when it was external."""
    if not value:
        return None
    if is_external(value):
        return external_copy(run_dir, role, str(value[EXTERNAL_PATH_KEY]))
    return root / str(value)


def _library_source(settings: Settings, manifest: Mapping[str, Any], run_dir: Path) -> Path:
    """Return a configured library of the run's id, else the run folder's external copy."""
    library_id = manifest["library_id"]
    if library_id in settings.libraries:
        return settings.libraries[library_id]
    recorded = (manifest.get("settings_effective") or {}).get("libraries", {}).get(library_id)
    copy = None
    if is_external(recorded):
        copy = external_copy(run_dir, LIBRARY_ROLE, str(recorded[EXTERNAL_PATH_KEY]))
    if copy is None or not copy.is_file():
        raise WiringError(
            f"library {library_id!r} of {run_dir.as_posix()} is not configured here and the run "
            f"folder holds no copy of it under {EXTERNAL_DIR}/{LIBRARY_ROLE}/"
        )
    return copy


def _replay_library(settings: Settings, manifest: Mapping[str, Any], run_dir: Path) -> Path:
    """Return the replayed run's library file, refusing one whose bytes changed."""
    path = _library_source(settings, manifest, run_dir)
    recorded = manifest.get("library_sha256")
    if recorded and hashlib.sha256(path.read_bytes()).hexdigest() != recorded:
        raise WiringError(f"library {path.as_posix()} is not the one the run used ({recorded})")
    return path


def _audit_line_ids(run_dir: Path) -> list[str]:
    """Return the output line ids recorded in a run's ``audit.jsonl``."""
    text = (run_dir / AUDIT_FILE).read_text(encoding=TEXT_ENCODING)
    return [json.loads(line)["line_id"] for line in text.splitlines() if line.strip()]


def replay_job(run_dir: Path, settings: Settings, policy: Path | None, root: Path) -> MatchJob:
    """Describe a replay of a recorded run as a match job.

    Args:
        run_dir: The recorded run folder.
        settings: The rebuilt settings.
        policy: A ``policy.yaml`` to re-decide with; the run's own override when None.
        root: The repository root the manifest's paths are relative to.

    Returns:
        The job: same input, library, profile, selection, policy file, BOM and spend cap,
        answered by ReplayLLM over the run's ``calls.jsonl``.

    """
    manifest = read_manifest(run_dir)
    if policy is None:
        policy = _recorded_file(manifest.get("policy_path"), POLICY_ROLE, run_dir, root)
    select = _audit_line_ids(run_dir) if manifest.get("select_count") is not None else None
    input_path = _recorded_file(manifest.get("input_path"), INPUT_ROLE, run_dir, root)
    if input_path is None:
        raise WiringError(f"{run_dir.as_posix()}: the manifest records no input file")
    return MatchJob(
        input_path=input_path,
        library_path=_replay_library(settings, manifest, run_dir),
        llm=LLMSpec(LLMKind.REPLAY, run_dir=run_dir),
        profile=RunProfile(manifest["profile"]),
        policy_path=policy,
        use_cache=False,
        excel_bom=bool(manifest.get("excel_bom")),
        selector=(lambda boq: select) if select is not None else None,
        budget_usd=manifest.get("budget_cap_usd"),
    )


def _first_difference(left: bytes, right: bytes) -> int:
    """Return the 1-based number of the first line where two outputs differ."""
    pairs = zip(left.splitlines(), right.splitlines(), strict=False)
    same = next((index for index, (a, b) in enumerate(pairs) if a != b), None)
    if same is not None:
        return same + 1
    return min(len(left.splitlines()), len(right.splitlines())) + 1


def _check_output(rendered: bytes, check: Path) -> int:
    """Byte-compare a replayed output with a file; return 0 or 1."""
    expected = check.read_bytes()
    if expected == rendered:
        typer.echo(f"check: byte-identical to {check.as_posix()}")
        return EXIT_OK
    line = _first_difference(rendered, expected)
    typer.echo(f"check: MISMATCH with {check.as_posix()} (first difference at line {line})")
    return EXIT_FAILED_CHECK


@app.command("replay")
def replay_command(
    run_dir: Annotated[Path, typer.Argument(help="A recorded run folder, runs/<run_id>.")],
    check: Annotated[Path | None, typer.Option("--check", help="CSV to byte-compare.")] = None,
    policy: Annotated[Path | None, typer.Option("--policy", help="policy.yaml override.")] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Write the CSV.")] = None,
) -> None:
    """Replay a recorded run at $0; with --check, exit 1 unless the CSV is byte-identical."""
    runtime = _runtime()
    try:
        settings = settings_from_manifest(read_manifest(run_dir))
        job = replay_job(run_dir, settings, policy, runtime.root())
        result, _ = run_match(job, prepare_run(job, settings, runtime), runtime)
        rendered = render_csv(result, excel_bom=job.excel_bom)
        if output is not None:
            output.write_bytes(rendered)
        verdict = _check_output(rendered, check) if check is not None else EXIT_OK
    except USAGE_ERRORS as error:
        _fail(error)
    typer.echo(render_summary(run_summary(result)))
    raise typer.Exit(verdict or result.exit_code)


@app.command(
    "score",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def score_command(ctx: typer.Context) -> None:
    """Score an output CSV: a passthrough to eval/score.py (run with --help for its options)."""
    script = _runtime().root() / SCORER_PATH
    if not script.is_file():
        _fail(WiringError(f"scorer not found at {script}; run oris from the repository root"))
    completed = subprocess.run(
        [sys.executable, str(script), *ctx.args],
        capture_output=True,
        text=True,
        encoding=TEXT_ENCODING,
        check=False,
    )
    typer.echo(completed.stdout, nl=False)
    typer.echo(completed.stderr, nl=False, err=True)
    raise typer.Exit(completed.returncode)


@app.command("doctor")
def doctor_command(
    live: Annotated[bool, typer.Option("--live", help="Run the paid checks.")] = False,
    evidence_dir: Annotated[Path, typer.Option("--evidence-dir")] = DEFAULT_EVIDENCE_DIR,
    run_dir: Annotated[Path, typer.Option("--run-dir")] = DEFAULT_RUNS_DIR,
) -> None:
    """Check keys, allowlist, pinned model and libraries; --live adds one 2-line call."""
    try:
        options = DoctorOptions(live=live, evidence_dir=evidence_dir, runs_dir=run_dir)
        report = run_doctor(Settings(), options, _runtime())
    except USAGE_ERRORS as error:
        _fail(error)
    typer.echo(render_table(report))
    raise typer.Exit(EXIT_OK if report.passed else EXIT_FAILED_CHECK)
