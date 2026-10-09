"""Command-line entry point: ``match`` (the default) and the other ``oris`` commands.

The commands are ``match``, ``score``, ``select``, ``replay``, ``doctor``, ``explain``,
``demo`` and ``serve``, which runs the API and the operator UI (docs/ui-spec.md) with any
``--llm`` the match command takes, ``replay:<run_dir>`` included, so a review costs $0.

``oris --input X --library Y --output Z`` is the brief's literal form of ``oris match``. A run
reads the BoQ, maps the library path to its ``Settings.libraries`` id (``custom`` otherwise),
calls ``MatchService``, writes ``runs/<run_id>/`` and the output CSV, and prints the §11.6
summary. ``--profile b0`` is the rules-only floor: it never builds a live adapter, needs no key
and may run over a whole exercise input before the freeze, because it sends nothing to a model.
``explain`` and ``demo`` are the read-only, $0 views of the live session (§12): ``explain``
prints why one line of a recorded run got its decision, from the run folder and the files its
manifest names; ``demo`` replays the committed lockbox B3 runs and byte-compares them with
``output/``. Exit codes: 0 ok, 1 a ``replay --check`` or ``demo`` mismatch or a failed doctor
check, 2 a usage or input error, 3 when any line is ``LLM_UNAVAILABLE`` or a replay miss
(§11.3). In a mixed E-08 run a main pass the replayed run never recorded is not a line
failure: it raises ``ReplayMissError``, nothing is written and the exit code is 2 (A65 note).

E-08 (A65.2) is measured as a mixed run: ``--llm replay:<run>`` with the verifier adopted, over a
run recorded without it, serves the main passes from that run's ``calls.jsonl`` through the
response cache ($0, a strict ReplayLLM behind it so nothing else is answered) and sends only the
verifier requests to ``--llm-verifier`` (the live primary by default). A ``RoutingLLM`` picks
the adapter by request kind. The run is ``cached`` (``fake`` with a fake verifier) and names the
replayed run as its source; its ``calls.jsonl`` holds both kinds, so a plain replay of it is
byte-identical. Only a B3 run is mixed, only over a source the cache serves exactly as a replay
would, and a live verifier only over answers first recorded live (A65 note, 2026-10-07).

``--enrichment <file>`` (or ``ORIS_ENRICHMENT``) renders an arm C enrichment in a B3 run, and
``none`` forces it off; without either, the deciding policy entry's file is used (A68.4). Any
replay renders the replayed run's recorded enrichment (none for a run recorded without one),
whatever the policy says now; the manifest records its path and SHA-256, and a file outside
the repository is copied into the run folder like an external ``--policy`` file.
"""

import asyncio
import dataclasses
import hashlib
import io
import ipaddress
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
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, NoReturn

import typer
import uvicorn
from fastapi import FastAPI
from typer import _click
from typer.core import TyperGroup

from oris_matcher.api.app import DEFAULT_UI_DIR, create_app
from oris_matcher.api.jobs import FAKE_MODE, Serving
from oris_matcher.doctor import (
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_RUNS_DIR,
    LIVE_KINDS,
    CapturingPort,
    DoctorOptions,
    LLMKind,
    LLMSpec,
    Runtime,
    WiringError,
    WrapperSetup,
    build_adapter,
    live_context,
    make_wrapper,
    parse_llm_spec,
    read_manifest,
    recorded_declines,
    render_table,
    resolve_spec_model,
    run_doctor,
    run_prefix_tokens,
    with_measured_tokens,
)
from oris_matcher.domain.boq import BoqFile
from oris_matcher.domain.decision import DecisionProfile
from oris_matcher.domain.library import load_library
from oris_matcher.enrichment import ENRICHMENT_OFF, is_enrichment_off
from oris_matcher.explain import RunRecords, explain_item, render_json, render_text
from oris_matcher.io.audit import (
    AUDIT_FILE,
    CALLS_FILE,
    EXTERNAL_DIR,
    EXTERNAL_PATH_KEY,
    MANIFEST_FILE,
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
from oris_matcher.llm.fake_llm import FakeLLM
from oris_matcher.llm.recording import CallRecord, ReasonForCall, read_calls_jsonl
from oris_matcher.llm.replay_llm import (
    RecordedRun,
    ReplayLLM,
    ReplayMissError,
    ResponseCache,
    is_cacheable,
)
from oris_matcher.llm.routing import RoutingLLM
from oris_matcher.llm.wrapper import PrefixKey
from oris_matcher.prompts.v1.verifier import is_verifier_request
from oris_matcher.service import (
    EXIT_OK,
    MatchService,
    RunOptions,
    RunProfile,
    RunResult,
    ServiceResources,
    budget_cap_usd,
    make_run_id,
    recorded_decision_profile,
)
from oris_matcher.settings import (
    MODELS_FILE,
    POLICY_FILE,
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
SELECTOR_PATH = Path("eval") / "select_threshold.py"
RUN_NONCE_BYTES = 8
LINES_PER_COST_UNIT = 100
PERCENT = 100.0
P50 = 50
P95 = 95
SHARE_DIGITS = 1
COST_DIGITS = 6
USAGE_ERRORS = (WiringError, ConfigError, OSError, ValueError, KeyError, ReplayMissError)
LIVE_MODE = "live"
REPLAY_MODE = "replay"
MEASURED_MODES = frozenset({LIVE_MODE, "cached"})
MAX_REPLAY_CHAIN = 64
INPUT_ROLE = "input"
POLICY_ROLE = "policy"
LIBRARY_ROLE = "library"
ENRICHMENT_ROLE = "enrichment"
FALLBACK_PREFIX = "fallback_"
FREEZE_TAG = "eval-freeze"
GIT_TAGS_AT_HEAD = ("git", "tag", "--points-at", "HEAD")
SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
EXERCISE_INPUTS_SHA256 = frozenset(
    {
        "ea19f4791cbb048d07bc3521fb806159a87560569099a5386d6208725a5c1bbc",
        "626003d156ea1de14ae8675eb79111aa6e034c46d1d8811019e6ee251d98383c",
    }
)
SERVE_HOST = "127.0.0.1"
SERVE_PORT = 8000
SERVE_WORKERS = 1
LIVE_SERVE = "live"
LOCALHOST = "localhost"
UI_INDEX = "index.html"
UI_MISSING_WARNING = (
    "warning: the operator UI build is missing at {ui_dir}, so /ui answers 404; "
    "build it with `npm --prefix ui ci && npm --prefix ui run build`"
)
OPEN_API_WARNING = (
    "warning: serving on {host} with no ORIS_API_TOKEN: anyone on the network can submit jobs"
    " (and spend the API key in live mode); set ORIS_API_TOKEN or bind 127.0.0.1"
)
SERVE_LLM_HELP = "live (default: the pinned primary, per job), fake, or replay:<run_dir>"
LLM_HELP = (
    "anthropic:<model> (default: the pinned primary), openai:<model> (e.g. gpt-4o-mini), "
    "fake, or replay:<run_dir>"
)
ENRICHMENT_HELP = (
    "Arm C enrichment file a B3 run renders, or 'none'; default: ORIS_ENRICHMENT, else the "
    "policy entry's file."
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
        drop_conflicting_votes: Forces arm ``E-subtype-drop`` (A64) on or off; None keeps the
            replayed run's recorded value for a replay, else the setting.
        verifier_adopted: Forces the E-08 verifier on or off; None keeps the replayed run's
            recorded value for a replay, else the setting.
        llm_verifier: Who answers the verifier requests of a mixed E-08 run; None means the
            live primary. Only a mixed run (see the module docstring) may name one.
        enrichment: Forces the arm C enrichment file of a B3 run, or ``none`` to force it off
            (A68.4); None keeps a replay's recorded enrichment, else follows the setting and
            then the policy entry.

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
    drop_conflicting_votes: bool | None = None
    verifier_adopted: bool | None = None
    llm_verifier: LLMSpec | None = None
    enrichment: Path | None = None


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
        verifier_spec: Who answers the verifier requests of a mixed E-08 run; None for any
            other run.

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
    verifier_spec: LLMSpec | None = None

    @property
    def rate_limit_headers(self) -> dict[str, str]:
        """The rate-limit headers of the live adapter's last successful call, else {}."""
        adapter = self.adapter
        if isinstance(adapter, RoutingLLM):
            adapter = adapter.verifier
        if isinstance(adapter, CapturingPort):
            return adapter.rate_limit_headers(successful_only=True)
        return {}

    @property
    def live_verifier(self) -> bool:
        """Whether a mixed E-08 run sends its verifier requests to a live model."""
        return self.verifier_spec is not None and self.verifier_spec.kind in LIVE_KINDS


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


def _service(
    settings: Settings, policy_path: Path | None, root: Path, fallback: Path | None = None
) -> MatchService:
    """Build the service; a ``--policy`` file replaces ``policy.yaml`` and is an override.

    A policy entry's enrichment path is repository-relative, so it resolves against ``root``;
    ``fallback`` forces the enrichment the A33 fallback's lines render (a replay's).
    """
    resources = ServiceResources.from_settings(settings)
    resources = dataclasses.replace(resources, root=root, fallback_enrichment=fallback)
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


def require_committable_inputs(job: MatchJob, root: Path, enrichment: Path | None = None) -> None:
    """Refuse host paths in a run that lands under ``runs/submission/`` (§9.6).

    Args:
        job: The run's inputs.
        root: The repository root.
        enrichment: The run's effective forced enrichment: the job's, else ``ORIS_ENRICHMENT``,
            else a replay's recorded one (A68.4); None checks the job's alone.

    Raises:
        ValueError: The run folder is committable and an input, library, policy, forced
            enrichment or replayed run lies outside the repository.

    """
    if not is_committable(job.runs_dir, root):
        return
    paths = [job.input_path, job.library_path, job.policy_path, job.llm.run_dir]
    forced = job.enrichment if enrichment is None else enrichment
    if forced is not None and not is_enrichment_off(forced):
        paths.append(forced)
    require_inside_root([path for path in paths if path is not None], root)


def reaches_model(job: MatchJob, spec: LLMSpec) -> bool:
    """Tell whether a run may send anything to a live model.

    Args:
        job: The run's inputs; a B0 run sends nothing, whatever ``--llm`` names.
        spec: The effective ``--llm``.

    Returns:
        True for a live spec on any profile but B0.

    """
    return job.profile != RunProfile.B0 and spec.kind in LIVE_KINDS


def check_exercise_input(job: MatchJob, spec: LLMSpec, input_sha256: str, runtime: Runtime) -> None:
    """Refuse a live run over a whole exercise input before the freeze (§10.3).

    The exercise inputs hold the lockbox items; before ``eval-freeze`` only the experiment
    runner's dev selection may reach a live model. Offline, replayed and B0 runs are allowed.

    Args:
        job: The run's inputs; a run with a line selector is the runner's dev selection.
        spec: The effective ``--llm``.
        input_sha256: SHA-256 of the input bytes.
        runtime: Runs the read-only ``git tag --points-at HEAD``.

    Raises:
        WiringError: A live spec over a whole exercise input while the tag is not at HEAD.

    """
    if not reaches_model(job, spec) or job.selector is not None:
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


def rules_only_adapter(model: str, allowlist: Sequence[str]) -> LLMPort:
    """Return the adapter a B0 run's wrapper holds; the service plans no batch, so no call.

    Args:
        model: The requested model, recorded in the manifest only.
        allowlist: Patterns from ``models.toml``.

    Returns:
        An offline FakeLLM, so no key is read and no live client is built.

    """
    return FakeLLM(model, allowlist)


def _adapters(
    job: MatchJob, spec: LLMSpec, model: str, settings: Settings, runtime: Runtime
) -> tuple[LLMPort, LLMPort | None]:
    """Build a run's adapter and its fallback; a B0 run gets the rules-only adapter alone."""
    allowlist = load_models_config(settings.config_file(MODELS_FILE)).allowlist.patterns
    if job.profile == RunProfile.B0:
        return rules_only_adapter(model, allowlist), None
    adapter = build_adapter(spec, model, settings, allowlist, runtime)
    if spec.kind in LIVE_KINDS:
        adapter = CapturingPort(adapter)
    fallback = fallback_adapter(spec, settings, allowlist, runtime)
    return adapter, fallback or _replay_fallback(spec, adapter)


def _replayed_profile(job: MatchJob) -> DecisionProfile | None:
    """Return the profile a replayed run recorded; None for other runs and for B0."""
    replayed = job.llm.run_dir if job.llm.kind == LLMKind.REPLAY else None
    if replayed is None or job.profile == RunProfile.B0:
        return None
    return recorded_decision_profile(read_manifest(replayed))


def decision_settings(job: MatchJob, settings: Settings) -> Settings:
    """Apply the run's decision profile: the job's values, else a replay's recorded ones.

    Args:
        job: The run's inputs.
        settings: The effective settings.

    Returns:
        The settings with ``drop_conflicting_votes`` (A64) and ``verifier_adopted`` (E-08)
        forced by the job, or read back from the replayed run's manifest (off for a run
        recorded before either); otherwise unchanged. A B0 run reads no replayed folder.

    """
    recorded = _replayed_profile(job)
    drop, verify = job.drop_conflicting_votes, job.verifier_adopted
    if recorded is not None:
        drop = recorded.drop_conflicting_votes if drop is None else drop
        verify = recorded.verifier_adopted if verify is None else verify
    values = {"drop_conflicting_votes": drop, "verifier_adopted": verify}
    update = {name: value for name, value in values.items() if value is not None}
    return settings.model_copy(update=update) if update else settings


def recorded_enrichment(run_dir: Path, root: Path, prefix: str = "") -> Path:
    """Return the enrichment a recorded run rendered: its file, or ``none`` (A68.4).

    A run recorded before A68, or without an enrichment, records no ``enrichment_sha256``,
    so its replay renders none whatever the setting or the policy says now.

    Args:
        run_dir: The recorded run folder.
        root: The repository root a recorded path is relative to.
        prefix: ``fallback_`` reads what the A33 fallback's lines rendered instead.

    Returns:
        ``none``, or the recorded file: repository-relative, or the run folder's copy of an
        external one.

    Raises:
        WiringError: The recorded file is missing, or its bytes changed.

    """
    manifest = read_manifest(run_dir)
    recorded = manifest.get(f"{prefix}enrichment_sha256")
    if not recorded:
        return Path(ENRICHMENT_OFF)
    value = manifest.get(f"{prefix}enrichment_path")
    path = _recorded_file(value, ENRICHMENT_ROLE, run_dir, root)
    if path is None or not path.is_file():
        raise WiringError(f"{run_dir.as_posix()}: the enrichment file it rendered is not found")
    if hashlib.sha256(path.read_bytes()).hexdigest() != recorded:
        raise WiringError(
            f"enrichment {path.as_posix()} is not the one {run_dir.as_posix()} rendered "
            f"({str(recorded)[:12]})"
        )
    return path


def enrichment_settings(job: MatchJob, settings: Settings, root: Path) -> Settings:
    """Apply the run's enrichment: the job's value, else a replay's recorded one (A68.4).

    Args:
        job: The run's inputs.
        settings: The effective settings.
        root: The repository root a relative path resolves against.

    Returns:
        The settings with ``enrichment`` forced by the job, or read back from the replayed
        run (``none`` for a run recorded without one); otherwise unchanged, so the setting and
        then the policy entry decide. A forced file is made absolute against the root.

    Raises:
        WiringError: A replayed run's enrichment file is missing or changed.

    """
    forced = job.enrichment
    replayed = _replayed_dir(job, job.llm)
    if forced is None and replayed is not None:
        forced = recorded_enrichment(replayed, root)
    if forced is None:
        return settings
    if not is_enrichment_off(forced):
        forced = _resolved(forced, root)
    return settings.model_copy(update={"enrichment": forced})


def fallback_enrichment(job: MatchJob, root: Path) -> Path | None:
    """Return the enrichment a replay's fallback re-renders: what the recorded one rendered.

    A forced ``--enrichment`` applies to the fallback too, so it leaves this None, as does a
    run that replays nothing, or one whose recorded fallback never took over or was recorded
    before the fallback rendered its own entry's enrichment (it then rendered the primary's).

    Args:
        job: The run's inputs.
        root: The repository root a recorded path is relative to.

    Returns:
        ``none`` or the recorded file, else None to follow the setting and the policy entry.

    Raises:
        WiringError: The recorded file is missing, or its bytes changed.

    """
    replayed = _replayed_dir(job, job.llm)
    if job.enrichment is not None or replayed is None:
        return None
    manifest = read_manifest(replayed)
    if not manifest.get("fallback_engaged") or "fallback_enrichment_sha256" not in manifest:
        return None
    return recorded_enrichment(replayed, root, FALLBACK_PREFIX)


def mixed_verifier_spec(job: MatchJob, settings: Settings) -> LLMSpec | None:
    """Return who answers the verifier requests when a run is a mixed E-08 run, else None.

    A run is mixed when it is a B3 run that replays a run recorded without the verifier while
    the verifier is adopted: its main passes come from that run, its verifier requests from this
    spec. B0 and B2 never run the verifier, so their replays stay plain replays whatever the
    flag says.

    Args:
        job: The run's inputs.
        settings: The settings after ``decision_settings``.

    Returns:
        ``job.llm_verifier``, or the live primary (``anthropic``) when it names none.

    Raises:
        WiringError: ``--llm-verifier`` is named for a run that is not mixed, or names a
            replay.

    """
    recorded = _replayed_profile(job) if job.profile == RunProfile.B3 else None
    mixed = recorded is not None and settings.verifier_adopted and not recorded.verifier_adopted
    if not mixed:
        if job.llm_verifier is not None:
            raise WiringError(
                "--llm-verifier applies only to a B3 --llm replay:<run> of a run recorded "
                "without the E-08 verifier, with the verifier adopted"
            )
        return None
    spec = job.llm_verifier or LLMSpec(LLMKind.ANTHROPIC)
    if spec.kind == LLMKind.REPLAY:
        raise WiringError("--llm-verifier names a live provider or fake, never a replay")
    return spec


def _replayed_parent(folder: Path, manifest: Mapping[str, Any], root: Path) -> Path:
    """Return the folder a replay run replayed: its ``source_run_id`` sibling, else ``llm_run_dir``.

    Args:
        folder: The replay run folder.
        manifest: Its manifest.
        root: The repository root ``llm_run_dir`` is relative to.

    Returns:
        The replayed run folder.

    Raises:
        WiringError: Neither names a folder holding a manifest.

    """
    candidates: list[Path] = []
    source = manifest.get("source_run_id")
    if source:
        candidates.append(folder.parent / str(source))
    recorded = manifest.get("llm_run_dir")
    if isinstance(recorded, str):
        candidates.append(root / recorded)
    found = next((path for path in candidates if (path / MANIFEST_FILE).is_file()), None)
    if found is None:
        raise WiringError(
            f"{folder.as_posix()} is a replay whose replayed run cannot be found, so where its "
            "answers came from is unknown"
        )
    return found


def origin_mode(run_dir: Path, root: Path) -> str:
    """Return the mode of the run a folder's answers were first recorded in.

    A replay records mode ``replay`` whatever it replayed, so the chain is followed down to the
    first run that is not a replay: ``live`` or ``cached`` answers are measurements, ``fake``
    or ``rules`` ones are not.

    Args:
        run_dir: The replayed run folder.
        root: The repository root a recorded ``llm_run_dir`` is relative to.

    Returns:
        That run's manifest mode.

    Raises:
        WiringError: A link of the chain is missing, or the chain does not end.

    """
    folder = run_dir
    for _ in range(MAX_REPLAY_CHAIN):
        manifest = read_manifest(folder)
        mode = str(manifest.get("mode") or "")
        if mode != REPLAY_MODE:
            return mode
        folder = _replayed_parent(folder, manifest, root)
    raise WiringError(f"{run_dir.as_posix()}: the replay chain does not end at a recorded run")


def require_measured_origin(run_dir: Path, root: Path) -> None:
    """Refuse a live verifier over answers that were not first recorded live (or cached).

    Args:
        run_dir: The replayed run folder.
        root: The repository root.

    Raises:
        WiringError: The chain of replays ends at a ``fake`` or ``rules`` run, or is broken.

    """
    origin = origin_mode(run_dir, root)
    if origin not in MEASURED_MODES:
        raise WiringError(
            f"the answers of {run_dir.as_posix()} come from a {origin} run: a live verifier "
            "over them would measure nothing; use --llm-verifier fake"
        )


def cache_served_records(run_dir: Path) -> list[CallRecord]:
    """Return a mixed run's source records once the cache provably serves them as a replay would.

    The cache keeps content records only, by occurrence of their hash, and knows nothing of the
    wrapper's retry chain or declines. Serving the main passes from it equals replaying them
    only when every recorded attempt delivered content, no request hash was sent twice and the
    run declined nothing.

    Args:
        run_dir: The replayed run folder.

    Returns:
        Its records, in recording order.

    Raises:
        WiringError: A record that is not content, a repeated request hash, or a declined
            attempt.

    """
    records = read_calls_jsonl(run_dir / CALLS_FILE)
    hashes = [record.request_sha256 for record in records]
    problems = (
        ("an attempt that delivered no content", not all(map(is_cacheable, records))),
        ("a request hash sent more than once", len(set(hashes)) != len(hashes)),
        ("a declined attempt", bool(recorded_declines(run_dir))),
    )
    found = [name for name, present in problems if present]
    if found:
        raise WiringError(
            f"{run_dir.as_posix()} cannot be served exactly from the cache: it records "
            f"{', '.join(found)}; replay it plainly instead"
        )
    return records


def mixed_adapter(
    job: MatchJob, verifier_spec: LLMSpec, model: str, settings: Settings, runtime: Runtime
) -> RoutingLLM:
    """Build a mixed E-08 run's port: replayed main passes, verifier requests to their spec.

    The main route is a strict ReplayLLM over the replayed run: the run's cache serves every
    recorded answer first, so this route only ever sees a request the replayed run never made,
    and raises instead of calling anything.

    Args:
        job: The run's inputs; ``job.llm`` replays a run recorded without the verifier.
        verifier_spec: Who answers the verifier requests.
        model: The replayed run's requested model, which the verifier must use too.
        settings: The effective settings.
        runtime: Supplies the adapter factory and the HTTP client.

    Returns:
        The routing port.

    Raises:
        WiringError: No replayed folder, a live verifier over a run whose answers are not
            measurements (``origin_mode``), a replayed run the cache cannot serve exactly
            (``cache_served_records``), or a verifier spec whose model is not the replayed
            run's.

    """
    run_dir = job.llm.run_dir
    if run_dir is None:
        raise WiringError("a mixed E-08 run replays a run folder: --llm replay:<run_dir>")
    if verifier_spec.kind in LIVE_KINDS:
        require_measured_origin(run_dir, runtime.root())
    models_config = load_models_config(settings.config_file(MODELS_FILE))
    pricing = load_pricing(settings.config_file(PRICING_FILE))
    if resolve_spec_model(verifier_spec, settings, pricing, models_config) != model:
        raise WiringError(f"--llm-verifier must answer with the replayed run's model {model}")
    allowlist = models_config.allowlist.patterns
    recorded = RecordedRun.from_records(cache_served_records(run_dir))
    main = ReplayLLM(recorded, model, allowlist, strict=True, declined=())
    verifier = build_adapter(verifier_spec, model, settings, allowlist, runtime)
    if verifier_spec.kind in LIVE_KINDS:
        verifier = CapturingPort(verifier)
    return RoutingLLM(main=main, verifier=verifier, is_verifier=is_verifier_request)


def prepare_run(job: MatchJob, base: Settings, runtime: Runtime) -> PreparedRun:
    """Read the input and build the settings, service and adapters of a run.

    Args:
        job: The run's inputs.
        base: The settings before the library and model are applied.
        runtime: The environment.

    Returns:
        The prepared run.

    Raises:
        WiringError: Bad ``--llm``, a missing key (never for B0), a missing library file, or a
            live run over an exercise input before the freeze.
        ConfigError: A config file is invalid or a model is not allowed.
        BoqFormatError: The input cannot be read as a BoQ.
        OSError: The input cannot be read.
        ValueError: A committable run reads a file outside the repository.

    """
    require_committable_inputs(job, runtime.root())
    data = job.input_path.read_bytes()
    boq = read_boq(data)
    settings, library_id = settings_for_library(base, job.library_path, runtime.root())
    settings = decision_settings(job, settings)
    settings = enrichment_settings(job, settings, runtime.root())
    require_committable_inputs(job, runtime.root(), settings.enrichment)
    spec = job.llm if job.profile == RunProfile.B0 else effective_spec(job.llm, settings)
    digest = hashlib.sha256(data).hexdigest()
    check_exercise_input(job, spec, digest, runtime)
    models_config = load_models_config(settings.config_file(MODELS_FILE))
    pricing = load_pricing(settings.config_file(PRICING_FILE))
    model = resolve_spec_model(spec, settings, pricing, models_config)
    settings = settings.model_copy(update={"primary_model": model})
    verifier_spec = mixed_verifier_spec(job, settings)
    if verifier_spec is None:
        adapter, fallback = _adapters(job, spec, model, settings, runtime)
    else:
        check_exercise_input(job, verifier_spec, digest, runtime)
        adapter, fallback = mixed_adapter(job, verifier_spec, model, settings, runtime), None
    rescued = fallback_enrichment(job, runtime.root())
    service = _service(settings, job.policy_path, runtime.root(), rescued)
    return PreparedRun(
        settings, library_id, pricing, service, adapter, boq, digest, spec, fallback, verifier_spec
    )


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


def replayed_cache(run_dir: Path) -> CacheIndex:
    """Seed a mixed E-08 run's cache with every answer the replayed run recorded.

    The replayed run's records are its responses verbatim (a replay's are copies of a live
    run's), so serving them as cache hits copies the main passes at $0, and each hit names the
    replayed run as its source.

    Args:
        run_dir: The replayed run folder.

    Returns:
        The cache and, for each original call id, the replayed run's id.

    """
    records = read_calls_jsonl(run_dir / CALLS_FILE)
    run_id = str(read_manifest(run_dir).get("run_id") or run_dir.name)
    sources = {record.source_call_id or record.call_id: run_id for record in records}
    return CacheIndex(ResponseCache.from_records(records), sources)


def _cache_index(job: MatchJob, prepared: PreparedRun) -> CacheIndex:
    """Return the run's cache; a mixed E-08 run's holds the replayed run's answers.

    Every other run gets earlier live runs' answers, none for ``--no-cache`` and none for a
    fake, replayed or B0 run.
    """
    if prepared.verifier_spec is not None and job.llm.run_dir is not None:
        return replayed_cache(job.llm.run_dir)
    if not job.use_cache or not reaches_model(job, prepared.spec):
        return CacheIndex()
    key = CacheKey(prepared.spec.kind.value, prepared.service.resources.requested_model)
    return load_cache(job.runs_dir, key)


def _cap(job: MatchJob, settings: Settings, line_count: int) -> float:
    """Return the run's spend cap: the floored §11.3 cap, lowered by the job's hard budget."""
    cap = budget_cap_usd(settings, line_count)
    return cap if job.budget_usd is None else min(cap, job.budget_usd)


def _replayed_dir(job: MatchJob, spec: LLMSpec) -> Path | None:
    """Return the run folder a ``replay:`` spec reads; None for B0, which reads nothing (§9.6)."""
    if job.profile == RunProfile.B0 or spec.kind != LLMKind.REPLAY:
        return None
    return spec.run_dir


def _source_run_id(job: MatchJob, spec: LLMSpec) -> str | None:
    """Return the replayed run's id for a ``replay:`` spec, never for a B0 run."""
    replayed = _replayed_dir(job, spec)
    if replayed is None:
        return None
    return str(read_manifest(replayed).get("run_id") or "") or None


def measured_prefix(job: MatchJob, prepared: PreparedRun, runtime: Runtime) -> dict[PrefixKey, int]:
    """Return the doctor's measured prefix sizes for a live Anthropic run, else {} (A63).

    Args:
        job: The run's inputs.
        prepared: The prepared run.
        runtime: The environment.

    Returns:
        ``prefix_key`` -> measured tokens, the same lookup the API makes.

    """
    if not reaches_model(job, prepared.spec) or prepared.spec.kind != LLMKind.ANTHROPIC:
        return {}
    return run_prefix_tokens(prepared.service, prepared.library_id, runtime.root())


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
    measured = measured_prefix(job, prepared, runtime)
    setup = WrapperSetup(cap, index.cache, run_id, measured_prefix_tokens=measured)
    wrapper = make_wrapper(prepared.adapter, prepared.pricing, prepared.settings, setup, runtime)
    options = RunOptions(
        select=select,
        run_id=run_id,
        source_run_id=_source_run_id(job, prepared.spec),
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
    doctor evidence (``unknown`` without one); a fake, replayed or B0 run records neither.
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
    if not reaches_model(job, prepared.spec) and not prepared.live_verifier:
        return context
    return live_context(context, prepared.rate_limit_headers)


def _extra_fields(job: MatchJob, prepared: PreparedRun, cap: float, root: Path) -> dict[str, Any]:
    """Return the manifest fields only the CLI knows: adapter, cap, input and policy file."""
    policy, replayed = job.policy_path, _replayed_dir(job, prepared.spec)
    verifier = prepared.verifier_spec
    return {
        **({"llm_verifier": verifier.text} if verifier is not None else {}),
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


def keep_external_inputs(
    job: MatchJob, folder: Path, root: Path, enrichment: Path | None = None
) -> None:
    """Copy an input, library, policy or enrichment file outside the repository into the run.

    The manifest records such a file by its name only (§9.6), so the copy is what a replay
    reads when the environment has no such file. Committable runs never get here with an
    outside file (``require_committable_inputs``).

    Args:
        job: The run's inputs.
        folder: The run folder.
        root: The repository root.
        enrichment: The enrichment file the run rendered, or None (A68.4).

    """
    roles = (
        (INPUT_ROLE, job.input_path),
        (LIBRARY_ROLE, job.library_path),
        (POLICY_ROLE, job.policy_path),
        (ENRICHMENT_ROLE, enrichment),
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
    if reaches_model(job, prepared.spec) and prepared.spec.kind == LLMKind.ANTHROPIC:
        root = runtime.root()
        manifest = with_measured_tokens(manifest, root / DEFAULT_EVIDENCE_DIR, root)
    folder = write_run(result, job.runs_dir, manifest)
    keep_external_inputs(job, folder, runtime.root(), result.enrichment_path)
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
    enrichment: Annotated[str | None, typer.Option("--enrichment", help=ENRICHMENT_HELP)] = None,
) -> None:
    """Match every line of a BoQ (the default command)."""
    try:
        job = MatchJob(
            enrichment=Path(enrichment) if enrichment is not None else None,
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
        The settings the run used, with its requested model as the primary and its recorded
        decision profile (each flag off for a run recorded without it).

    """
    recorded = manifest.get("settings_effective") or {}
    current = Settings()
    known = {
        name: _recorded_setting(value, getattr(current, name))
        for name, value in recorded.items()
        if name in Settings.model_fields
    }
    known["primary_model"] = manifest["requested_model"]
    profile = recorded_decision_profile(manifest)
    known["drop_conflicting_votes"] = profile.drop_conflicting_votes
    known["verifier_adopted"] = profile.verifier_adopted
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


RE_RESOLVED = frozenset({"exact", "fallback_strictest"})


def require_recorded_policy(manifest: Mapping[str, Any], settings: Settings) -> None:
    """Refuse a replay that today's ``policy.yaml`` would decide at another threshold (§11.5).

    A run resolved through ``config/policy.yaml`` (``exact`` or ``fallback_strictest``)
    records no policy file, so a replay resolves the current one. When that entry now names
    another threshold, the replay would silently re-decide the run instead of reproducing
    it. Overrides, path-less runs and B0/B2 resolve as they did whatever the file says. A run the
    fallback rescued decided at the fallback model's policy, so that model's entry is compared.

    Args:
        manifest: The recorded run's manifest.
        settings: The settings the replay runs with.

    Raises:
        WiringError: The current entry differs from the recorded resolution or threshold.

    """
    recorded = manifest.get("policy_resolution")
    if recorded not in RE_RESOLVED:
        return
    deciding = "fallback_model" if manifest.get("fallback_engaged") else "requested_model"
    entry = load_policy(settings.config_file(POLICY_FILE)).lookup(
        str(manifest[deciding]), str(manifest["library_sha256"])
    )
    now = ("exact", entry.policy_id) if entry else ("fallback_strictest", None)
    then = (recorded, manifest.get("policy_id") if recorded == "exact" else None)
    if now != then:
        raise WiringError(
            f"this run was decided at {manifest.get('policy_id')} ({recorded}), but "
            f"{POLICY_FILE} now resolves {now[1] or 'the strictest threshold'} ({now[0]}); "
            "replay with --policy naming a policy file that reproduces it (an empty "
            "'policies: {}' file for a fallback_strictest run)"
        )


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
    if policy is None:
        require_recorded_policy(manifest, settings)
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


def replay_run(run_dir: Path, policy: Path | None, runtime: Runtime) -> tuple[RunResult, bytes]:
    """Replay a recorded run at $0 and render its output CSV, writing nothing.

    Args:
        run_dir: The recorded run folder.
        policy: A ``policy.yaml`` to re-decide with; the run's own when None.
        runtime: The environment.

    Returns:
        The replayed run and its rendered CSV bytes.

    """
    settings = settings_from_manifest(read_manifest(run_dir))
    job = replay_job(run_dir, settings, policy, runtime.root())
    result, _ = run_match(job, prepare_run(job, settings, runtime), runtime)
    return result, render_csv(result, excel_bom=job.excel_bom)


@app.command("replay")
def replay_command(
    run_dir: Annotated[Path, typer.Argument(help="A recorded run folder, runs/<run_id>.")],
    check: Annotated[Path | None, typer.Option("--check", help="CSV to byte-compare.")] = None,
    policy: Annotated[Path | None, typer.Option("--policy", help="policy.yaml override.")] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Write the CSV.")] = None,
) -> None:
    """Replay a recorded run at $0; with --check, exit 1 unless the CSV is byte-identical."""
    try:
        result, rendered = replay_run(run_dir, policy, _runtime())
        if output is not None:
            output.write_bytes(rendered)
        verdict = _check_output(rendered, check) if check is not None else EXIT_OK
    except USAGE_ERRORS as error:
        _fail(error)
    typer.echo(render_summary(run_summary(result)))
    raise typer.Exit(verdict or result.exit_code)


def _recorded_input(run_dir: Path, manifest: Mapping[str, Any], root: Path) -> BoqFile:
    """Read the input a run's manifest names, refusing one whose bytes changed."""
    path = _recorded_file(manifest.get("input_path"), INPUT_ROLE, run_dir, root)
    if path is None:
        raise WiringError(f"{run_dir.as_posix()}: the manifest records no input file")
    data = path.read_bytes()
    recorded = manifest.get("input_sha256")
    if recorded and hashlib.sha256(data).hexdigest() != recorded:
        raise WiringError(f"input {path.as_posix()} is not the one the run used ({recorded})")
    return read_boq(data)


def run_records(run_dir: Path, root: Path) -> RunRecords:
    """Gather what ``explain`` reads of a recorded run, without replaying or calling anything.

    Args:
        run_dir: The recorded run folder.
        root: The repository root the manifest's paths are relative to.

    Returns:
        The manifest, with the input and library it names, both hash-checked.

    """
    manifest = read_manifest(run_dir)
    settings = settings_from_manifest(manifest)
    library_path = _replay_library(settings, manifest, run_dir)
    library = load_library(library_path.read_bytes(), catalogue=str(manifest["library_id"]))
    boq = _recorded_input(run_dir, manifest, root)
    return RunRecords(run_dir=run_dir, manifest=manifest, boq=boq, library=library)


@app.command("explain")
def explain_command(
    run_dir: Annotated[Path, typer.Option("--run", help="A recorded run folder.")],
    item: Annotated[str, typer.Option("--item", help="The line's Item No. (or line id).")],
    as_json: Annotated[bool, typer.Option("--json", help="One JSON object, sorted keys.")] = False,
    full: Annotated[
        bool, typer.Option("--full", help="Also print each call's user message and raw response.")
    ] = False,
) -> None:
    """Explain why one line of a recorded run got its decision; reads only, calls nothing."""
    try:
        explanation = explain_item(run_records(run_dir, _runtime().root()), item)
    except USAGE_ERRORS as error:
        _fail(error)
    typer.echo(render_json(explanation) if as_json else render_text(explanation, full=full))


class DemoLang(StrEnum):
    """Which committed lockbox run ``oris demo`` replays."""

    EN = "en"
    FR = "fr"
    BOTH = "both"


@dataclass(frozen=True)
class DemoRun:
    """One committed lockbox B3 run and the output it must reproduce byte for byte.

    Attributes:
        run_dir: The run folder, repository-relative.
        expected: The committed output CSV, repository-relative.

    """

    run_dir: Path
    expected: Path


DEMO_RUNS: Mapping[str, DemoRun] = MappingProxyType(
    {
        DemoLang.EN.value: DemoRun(
            Path("runs/submission/20261008T023928Z-56f85fb8"),
            Path("output/improved_output_en.csv"),
        ),
        DemoLang.FR.value: DemoRun(
            Path("runs/submission/20261008T024244Z-de394c39"),
            Path("output/improved_output_fr.csv"),
        ),
    }
)
DEMO_DECISIONS = ("matched", "needs_review", "not_a_material")
FLIP_RATE_NOTE = (
    "note: this is a $0 replay of recorded answers; a live run of the same input can differ "
    "from it by up to the measured decision flip rate (temperature 0 is not bit-deterministic "
    "on hosted APIs, D-11); measured on dev: 0 decision flips in 199 lines per language "
    "(G3 cold live rerun, docs/gates/G3.md §5)"
)


def demo_languages(lang: DemoLang) -> tuple[str, ...]:
    """Return the languages ``--lang`` selects, in a fixed order.

    Args:
        lang: The option.

    Returns:
        ``("en", "fr")`` for both, else the one language.

    """
    if lang == DemoLang.BOTH:
        return (DemoLang.EN.value, DemoLang.FR.value)
    return (lang.value,)


def demo_summary(lang: str, result: RunResult, verdict: int) -> str:
    """Render one language's demo line.

    Args:
        lang: The language.
        result: The replayed run.
        verdict: 0 when the replay is byte-identical, else 1.

    Returns:
        Rows, decisions, the three decision counts and the replay verdict.

    """
    counts = Counter(item.decision.decision.value for item in result.lines)
    decisions = ", ".join(f"{name} {counts.get(name, 0)}" for name in DEMO_DECISIONS)
    replay = "byte-identical" if verdict == EXIT_OK else "NOT byte-identical"
    return (
        f"{lang}: {len(result.lines)} rows, {sum(counts.values())} decisions ({decisions}); "
        f"replay {replay}"
    )


def demo_one(lang: str, runtime: Runtime) -> int:
    """Replay one committed run, byte-compare it and print its summary.

    Args:
        lang: ``en`` or ``fr``.
        runtime: The environment; paths are relative to its root.

    Returns:
        0 when the replay is byte-identical, else 1.

    """
    demo = DEMO_RUNS[lang]
    root = runtime.root()
    typer.echo(f"demo {lang}: replaying {demo.run_dir.as_posix()} at $0")
    result, rendered = replay_run(root / demo.run_dir, None, runtime)
    verdict = _check_output(rendered, root / demo.expected)
    typer.echo(demo_summary(lang, result, verdict))
    return verdict


@app.command("demo")
def demo_command(
    lang: Annotated[DemoLang, typer.Option("--lang", help="en, fr or both.")] = DemoLang.BOTH,
) -> None:
    """Replay the committed lockbox B3 runs at $0; exit 1 unless each output is byte-identical."""
    runtime = _runtime()
    try:
        verdicts = [demo_one(language, runtime) for language in demo_languages(lang)]
    except USAGE_ERRORS as error:
        _fail(error)
    typer.echo(FLIP_RATE_NOTE)
    raise typer.Exit(EXIT_FAILED_CHECK if any(verdicts) else EXIT_OK)


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


@app.command(
    "select",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def select_command(ctx: typer.Context) -> None:
    """Select the threshold from replayed dev votes: a passthrough to eval/select_threshold.py."""
    script = _runtime().root() / SELECTOR_PATH
    if not script.is_file():
        _fail(WiringError(f"selector not found at {script}; run oris from the repository root"))
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


def run_server(app: FastAPI, host: str, port: int, **options: Any) -> None:
    """Run the app under uvicorn; tests replace this.

    Args:
        app: The application.
        host: The interface to bind.
        port: The TCP port.
        **options: Further ``uvicorn.run`` options.

    """
    uvicorn.run(app, host=host, port=port, **options)


def serve_settings(spec: LLMSpec, base: Settings, root: Path) -> Settings:
    """Apply what ``oris match`` applies for an ``--llm``: a replay's profile and enrichment.

    Args:
        spec: The parsed ``--llm``.
        base: The settings.
        root: The repository root recorded paths resolve against.

    Returns:
        The settings with a replayed run's recorded decision profile and enrichment, and the
        model the spec requests as the primary.

    Raises:
        WiringError: The replayed run's manifest or enrichment cannot be read.

    """
    job = MatchJob(input_path=Path(), library_path=Path(), llm=spec)
    settings = enrichment_settings(job, decision_settings(job, base), root)
    models_config = load_models_config(settings.config_file(MODELS_FILE))
    pricing = load_pricing(settings.config_file(PRICING_FILE))
    model = resolve_spec_model(spec, settings, pricing, models_config)
    return settings.model_copy(update={"primary_model": model})


def _serve_factory(spec: LLMSpec, settings: Settings, runtime: Runtime) -> Callable[[str], LLMPort]:
    """Return the per-job adapter factory of a served ``--llm``, built as ``oris match`` does.

    Each call builds a fresh adapter, so every job replays from the first recorded attempt.
    An offline spec has no fallback, as in the CLI, unless the replayed run engaged one.
    """
    allowlist = load_models_config(settings.config_file(MODELS_FILE)).allowlist.patterns
    primary = str(settings.primary_model)
    replayed = spec.run_dir if spec.kind == LLMKind.REPLAY else None
    fallback_ok = spec.kind in LIVE_KINDS or (
        replayed is not None and bool(read_manifest(replayed).get("fallback_engaged"))
    )

    def factory(model: str) -> LLMPort:
        """Build the adapter for one job's model."""
        if model != primary and not fallback_ok:
            raise WiringError(f"--llm {spec.kind.value} serves no fallback model")
        return build_adapter(spec, model, settings, allowlist, runtime)

    return factory


def serve_app(
    llm: str | None, base: Settings, runtime: Runtime, runs_dir: Path = DEFAULT_RUNS_DIR
) -> FastAPI:
    """Build the API and operator UI for ``oris serve``.

    Args:
        llm: ``live`` (or None) for the pinned primary built per job, else an ``--llm`` spec
            such as ``fake`` or ``replay:<run_dir>``.
        base: The settings.
        runtime: The environment.
        runs_dir: Where each job writes its run folder.

    Returns:
        The application.

    Raises:
        WiringError: A bad spec, or a replayed run that cannot be read.
        ConfigError: A config file is invalid.

    """
    if llm is None or llm == LIVE_SERVE:
        return create_app(base, runtime=runtime, runs_dir=runs_dir)
    spec = parse_llm_spec(llm)
    root = runtime.root()
    settings = serve_settings(spec, base, root)
    job = MatchJob(input_path=Path(), library_path=Path(), llm=spec)
    service = _service(settings, None, root, fallback_enrichment(job, root))
    factory = _serve_factory(spec, settings, runtime)
    return create_app(
        settings,
        factory,
        runtime=runtime,
        runs_dir=runs_dir,
        service=service,
        serving=serving_of(spec),
    )


def serving_of(spec: LLMSpec) -> Serving:
    """Describe how a served ``--llm`` answers; a replay runs only its recorded library.

    Args:
        spec: The parsed ``--llm``.

    Returns:
        ``fake`` or ``live`` for those specs; for a replay, its run id and the one library the
        run was recorded on, since another library's enrichment does not match the recording.

    Raises:
        KeyError: The replayed manifest names no library.
        WiringError: The replayed manifest cannot be read.

    """
    if spec.kind != LLMKind.REPLAY or spec.run_dir is None:
        return Serving(mode=FAKE_MODE if spec.kind == LLMKind.FAKE else LIVE_MODE)
    manifest = read_manifest(spec.run_dir)
    run_id = str(manifest.get("run_id") or spec.run_dir.name)
    library_id = str(manifest["library_id"])
    note = f"replay run {run_id} was recorded on library {library_id}"
    return Serving(REPLAY_MODE, run_id, (library_id,), note)


def serve_warnings(host: str, settings: Settings, ui_dir: Path) -> list[str]:
    """List what an operator should know before ``oris serve`` starts.

    Args:
        host: The interface to bind.
        settings: The settings.
        ui_dir: The operator UI's build folder.

    Returns:
        A warning when the UI build is missing, and one when a non-loopback host has no token.

    """
    warnings_found: list[str] = []
    if not (ui_dir / UI_INDEX).is_file():
        warnings_found.append(UI_MISSING_WARNING.format(ui_dir=ui_dir))
    token = settings.api_token
    if not _is_loopback(host) and (token is None or not token.get_secret_value().strip()):
        warnings_found.append(OPEN_API_WARNING.format(host=host))
    return warnings_found


def _is_loopback(host: str) -> bool:
    """Tell whether a bind address only accepts connections from this machine."""
    if host == LOCALHOST:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@app.command("serve")
def serve_command(
    host: Annotated[str, typer.Option("--host", help="Interface to bind.")] = SERVE_HOST,
    port: Annotated[int, typer.Option("--port", help="TCP port.")] = SERVE_PORT,
    llm: Annotated[str, typer.Option("--llm", help=SERVE_LLM_HELP)] = LIVE_SERVE,
    run_dir: Annotated[Path, typer.Option("--run-dir")] = DEFAULT_RUNS_DIR,
) -> None:
    """Serve the API and the operator UI (/ui) in one worker; jobs live in memory."""
    settings = Settings()
    try:
        server = serve_app(llm, settings, _runtime(), run_dir)
    except USAGE_ERRORS as error:
        _fail(error)
    for warning in serve_warnings(host, settings, DEFAULT_UI_DIR):
        typer.echo(warning, err=True)
    typer.echo(f"operator UI: http://{host}:{port}/ui/  (API docs: /docs; --llm {llm})")
    run_server(server, host=host, port=port, workers=SERVE_WORKERS)
