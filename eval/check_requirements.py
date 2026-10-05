"""Gate a matcher output and its run folder on the eleven requirement checks (DESIGN.md §11.7).

Every output first goes through ``eval/score.py --strict`` (loaded by path), then RQ1-RQ11 of
the evaluation protocol §8 run against the output CSV, the input, the library, the run folder
(``manifest.json``, ``calls.jsonl``, ``audit.jsonl``) and ``config/``. Configuration, the
reader and the line normaliser are reused from ``oris_matcher`` so the checks read the same
files the service reads; nothing here reads a split or annotation file.

Usage::

    python eval/check_requirements.py --output OUT.csv --input IN.csv --library LIB.csv
        [--run runs/<run_id>] [--reference data/boq_dataset_matched_GT.csv] [--config config]
        [--oris oris] [--no-skips] [--json report.json]

RQ6-RQ10 need ``--run`` and are skipped without it; RQ7 and RQ8 are skipped unless the
manifest is a cold live run (``mode = live``, ``cache_hits = 0``); RQ11 runs
``oris replay <run> --check <output>`` and reports ``skipped: no B3 run`` without ``--run``.
Exit code 0 when nothing failed, 1 on any failure (or any skip under ``--no-skips``), 2 when
the output or the input cannot be read.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import math
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from oris_matcher.domain.boq import BoqLine
from oris_matcher.domain.decision import LLM_FAILURE_PREFIX, ReasonCode, is_service_unit
from oris_matcher.domain.library import LibraryError, load_library
from oris_matcher.domain.normalize import normalize
from oris_matcher.io.boq_reader import BoqFormatError, read_boq
from oris_matcher.settings import (
    MODELS_FILE,
    SERVICE_UNITS_FILE,
    SUPPLY_MARKERS_FILE,
    UNIT_ALIASES_FILE,
    ConfigError,
    is_model_allowed,
    load_models_config,
    load_service_units,
    load_supply_markers,
    load_unit_aliases,
)

ROOT = Path(__file__).resolve().parents[1]
SCORE_PATH = Path(__file__).resolve().with_name("score.py")
SCORE_MODULE = "oris_eval_score"
DEFAULT_REFERENCE = ROOT / "data" / "boq_dataset_matched_GT.csv"
DEFAULT_CONFIG = ROOT / "config"
DEFAULT_ORIS = ("oris",)
ENCODING = "utf-8"
BOM = b"\xef\xbb\xbf"
CRLF = "\r\n"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2
NO_B3_RUN = "skipped: no B3 run"
NO_RUN = "skipped: no run folder (--run)"
DETAIL_LIMIT = 5
REPLAY_TIMEOUT_S = 600
STDERR_TAIL_CHARS = 400
JSON_INDENT = 2
JSON_NEWLINE = "\n"

DECISION_COLUMNS = ("decision", "material_type", "material_usage", "material_subtype")
AUDIT_COLUMNS = ("reason", "model", "prompt_version", "latency_ms", "cost_usd")
SUGGESTION_COLUMNS = (
    "suggested_type",
    "suggested_usage",
    "suggested_subtype",
    "library_row_id",
    "call_ids",
)
SECOND_SUGGESTION_COLUMNS = ("suggested2_type", "suggested2_usage", "suggested2_subtype")
APPENDED_COLUMNS = (
    *DECISION_COLUMNS,
    *AUDIT_COLUMNS,
    *SUGGESTION_COLUMNS,
    *SECOND_SUGGESTION_COLUMNS,
)
LABEL_COLUMNS = DECISION_COLUMNS[1:]
CALL_ID_SEPARATOR = ";"
MATCHED = "matched"
NOT_A_MATERIAL = "not_a_material"
NOT_A_MATERIAL_REASONS = frozenset({ReasonCode.HEADER, ReasonCode.EMPTY_ROW, ReasonCode.G2_SERVICE})
UNANSWERED_REASONS = frozenset({ReasonCode.LLM_UNAVAILABLE, ReasonCode.BUDGET_CAP})

LIVE_MODE = "live"
RUN_MODES = frozenset({LIVE_MODE, "cached", "replay", "fake", "rules"})
MAX_COST_PER_100_LINES_USD = 2.0
LINES_PER_COST_UNIT = 100
MAX_SECONDS_PER_ROUTED_LINE = 2.0
COST_CELL_ROUNDING_USD = 5e-7
FLOAT_TOLERANCE = 1e-9
SPEND_TOLERANCE_USD = 1e-6

MANIFEST_PINS = (
    "code_sha",
    "requested_model",
    "prompt_version",
    "library_sha256",
    "split_sha256",
    "price_date",
    "mode",
    "config_sha256",
    "enrichment_sha256",
    "otel_semconv_version",
    "rate_limit_tier",
    "rate_limit_headers",
    "rate_limit_source",
    "policy_resolution",
)
NULLABLE_PINS = frozenset({"enrichment_sha256"})
NON_LIVE_NULLABLE_PINS = frozenset({"rate_limit_tier", "rate_limit_headers", "split_sha256"})
UNKNOWN_TIER = "unknown"
GLOSSARY_NAME = "glossary.yaml"
REQUEST_MODEL = "gen_ai.request.model"
RESPONSE_MODEL = "gen_ai.response.model"
FINISH_REASONS = "gen_ai.response.finish_reasons"
CALL_FIELDS = (
    REQUEST_MODEL,
    RESPONSE_MODEL,
    "raw_response",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "cost_usd",
    "latency_ms",
)
SUCCESS_FIELDS = ("provider_request_id", FINISH_REASONS)


class InputError(Exception):
    """The output or the input cannot be read, so no check can run."""


class CheckFailedError(Exception):
    """A check found a violation; the message is its detail."""


class CheckSkippedError(Exception):
    """A check cannot apply to these inputs; the message is its detail."""


def load_score() -> ModuleType:
    """Load ``eval/score.py`` by path, reusing an already loaded copy."""
    if SCORE_MODULE in sys.modules:
        return sys.modules[SCORE_MODULE]
    spec = importlib.util.spec_from_file_location(SCORE_MODULE, SCORE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {SCORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[SCORE_MODULE] = module
    spec.loader.exec_module(module)
    return module


score = load_score()


@dataclass(frozen=True)
class Inputs:
    """What the checks run against.

    Attributes:
        output: The output CSV.
        input: The BoQ input the output was produced from.
        library: The library CSV the run used.
        reference: The reference the strict scorer joins against.
        run: The run folder, or None.
        config_dir: The ``config/`` directory.
        oris: The command that starts the ``oris`` CLI, for RQ11.

    """

    output: Path
    input: Path
    library: Path
    reference: Path
    run: Path | None
    config_dir: Path
    oris: tuple[str, ...]


@dataclass(frozen=True)
class Result:
    """One row of the pass/fail table.

    Attributes:
        rq: Check id, ``STRICT`` or ``RQ1``-``RQ11``.
        status: ``PASS``, ``FAIL`` or ``SKIP``.
        detail: What was checked or what went wrong.

    """

    rq: str
    status: str
    detail: str


@dataclass(frozen=True)
class RunFolder:
    """The three run records.

    Attributes:
        manifest: ``manifest.json``.
        calls: ``calls.jsonl``, one record per attempt.
        audit: ``audit.jsonl``, one record per line.

    """

    manifest: dict[str, Any]
    calls: list[dict[str, Any]]
    audit: list[dict[str, Any]]


@dataclass(frozen=True)
class Context:
    """Everything loaded once and shared by the checks.

    Attributes:
        inputs: The paths.
        output_bytes: The output file's bytes.
        header: The output header row.
        rows: The output data rows, raw strings.
        input_header: The input header row.
        lines: The input lines.
        folder: The run folder, when it was given and could be read.
        run_error: Why the run folder could not be read, or None.

    """

    inputs: Inputs
    output_bytes: bytes
    header: list[str]
    rows: list[list[str]]
    input_header: tuple[str, ...]
    lines: tuple[BoqLine, ...]
    folder: RunFolder | None
    run_error: str | None

    def run_folder(self) -> RunFolder:
        """Return the run folder; skip without one, fail when it was unreadable."""
        if self.inputs.run is None:
            raise CheckSkippedError(NO_RUN)
        if self.folder is None:
            raise CheckFailedError(self.run_error or "run folder unreadable")
        return self.folder

    def column(self, name: str) -> int:
        """Return the index of an output column, failing the check when it is absent."""
        if name not in self.header:
            raise CheckFailedError(f"output has no {name!r} column")
        return self.header.index(name)

    def values(self, name: str) -> list[str]:
        """Return one output column's raw cells, row by row."""
        index = self.column(name)
        return [row[index] if index < len(row) else "" for row in self.rows]


# --- loading ---------------------------------------------------------------------------


def read_json(path: Path) -> Any:
    """Parse a JSON file, failing the check on an unreadable file."""
    try:
        return json.loads(path.read_text(encoding=ENCODING))
    except (OSError, ValueError) as error:
        raise CheckFailedError(f"{path}: {error}") from error


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSON-lines file of objects, failing the check on any bad line."""
    try:
        lines = path.read_text(encoding=ENCODING).splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError) as error:
        raise CheckFailedError(f"{path}: {error}") from error
    if not all(isinstance(record, dict) for record in records):
        raise CheckFailedError(f"{path}: every line must be a JSON object")
    return records


def load_run(run: Path) -> RunFolder:
    """Read ``manifest.json``, ``calls.jsonl`` and ``audit.jsonl`` from a run folder."""
    manifest = read_json(run / "manifest.json")
    if not isinstance(manifest, dict):
        raise CheckFailedError(f"{run / 'manifest.json'}: not a JSON object")
    return RunFolder(
        manifest=manifest,
        calls=read_jsonl(run / "calls.jsonl"),
        audit=read_jsonl(run / "audit.jsonl"),
    )


def try_load_run(run: Path | None) -> tuple[RunFolder | None, str | None]:
    """Load the run folder when given; return it, or the reason it could not be read."""
    if run is None:
        return None, None
    try:
        return load_run(run), None
    except CheckFailedError as error:
        return None, str(error)


def build_context(inputs: Inputs) -> Context:
    """Read the output, the input and the run folder once."""
    try:
        output_bytes = inputs.output.read_bytes()
        table = score.read_table(inputs.output)
        boq = read_boq(inputs.input)
    except (OSError, BoqFormatError, score.ScoreError) as error:
        raise InputError(str(error)) from error
    folder, run_error = try_load_run(inputs.run)
    return Context(
        inputs=inputs,
        output_bytes=output_bytes,
        header=table.header,
        rows=table.rows,
        input_header=boq.header,
        lines=boq.lines,
        folder=folder,
        run_error=run_error,
    )


def sha256_of(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarise(problems: Sequence[str]) -> str:
    """Join the first problems, with a count of the rest."""
    shown = "; ".join(problems[:DETAIL_LIMIT])
    extra = len(problems) - DETAIL_LIMIT
    return shown + (f"; ... (+{extra} more)" if extra > 0 else "")


def fail_on(problems: Sequence[str]) -> None:
    """Fail the check when there is any problem."""
    if problems:
        raise CheckFailedError(summarise(problems))


# --- STRICT, RQ1-RQ4: the output file --------------------------------------------------


def check_strict(context: Context) -> str:
    """Score the output with ``score.py --strict`` against the reference."""
    settings = score.Settings(
        jobs=[score.Job(label="output", path=context.inputs.output)],
        reference=context.inputs.reference,
        key=None,
        strict=True,
        join=score.JOIN_KEY,
        split=None,
        side=score.SIDE_ALL,
        classes=None,
        json_path=None,
    )
    try:
        score.run(settings)
    except score.ScoreError as error:
        raise CheckFailedError(f"score.py --strict: {error}") from error
    return f"score.py --strict accepted the output against {context.inputs.reference.name}"


def check_columns(context: Context) -> str:
    """RQ1: the input columns in order, then the §9.6 columns and the A56 trio."""
    expected = [*context.input_header, *APPENDED_COLUMNS]
    if context.header == expected:
        return f"{len(expected)} columns in the §9.6 + A56 order"
    for index, (seen, wanted) in enumerate(zip(context.header, expected, strict=False)):
        if seen != wanted:
            raise CheckFailedError(f"column {index + 1} is {seen!r}, expected {wanted!r}")
    raise CheckFailedError(f"{len(context.header)} columns, expected {len(expected)}")


def reserialised(header: list[str], rows: list[list[str]]) -> bytes:
    """Write the parsed rows back with the csv module: CRLF, QUOTE_MINIMAL, UTF-8."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator=CRLF, quoting=csv.QUOTE_MINIMAL).writerows([header, *rows])
    return buffer.getvalue().encode(ENCODING)


def byte_problems(context: Context) -> list[str]:
    """RQ2 byte checks: no BOM, and the bytes a CRLF QUOTE_MINIMAL writer produces."""
    if context.output_bytes.startswith(BOM):
        return ["output starts with a UTF-8 BOM"]
    if context.output_bytes != reserialised(context.header, context.rows):
        return ["output is not CRLF / QUOTE_MINIMAL csv-module output"]
    return []


def cell_problems(context: Context) -> list[str]:
    """RQ2 cell checks: the same rows, in order, with every input cell unchanged."""
    if len(context.rows) != len(context.lines):
        return [f"{len(context.rows)} output rows, {len(context.lines)} input rows"]
    width = len(context.input_header)
    return [
        f"row {line.position + 1} ({line.item_no!r}): input cells changed"
        for line, row in zip(context.lines, context.rows, strict=True)
        if tuple(row[:width]) != line.raw_row
    ]


def check_rows(context: Context) -> str:
    """RQ2: row count, order and input cells identical; CRLF and no BOM."""
    fail_on(byte_problems(context) + cell_problems(context))
    return f"{len(context.rows)} rows identical to the input; CRLF, no BOM"


def check_decisions(context: Context) -> str:
    """RQ3: every decision is one of the three values, never blank."""
    decisions = context.values("decision")
    bad = Counter(value for value in decisions if value not in score.DECISIONS)
    fail_on([f"{count} row(s) with decision {value!r}" for value, count in bad.items()])
    return f"{len(decisions)} decisions in {{{', '.join(score.DECISIONS)}}}"


def library_triples(path: Path) -> frozenset[tuple[str, str, str]]:
    """Return the library's raw (type, usage, subtype) triples."""
    try:
        library = load_library(path.read_bytes())
    except (OSError, LibraryError) as error:
        raise CheckFailedError(f"library {path}: {error}") from error
    return frozenset(
        (row.material_type, row.material_usage, row.material_subtype) for row in library.rows
    )


def label_rows(context: Context) -> list[tuple[str, tuple[str, str, str]]]:
    """Return each row's decision and raw label triple."""
    decisions = context.values("decision")
    columns = [context.values(name) for name in LABEL_COLUMNS]
    return [
        (decision, (labels[0], labels[1], labels[2]))
        for decision, *labels in zip(decisions, *columns, strict=True)
    ]


def check_closed_world(context: Context) -> str:
    """RQ4: matched triples are library rows verbatim; other rows carry no labels."""
    triples = library_triples(context.inputs.library)
    problems: list[str] = []
    for index, (decision, labels) in enumerate(label_rows(context), 1):
        if decision == MATCHED and labels not in triples:
            problems.append(f"row {index}: matched triple {labels!r} is not a library row")
        elif decision != MATCHED and any(labels):
            problems.append(f"row {index}: {decision} row carries labels")
    fail_on(problems)
    return f"every matched triple is one of {len(triples)} library rows; other rows blank"


# --- RQ5: not_a_material provenance -----------------------------------------------------


def supply_marker_in(text: str, markers: Iterable[str]) -> bool:
    """Tell whether a supply marker occurs in the normalised text as a whole word or phrase.

    Args:
        text: Raw line text.
        markers: Markers from ``supply_markers.yaml``.

    Returns:
        True when any normalised marker occurs between non-word boundaries.

    """
    haystack = normalize(text)
    return any(
        re.search(rf"(?<!\w){re.escape(normalize(marker))}(?!\w)", haystack) for marker in markers
    )


@dataclass(frozen=True)
class G2Config:
    """The configuration gate G2 reads.

    Attributes:
        units: Service units, compared exactly.
        canonical_of: Maps a unit alias to its canonical unit.
        markers: Supply markers.

    """

    units: frozenset[str]
    canonical_of: Callable[[str], str]
    markers: tuple[str, ...]


def load_g2(config_dir: Path) -> G2Config:
    """Load the service units, unit aliases and supply markers."""
    try:
        units = load_service_units(config_dir / SERVICE_UNITS_FILE)
        aliases = load_unit_aliases(config_dir / UNIT_ALIASES_FILE)
        markers = load_supply_markers(config_dir / SUPPLY_MARKERS_FILE)
    except ConfigError as error:
        raise CheckFailedError(str(error)) from error
    return G2Config(units=units.units, canonical_of=aliases.canonical_of, markers=markers.markers)


def g2_problem(line: BoqLine, g2: G2Config) -> str | None:
    """Return why a ``G2_SERVICE`` skip is not backed by its line, or None."""
    unit = line.unit.strip()
    if not (is_service_unit(unit, g2.units) or g2.canonical_of(unit) in g2.units):
        return f"G2_SERVICE on measured unit {line.unit!r}"
    if supply_marker_in(f"{line.short} {line.long}", g2.markers):
        return "G2_SERVICE on a line with a supply marker"
    return None


def nam_problem(index: int, reason: str, context: Context, g2: G2Config) -> str | None:
    """Return why one ``not_a_material`` row breaks RQ5, or None."""
    if reason not in NOT_A_MATERIAL_REASONS:
        return f"row {index + 1}: not_a_material with reason {reason!r}"
    if reason != ReasonCode.G2_SERVICE:
        return None
    if index >= len(context.lines):
        return f"row {index + 1}: no input line"
    problem = g2_problem(context.lines[index], g2)
    return None if problem is None else f"row {index + 1}: {problem}"


def check_not_a_material(context: Context) -> str:
    """RQ5: ``not_a_material`` only from HEADER, EMPTY_ROW or a backed G2_SERVICE."""
    g2 = load_g2(context.inputs.config_dir)
    pairs = zip(context.values("decision"), context.values("reason"), strict=True)
    skipped = [(index, reason) for index, (decision, reason) in enumerate(pairs)
               if decision == NOT_A_MATERIAL]  # fmt: skip
    problems = [nam_problem(index, reason, context, g2) for index, reason in skipped]
    fail_on([problem for problem in problems if problem])
    return f"{len(skipped)} not_a_material row(s), all HEADER / EMPTY_ROW / backed G2_SERVICE"


# --- RQ6: audit and call records --------------------------------------------------------


def audit_order_problems(context: Context, audit: list[dict[str, Any]]) -> list[str]:
    """Check that audit records map 1:1, in order, onto the input line ids."""
    seen = [record.get("line_id") for record in audit]
    expected = [line.line_id for line in context.lines]
    if seen == expected:
        return []
    duplicates = sum(count - 1 for count in Counter(seen).values() if count > 1)
    missing = len(set(expected) - set(seen))
    extra = len(set(seen) - set(expected))
    return [
        f"audit.jsonl has {len(seen)} records for {len(expected)} lines "
        f"({missing} missing, {extra} unknown, {duplicates} duplicate, or out of order)"
    ]


def is_answered(reason: str) -> bool:
    """Whether a line's decision came from a model answer (not a failure or no call)."""
    return not (reason.startswith(LLM_FAILURE_PREFIX) or reason in UNANSWERED_REASONS)


def row_call_ids(cell_text: str, record: dict[str, Any] | None) -> set[str]:
    """Return the call ids an output row and its audit record reference."""
    ids = {part for part in cell_text.split(CALL_ID_SEPARATOR) if part}
    if record is not None:
        ids |= {str(call_id) for call_id in record.get("call_ids") or ()}
    return ids


def line_problems(context: Context, folder: RunFolder) -> list[str]:
    """Check call references and ``raw_line_response`` on LLM-derived lines."""
    known = {str(call.get("call_id")) for call in folder.calls}
    reasons = context.values("reason")
    cells = context.values("call_ids")
    problems: list[str] = []
    for index, (reason, cell_text) in enumerate(zip(reasons, cells, strict=True)):
        record = folder.audit[index] if index < len(folder.audit) else None
        ids = row_call_ids(cell_text, record)
        if ids - known:
            problems.append(f"row {index + 1}: unknown call id(s) {sorted(ids - known)}")
        if ids and is_answered(reason) and not (record or {}).get("raw_line_response"):
            problems.append(f"row {index + 1}: LLM-derived line without raw_line_response")
    return problems


def call_problems(call: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Check one call record's fields; successful calls need every value non-null."""
    name = call.get("call_id")
    missing = [field for field in CALL_FIELDS if field not in call]
    if "prompt_version" not in call and manifest.get("prompt_version") is None:
        missing.append("prompt_version")
    if call.get("error_class") is None:
        missing += [field for field in CALL_FIELDS if field in call and call[field] is None]
        missing += [field for field in SUCCESS_FIELDS if not call.get(field)]
    return [f"call {name}: missing or null {', '.join(missing)}"] if missing else []


def check_audit(context: Context) -> str:
    """RQ6: one audit record per line, and complete call records."""
    folder = context.run_folder()
    problems = audit_order_problems(context, folder.audit) + line_problems(context, folder)
    for call in folder.calls:
        problems += call_problems(call, folder.manifest)
    fail_on(problems)
    return f"{len(folder.audit)} audit records 1:1 with lines; {len(folder.calls)} call records"


# --- RQ7-RQ8: gated cost and latency ---------------------------------------------------


def gated_manifest(context: Context) -> dict[str, Any]:
    """Return the manifest of a cold live run; skip any other run."""
    manifest = context.run_folder().manifest
    mode, hits = manifest.get("mode"), manifest.get("cache_hits")
    if mode != LIVE_MODE or hits != 0:
        raise CheckSkippedError(
            f"skipped (not a cold live run): mode={mode!r}, cache_hits={hits!r}"
        )
    return manifest


def number(record: dict[str, Any], name: str) -> float:
    """Return a numeric field, failing the check when it is absent or not a number."""
    value = record.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise CheckFailedError(f"{name} missing or not a number: {value!r}")
    return float(value)


def line_cost_total(context: Context) -> float:
    """Sum the output's per-line ``cost_usd`` cells."""
    try:
        return math.fsum(float(value) for value in context.values("cost_usd"))
    except ValueError as error:
        raise CheckFailedError(f"cost_usd cell is not a number: {error}") from error


def check_cost(context: Context) -> str:
    """RQ7: cost per 100 lines within budget; line costs sum to the run total."""
    manifest = gated_manifest(context)
    calls_total = math.fsum(number(call, "cost_usd") for call in context.run_folder().calls)
    spend = number(manifest, "spend_usd")
    lines_total = line_cost_total(context)
    tolerance = len(context.rows) * COST_CELL_ROUNDING_USD + FLOAT_TOLERANCE
    per_100 = spend / max(len(context.rows), 1) * LINES_PER_COST_UNIT
    problems: list[str] = []
    if abs(lines_total - calls_total) > tolerance:
        problems.append(f"line costs sum to {lines_total:.6f}, calls to {calls_total:.6f}")
    if abs(spend - calls_total) > SPEND_TOLERANCE_USD:
        problems.append(f"manifest spend_usd {spend:.6f} != calls total {calls_total:.6f}")
    if per_100 > MAX_COST_PER_100_LINES_USD:
        problems.append(f"${per_100:.4f} per 100 lines > ${MAX_COST_PER_100_LINES_USD:.2f}")
    fail_on(problems)
    return f"${per_100:.4f} per 100 lines; line costs sum to the run total {calls_total:.6f}"


def check_latency(context: Context) -> str:
    """RQ8: wall-clock per routed line of a cold live run within the gate."""
    manifest = gated_manifest(context)
    wall = number(manifest, "wall_clock_s")
    routed = number(manifest, "n_routed")
    if routed <= 0:
        raise CheckFailedError(f"n_routed must be positive, got {routed:g}")
    per_line = wall / routed
    if per_line > MAX_SECONDS_PER_ROUTED_LINE:
        raise CheckFailedError(
            f"{per_line:.3f} s per routed line > {MAX_SECONDS_PER_ROUTED_LINE:.1f} s"
        )
    return f"{per_line:.3f} s per routed line over {routed:g} routed lines"


# --- RQ9-RQ10: manifest and models -----------------------------------------------------


def config_keys(config_dir: Path) -> list[str]:
    """Return the ``config_sha256`` keys every file under ``config/`` needs."""
    return [
        f"{config_dir.name}/{path.relative_to(config_dir).as_posix()}"
        for path in sorted(config_dir.rglob("*"))
        if path.is_file()
    ]


def config_problems(manifest: dict[str, Any], config_dir: Path) -> list[str]:
    """Check that ``config_sha256`` covers ``config/*`` and the glossary."""
    shas = manifest.get("config_sha256")
    if not isinstance(shas, dict):
        return ["config_sha256 is not an object"]
    missing = [key for key in config_keys(config_dir) if not shas.get(key)]
    if not any(str(key).endswith(GLOSSARY_NAME) and shas[key] for key in shas):
        missing.append(GLOSSARY_NAME)
    return [f"config_sha256 lacks {', '.join(missing)}"] if missing else []


def fallback_problems(manifest: dict[str, Any], calls: list[dict[str, Any]]) -> list[str]:
    """Require ``fallback_model`` when a call requested a model other than the manifest's."""
    others = {call.get(REQUEST_MODEL) for call in calls} - {manifest.get("requested_model"), None}
    if others and not manifest.get("fallback_model"):
        return [f"calls used {sorted(map(str, others))} but fallback_model is not pinned"]
    return []


def nullable_pins(manifest: dict[str, Any]) -> frozenset[str]:
    """Return the pins that may be null; their keys must still be present.

    A run whose mode is not ``live`` (fake, replay or cached) may have seen no live
    rate-limit header and need not belong to a split, so its tier, headers and split hash may
    be null.
    """
    if manifest.get("mode") != LIVE_MODE:
        return NULLABLE_PINS | NON_LIVE_NULLABLE_PINS
    return NULLABLE_PINS


def mode_problems(manifest: dict[str, Any]) -> list[str]:
    """Refuse a mode the service never writes: live, cached, replay, fake, or rules (B0, A58)."""
    mode = manifest.get("mode")
    if "mode" in manifest and mode not in RUN_MODES:
        return [f"mode {mode!r} is not one of {', '.join(sorted(RUN_MODES))}"]
    return []


def tier_problems(manifest: dict[str, Any]) -> list[str]:
    """Refuse an ``unknown`` rate-limit tier on a live run; other modes may not know it."""
    if manifest.get("mode") == LIVE_MODE and manifest.get("rate_limit_tier") == UNKNOWN_TIER:
        return ["a live run's rate_limit_tier is unknown: run oris doctor --live first"]
    return []


def check_manifest(context: Context) -> str:
    """RQ9: every pin is present, config hashes are complete and the library matches."""
    folder = context.run_folder()
    manifest = folder.manifest
    nullable = nullable_pins(manifest)
    problems = [
        f"manifest lacks {name}"
        for name in MANIFEST_PINS
        if name not in manifest or (manifest[name] is None and name not in nullable)
    ]
    problems += mode_problems(manifest)
    problems += tier_problems(manifest)
    problems += config_problems(manifest, context.inputs.config_dir)
    if manifest.get("library_sha256") != sha256_of(context.inputs.library):
        problems.append("library_sha256 is not the SHA-256 of --library")
    problems += fallback_problems(manifest, folder.calls)
    fail_on(problems)
    detail = f"{len(MANIFEST_PINS)} pins present; config_sha256 complete; library hash matches"
    relaxed = sorted(nullable - NULLABLE_PINS)
    if relaxed:
        mode = manifest.get("mode")
        detail += f" (non-live {mode} run: {', '.join(relaxed)} may be null or unknown)"
    return detail


def run_models(folder: RunFolder) -> set[str]:
    """Every requested and served model in the calls, plus the manifest's models."""
    models = {call.get(name) for call in folder.calls for name in (REQUEST_MODEL, RESPONSE_MODEL)}
    manifest = folder.manifest
    models |= {manifest.get("requested_model"), manifest.get("fallback_model")}
    models |= set(manifest.get("served_models") or ())
    return {str(model) for model in models if model}


def served_anything(folder: RunFolder) -> bool:
    """Whether the run made a call or recorded a served model; a 0-call run did neither."""
    return bool(folder.calls) or bool(folder.manifest.get("served_models"))


def check_allowlist(context: Context) -> str:
    """RQ10: every model the run requested, served or configured is on the allowlist.

    A run with no call record and no served model still has its configured models checked;
    its detail says that nothing was served.
    """
    try:
        config = load_models_config(context.inputs.config_dir / MODELS_FILE)
    except ConfigError as error:
        raise CheckFailedError(str(error)) from error
    models = run_models(context.run_folder())
    refused = sorted(
        model for model in models if not is_model_allowed(model, config.allowlist.patterns)
    )
    fail_on([f"model {model!r} is not on the allowlist" for model in refused])
    listed = ", ".join(sorted(models))
    if not served_anything(context.run_folder()):
        return f"no served model (0 calls); configured on the allowlist: {listed}"
    return f"{len(models)} model(s) on the allowlist: {listed}"


# --- RQ11: replay ----------------------------------------------------------------------


def check_replay(context: Context) -> str:
    """RQ11: ``oris replay <run> --check <output>`` reports a byte-identical output."""
    run = context.inputs.run
    if run is None:
        raise CheckSkippedError(NO_B3_RUN)
    command = [*context.inputs.oris, "replay", str(run), "--check", str(context.inputs.output)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding=ENCODING,
            errors="replace",
            timeout=REPLAY_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckFailedError(f"cannot run {command[0]!r}: {error}") from error
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout).strip()[-STDERR_TAIL_CHARS:]
        raise CheckFailedError(
            f"oris replay exited {completed.returncode}" + (f": {tail}" if tail else "")
        )
    return "oris replay --check: byte-identical"


# --- orchestration ---------------------------------------------------------------------


CHECKS: tuple[tuple[str, Callable[[Context], str]], ...] = (
    ("STRICT", check_strict),
    ("RQ1", check_columns),
    ("RQ2", check_rows),
    ("RQ3", check_decisions),
    ("RQ4", check_closed_world),
    ("RQ5", check_not_a_material),
    ("RQ6", check_audit),
    ("RQ7", check_cost),
    ("RQ8", check_latency),
    ("RQ9", check_manifest),
    ("RQ10", check_allowlist),
    ("RQ11", check_replay),
)


def run_one(rq: str, check: Callable[[Context], str], context: Context) -> Result:
    """Run one check and turn its outcome into a table row."""
    try:
        return Result(rq=rq, status=PASS, detail=check(context))
    except CheckFailedError as error:
        return Result(rq=rq, status=FAIL, detail=str(error))
    except CheckSkippedError as error:
        return Result(rq=rq, status=SKIP, detail=str(error))


def run_checks(inputs: Inputs) -> list[Result]:
    """Run STRICT and RQ1-RQ11.

    Args:
        inputs: The paths to check.

    Returns:
        One result per check, in order.

    Raises:
        InputError: The output or the input cannot be read.

    """
    context = build_context(inputs)
    return [run_one(rq, check, context) for rq, check in CHECKS]


def render(results: Sequence[Result]) -> str:
    """Render the pass/fail table."""
    width = max(len(result.rq) for result in results)
    lines = [f"{'check'.ljust(width)}  status  detail"]
    lines += [
        f"{result.rq.ljust(width)}  {result.status.ljust(6)}  {result.detail}" for result in results
    ]
    return "\n".join(lines) + "\n"


def exit_code(results: Sequence[Result], no_skips: bool) -> int:
    """0 when nothing failed; 1 on a failure, or on a skip under ``--no-skips``."""
    bad = {FAIL, SKIP} if no_skips else {FAIL}
    return EXIT_FAIL if any(result.status in bad for result in results) else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--config", dest="config_dir", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--oris", nargs="+", default=list(DEFAULT_ORIS))
    parser.add_argument("--no-skips", action="store_true")
    parser.add_argument("--json", dest="json_path", type=Path)
    return parser


def write_json(path: Path, results: Sequence[Result], code: int) -> None:
    """Write the results as JSON."""
    payload = {
        "checks": [vars(result) for result in results],
        "passed": code == EXIT_OK,
    }
    text = json.dumps(payload, sort_keys=True, indent=JSON_INDENT, ensure_ascii=False)
    path.write_bytes((text + JSON_NEWLINE).encode(ENCODING))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the checks; print the table and return the exit code."""
    score.use_utf8_stdout()
    args = build_parser().parse_args(argv)
    inputs = Inputs(
        output=args.output,
        input=args.input,
        library=args.library,
        reference=args.reference,
        run=args.run,
        config_dir=args.config_dir,
        oris=tuple(args.oris),
    )
    try:
        results = run_checks(inputs)
    except InputError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    sys.stdout.write(render(results))
    code = exit_code(results, args.no_skips)
    if args.json_path is not None:
        write_json(args.json_path, results, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
