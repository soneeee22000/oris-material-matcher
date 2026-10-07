"""Run folder writer: manifest.json, calls.jsonl and audit.jsonl (DESIGN.md §9.6).

``runs/<run_id>/`` holds the manifest (sorted keys, repo-relative POSIX paths, a path outside
the repository as its file name with ``external: true``, unknown values written as explicit
nulls), one call record per attempt, one audit record per output line, and
each distinct set of system blocks once under ``prompts/<sha256>.txt``, where the file's own
SHA-256 is its name. The code version is read with two read-only git commands.
"""

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from oris_matcher.llm.recording import record_line
from oris_matcher.prompts.v1 import render
from oris_matcher.service import RunResult
from oris_matcher.settings import PricingTable, Settings

TEXT_ENCODING = "utf-8"
JSON_NEWLINE = "\n"
JSON_INDENT = 2
MANIFEST_FILE = "manifest.json"
CALLS_FILE = "calls.jsonl"
AUDIT_FILE = "audit.jsonl"
PROMPTS_DIR = "prompts"
PROMPT_SUFFIX = ".txt"
OTEL_GENAI_SEMCONV_VERSION = "1.37.0"
SDK_PACKAGES = ("anthropic", "openai")
SECRET_SETTINGS = frozenset({"anthropic_api_key", "openai_api_key", "api_token"})
GIT_HEAD = ("git", "rev-parse", "HEAD")
GIT_STATUS = ("git", "status", "--porcelain")
GIT_TIMEOUT_S = 10.0
SUBMISSION_DIR = Path("runs") / "submission"
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
GLOSSARY_PATH = Path(render.__file__).parent / render.GLOSSARY_FILE
RATE_LIMIT_SOURCE_NONE = "none"
EXTERNAL_FLAG = "external"
EXTERNAL_PATH_KEY = "path"
EXTERNAL_DIR = "external"

GitRunner = Callable[[Sequence[str], Path], str]


@dataclass(frozen=True)
class CodeVersion:
    """The code a run was made with.

    Attributes:
        sha: ``git rev-parse HEAD``, or None when git is unavailable.
        dirty: Whether ``git status --porcelain`` lists changes, or None when unknown.

    """

    sha: str | None
    dirty: bool | None


def _run_git(args: Sequence[str], root: Path) -> str:
    """Run one read-only git command in ``root`` and return its standard output."""
    completed = subprocess.run(
        list(args),
        cwd=root,
        capture_output=True,
        text=True,
        encoding=TEXT_ENCODING,
        check=True,
        timeout=GIT_TIMEOUT_S,
    )
    return completed.stdout


def code_version(root: Path, runner: GitRunner = _run_git) -> CodeVersion:
    """Read the code SHA and the dirty flag with ``git rev-parse`` and ``git status``.

    Args:
        root: The repository root.
        runner: Runs one git command; injectable for tests.

    Returns:
        The version; both fields are None when git cannot be run.

    """
    try:
        sha = runner(GIT_HEAD, root).strip()
        status = runner(GIT_STATUS, root)
    except (OSError, subprocess.SubprocessError):
        return CodeVersion(sha=None, dirty=None)
    return CodeVersion(sha=sha or None, dirty=bool(status.strip()))


@dataclass(frozen=True)
class ManifestContext:
    """What the manifest records beyond the run itself.

    Attributes:
        root: The repository root; every path is written relative to it, in POSIX form.
        settings: The effective settings.
        pricing: The dated price table.
        code: The code version.
        split_sha256: SHA-256 of the split file, supplied by the caller; src never reads it.
        excel_bom: Whether the output CSV was written with a BOM.
        input_path: The input file, when the run read one.
        rate_limit_tier: The account tier: from the newest doctor evidence for a live run,
            ``unknown`` without one, None for a fake or replayed run.
        rate_limit_headers: The rate-limit headers of the run's last successful live call.
        rate_limit_source: ``last_live_call`` when headers were captured, else ``none``.
        rate_limit_tier_source: The doctor evidence file the tier came from, or None.

    """

    root: Path
    settings: Settings
    pricing: PricingTable
    code: CodeVersion
    split_sha256: str | None = None
    excel_bom: bool = False
    input_path: Path | None = None
    rate_limit_tier: str | None = None
    rate_limit_headers: Mapping[str, str] | None = None
    rate_limit_source: str = RATE_LIMIT_SOURCE_NONE
    rate_limit_tier_source: Path | None = None


def _inside_root(path: Path, root: Path) -> str | None:
    """Return a path relative to the root in POSIX form, or None when it lies outside."""
    resolved = path if path.is_absolute() else root / path
    try:
        return resolved.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def repo_relative(path: Path, root: Path) -> str:
    """Return a path as text that names no host directory, for keys and composite strings.

    Args:
        path: Any path.
        root: The repository root.

    Returns:
        The repo-relative POSIX path, or only the file name when the path lies outside the root.

    """
    inside = _inside_root(path, root)
    return path.name if inside is None else inside


def portable_path(path: Path, root: Path) -> str | dict[str, Any]:
    """Return a path as a JSON artefact records it: never absolute, never OS-specific.

    Args:
        path: Any path.
        root: The repository root.

    Returns:
        The repo-relative POSIX path; for a path outside the root, ``{"path": <file name>,
        "external": true}``.

    """
    inside = _inside_root(path, root)
    if inside is None:
        return {EXTERNAL_PATH_KEY: path.name, EXTERNAL_FLAG: True}
    return inside


def is_external(value: object) -> bool:
    """Tell whether a recorded path value is the marker of a path outside the repository.

    Args:
        value: A value read from a manifest.

    Returns:
        True for ``{"path": ..., "external": true}``.

    """
    return isinstance(value, Mapping) and value.get(EXTERNAL_FLAG) is True


def is_committable(folder: Path, root: Path) -> bool:
    """Tell whether a run folder lies under ``runs/submission/``, the only committed runs (§9.6).

    Args:
        folder: The run folder.
        root: The repository root.

    Returns:
        True when the folder resolves inside ``<root>/runs/submission``.

    """
    submission = (root / SUBMISSION_DIR).resolve()
    return (root / folder).resolve().is_relative_to(submission)


def require_inside_root(paths: Iterable[Path], root: Path) -> None:
    """Refuse any path outside the repository root, so a committed manifest names no host path.

    Args:
        paths: Paths a committed run's manifest would record.
        root: The repository root.

    Raises:
        ValueError: A path lies outside the root.

    """
    base = root.resolve()
    for path in paths:
        if not (root / path).resolve().is_relative_to(base):
            raise ValueError(
                f"{path.as_posix()} is outside the repository; a run under "
                f"{SUBMISSION_DIR.as_posix()}/ may only read files inside it"
            )


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_hashes(settings: Settings, root: Path, extra: Iterable[Path] = ()) -> dict[str, str]:
    """Hash every file under ``config/``, the glossary and any extra file (§9.6 ``config_sha256``).

    Args:
        settings: The effective settings; ``config_dir`` is hashed recursively.
        root: The repository root.
        extra: Further files the prompt read, e.g. the run's enrichment file (A68.4).

    Returns:
        Repo-relative POSIX path (a file name outside the repository) -> SHA-256, sorted.

    """
    config_dir = (
        settings.config_dir if settings.config_dir.is_absolute() else root / settings.config_dir
    )
    files = [path for path in config_dir.rglob("*") if path.is_file()]
    files.append(GLOSSARY_PATH)
    files.extend(extra)
    hashes = {repo_relative(path, root): _file_sha256(path) for path in files}
    return dict(sorted(hashes.items()))


def _jsonable(value: Any, root: Path) -> Any:
    """Convert a settings value to JSON, writing paths repo-relative in POSIX form."""
    if isinstance(value, Path):
        return portable_path(value, root)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item, root) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item, root) for item in value]
    return value


def effective_settings(settings: Settings, root: Path) -> dict[str, Any]:
    """Return the non-secret effective settings.

    Args:
        settings: The effective settings.
        root: The repository root.

    Returns:
        Every setting except the API keys and the API token, paths repo-relative.

    """
    values = settings.model_dump(exclude=set(SECRET_SETTINGS))
    return {name: _jsonable(value, root) for name, value in sorted(values.items())}


def _package_version(name: str) -> str | None:
    """Return an installed package's version, or None."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def sdk_versions(packages: Iterable[str] = SDK_PACKAGES) -> dict[str, str | None]:
    """Return the installed versions of the provider SDKs.

    Args:
        packages: Distribution names.

    Returns:
        Name -> version, None when a package is not installed.

    """
    return {name: _package_version(name) for name in packages}


def build_manifest(result: RunResult, context: ManifestContext) -> dict[str, Any]:
    """Complete the run's manifest with the environment fields (§9.6).

    The rate-limit headers are null, with ``rate_limit_source: none``, when the run made no
    successful live call that carried them (a fake, replayed, fully cached or OpenAI run).
    ``enrichment_path`` names the arm C enrichment file the run rendered, portably, or is null;
    its hash enters ``config_sha256`` beside the glossary's (A68.4). ``fallback_enrichment_path``
    does the same for the file the A33 fallback's lines rendered once it rescued the run.

    Args:
        result: The run.
        context: The environment of the run.

    Returns:
        Every manifest field; unknown values are None.

    """
    root = context.root
    input_path, tier_source = context.input_path, context.rate_limit_tier_source
    enrichment, fallback = result.enrichment_path, result.fallback_enrichment_path
    extra = tuple(path for path in (enrichment, fallback) if path is not None)
    return {
        **result.manifest,
        "code_sha": context.code.sha,
        "code_dirty": context.code.dirty,
        "split_sha256": context.split_sha256,
        "price_date": context.pricing.price_date.isoformat(),
        "sdk_versions": sdk_versions(),
        "otel_semconv_version": OTEL_GENAI_SEMCONV_VERSION,
        "rate_limit_tier": context.rate_limit_tier,
        "rate_limit_headers": dict(context.rate_limit_headers or {}) or None,
        "rate_limit_source": context.rate_limit_source,
        "rate_limit_tier_source": portable_path(tier_source, root) if tier_source else None,
        "config_sha256": config_hashes(context.settings, root, extra),
        "settings_effective": effective_settings(context.settings, root),
        "excel_bom": context.excel_bom,
        "input_path": portable_path(input_path, root) if input_path else None,
        "enrichment_path": portable_path(enrichment, root) if enrichment else None,
        "fallback_enrichment_path": portable_path(fallback, root) if fallback else None,
    }


def run_folder(runs_dir: Path, run_id: str) -> Path:
    """Return ``runs_dir/<run_id>``, refusing an id that is not a plain folder name.

    Args:
        runs_dir: The ``runs/`` directory.
        run_id: The run id.

    Returns:
        The run's folder.

    Raises:
        ValueError: The id holds a path separator or another unsafe character.

    """
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"run id {run_id!r} is not a plain folder name")
    return runs_dir / run_id


def _json_lines(records: Iterable[str]) -> str:
    """Join JSON records, one per LF-terminated line."""
    return "".join(record + JSON_NEWLINE for record in records)


def _write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line endings on every platform."""
    path.write_bytes(text.encode(TEXT_ENCODING))


def write_run(result: RunResult, runs_dir: Path, manifest: Mapping[str, Any]) -> Path:
    """Write ``runs/<run_id>/`` for a run.

    Args:
        result: The run.
        runs_dir: The ``runs/`` directory.
        manifest: The full manifest, e.g. from ``build_manifest``.

    Returns:
        The run's folder.

    """
    folder = run_folder(runs_dir, result.run_id)
    prompts = folder / PROMPTS_DIR
    prompts.mkdir(parents=True, exist_ok=True)
    manifest_text = json.dumps(manifest, sort_keys=True, indent=JSON_INDENT, ensure_ascii=False)
    _write_text(folder / MANIFEST_FILE, manifest_text + JSON_NEWLINE)
    _write_text(folder / CALLS_FILE, _json_lines(record_line(call) for call in result.calls))
    audit = (json.dumps(record, sort_keys=True, ensure_ascii=False) for record in result.audit)
    _write_text(folder / AUDIT_FILE, _json_lines(audit))
    for sha, text in sorted(result.system_prompts.items()):
        _write_text(prompts / f"{sha}{PROMPT_SUFFIX}", text)
    return folder
