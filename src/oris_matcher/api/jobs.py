"""Async BoQ jobs for the operator UI (docs/ui-spec.md §2-§4, DESIGN.md §13).

``POST /v1/jobs`` takes a multipart upload (``file``: ``.csv`` or ``.xlsx``, at most 2 MB and
2,000 data rows; ``library``: an id from ``Settings.libraries``, ``global`` by default),
parses it on the server with the CLI's reader and answers ``202`` with a job id before any
model call. Jobs run one at a time, in submission order, through the same wiring as
``POST /v1/match`` (``ApiState.run``), so a job writes the same run folder and its
``result.csv`` is the CLI writer's output for that run. The store is in memory, in a single
worker: at most four jobs wait (a fifth is ``429 queue_full``), a finished job's result is
dropped an hour later (``404 job_expired``), at most 20 jobs are kept (the oldest finished one
expires first), waiting and running jobs are cancelled on shutdown, and nothing survives a
restart. Errors are ``{error, detail}``.

The body is read with a running byte count, so a chunked upload with no ``Content-Length`` is
refused once it passes the limit, and the multipart parts are kept in memory, never spooled to a
temporary file. A failed job reports a code and a message with no server path; the full error
goes to the server log. ``GET /v1/serving`` says how the server answers (``live``, ``replay`` of
a recorded run, or the offline ``fake``), its model concurrency and the libraries it runs.
"""

import asyncio
import hashlib
import logging
import re
import secrets
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePath
from typing import Any, Final

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from oris_matcher.api.job_views import iso, library_record, result_record, summary_record
from oris_matcher.api.progress import RunProgress
from oris_matcher.doctor import WiringError
from oris_matcher.domain.boq import BoqFile, PathMode
from oris_matcher.io.boq_reader import BoqFormatError, MissingColumnsError
from oris_matcher.io.writer import render_csv
from oris_matcher.io.xlsx_reader import (
    EmptyBoqError,
    TooManyRowsError,
    UnsupportedTypeError,
    UploadedBoq,
    WorkbookTooLargeError,
    read_upload,
)
from oris_matcher.service import (
    MatchService,
    PolicyQuery,
    RunResult,
    as_override,
    resolve_policy,
)
from oris_matcher.settings import ConfigError

MAX_UPLOAD_BYTES: Final = 2 * 1024 * 1024
MAX_ROWS: Final = 2000
MAX_QUEUED: Final = 4
MAX_KEPT_JOBS: Final = 20
MAX_EXPIRED_IDS: Final = 1000
RESULT_TTL: Final = timedelta(hours=1)
MULTIPART_ALLOWANCE_BYTES: Final = 64 * 1024
MAX_BODY_BYTES: Final = MAX_UPLOAD_BYTES + MULTIPART_ALLOWANCE_BYTES
MAX_FORM_FIELDS: Final = 8
MAX_FILE_PARTS: Final = 2
JOB_ID_BYTES: Final = 8
DEFAULT_LIBRARY: Final = "global"
FILE_FIELD: Final = "file"
LIBRARY_FIELD: Final = "library"
LIBRARY_FILE_FIELD: Final = "library_file"
FORM_FIELDS: Final = frozenset({FILE_FIELD, LIBRARY_FIELD})
MULTIPART_TYPE: Final = "multipart/form-data"
CONTENT_TYPE: Final = "content-type"
CUSTOM_LIBRARY_REFUSED: Final = "custom library upload is not supported; choose a built-in library"
LIVE_MODE: Final = "live"
REPLAY_MODE: Final = "replay"
FAKE_MODE: Final = "fake"
INTERNAL_ERROR: Final = "internal_error"
MISCONFIGURED: Final = "server_misconfigured"
CONFIG_ERRORS: Final = (WiringError, ConfigError)
ABSOLUTE_PATH_RE: Final = re.compile(
    r"(?:[A-Za-z]:[\\/]|/)(?:[^\s'\"<>|]*[\\/])+(?P<name>[^\s'\"<>|\\/,;:]+)"
)
CSV_MEDIA_TYPE: Final = "text/csv"
CONTENT_LENGTH: Final = "content-length"
UNSAFE_FILENAME_RE: Final = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_STEM: Final = "boq"
HTTP_ACCEPTED: Final = 202
HTTP_BAD_REQUEST: Final = 400
HTTP_NOT_FOUND: Final = 404
HTTP_CONFLICT: Final = 409
HTTP_PAYLOAD_TOO_LARGE: Final = 413
HTTP_UNPROCESSABLE: Final = 422
HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_UNAVAILABLE: Final = 503
UPLOAD_SCHEMA: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [FILE_FIELD],
                    "properties": {
                        FILE_FIELD: {"type": "string", "format": "binary"},
                        LIBRARY_FIELD: {"type": "string", "default": DEFAULT_LIBRARY},
                    },
                }
            }
        },
    }
}

Clock = Callable[[], datetime]
LOGGER = logging.getLogger(__name__)


class JobStatus(StrEnum):
    """Where a job is in its life."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINISHED: Final = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED})


class ApiError(Exception):
    """A refused jobs request, answered as ``{error, detail}``."""

    def __init__(self, status: int, error: str, detail: object = None) -> None:
        """Keep the status code, the error code and its detail.

        Args:
            status: The HTTP status.
            error: A stable machine-readable code, e.g. ``queue_full``.
            detail: A human-readable message or a list, e.g. the missing columns.

        """
        super().__init__(error)
        self.status = status
        self.error = error
        self.detail = detail


async def api_error_handler(request: Request, error: Exception) -> Response:
    """Render an ``ApiError`` as its JSON body.

    Args:
        request: The request.
        error: The ``ApiError``.

    Returns:
        ``{error, detail}`` with the error's status.

    """
    del request
    if not isinstance(error, ApiError):
        raise error
    return JSONResponse({"error": error.error, "detail": error.detail}, status_code=error.status)


@dataclass
class Job:
    """One uploaded BoQ and its run.

    Attributes:
        job_id: The job's id.
        boq: The parsed upload, released once the job finishes.
        library_id: The library it runs against.
        input_sha256: SHA-256 of the uploaded bytes, the run's input hash.
        filename: The uploaded file's name, as given.
        created_at: When it was accepted.
        progress: What the run has finished so far.
        total_lines: The number of input lines.
        library: The library's id, row count and short hash.
        status: Where it is.
        started_at: When it started running.
        finished_at: When it finished, failed or was cancelled.
        result: The run, once done.
        output_csv: The CLI writer's output for the run, once done.
        failure: Why it failed, when it did.

    """

    job_id: str
    boq: BoqFile | None
    library_id: str
    input_sha256: str
    filename: str
    created_at: datetime
    progress: RunProgress
    total_lines: int
    library: dict[str, Any]
    status: JobStatus = JobStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: RunResult | None = None
    output_csv: bytes | None = None
    failure: str | None = None

    @property
    def total(self) -> int:
        """The number of input lines."""
        return self.total_lines


JobRunner = Callable[[Job], Awaitable[RunResult]]


@dataclass
class JobStore:
    """In-memory jobs, run one at a time in submission order.

    Attributes:
        runner: Runs a job's BoQ and returns the run.
        clock: UTC clock for timestamps and expiry.
        jobs: Live jobs by id, oldest first.
        expired: Ids whose results were dropped, oldest first, at most ``MAX_EXPIRED_IDS``.

    """

    runner: JobRunner
    clock: Clock
    jobs: dict[str, Job] = field(default_factory=dict)
    expired: OrderedDict[str, None] = field(default_factory=OrderedDict)
    _tasks: dict[str, "asyncio.Task[None]"] = field(default_factory=dict)
    _turn: asyncio.Lock = field(default_factory=asyncio.Lock)

    def queued(self) -> int:
        """Return how many jobs wait for their turn."""
        return sum(job.status == JobStatus.QUEUED for job in self.jobs.values())

    def submit(self, job: Job) -> None:
        """Queue a job and start its task on the running loop.

        Args:
            job: A new job.

        Raises:
            ApiError: 429 ``queue_full`` when four jobs already wait, or when every kept job is
                still waiting or running.

        """
        self._drop_expired()
        if self.queued() >= MAX_QUEUED:
            raise ApiError(HTTP_TOO_MANY_REQUESTS, "queue_full", f"{MAX_QUEUED} jobs wait already")
        self._make_room()
        self.jobs[job.job_id] = job
        task = asyncio.create_task(self._execute(job))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.job_id, None))

    def get(self, job_id: str) -> Job:
        """Return a live job.

        Args:
            job_id: The job's id.

        Returns:
            The job.

        Raises:
            ApiError: 404 ``job_expired`` an hour after it finished, else 404 ``job_not_found``.

        """
        self._drop_expired()
        if job_id in self.expired:
            raise ApiError(HTTP_NOT_FOUND, "job_expired", "results are kept 1 h after a job ends")
        job = self.jobs.get(job_id)
        if job is None:
            raise ApiError(HTTP_NOT_FOUND, "job_not_found", "no such job on this server")
        return job

    def expires_at(self, job: Job) -> datetime | None:
        """Return when a finished job's result is dropped; None while it waits or runs.

        Args:
            job: The job.

        Returns:
            The moment, or None.

        """
        return None if job.finished_at is None else job.finished_at + RESULT_TTL

    async def shutdown(self) -> None:
        """Cancel every waiting and running job; nothing persists across restarts."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for job in self.jobs.values():
            if job.status not in FINISHED:
                self._finish(job, JobStatus.CANCELLED)

    def _drop_expired(self) -> None:
        """Forget every job whose result is past its expiry."""
        now = self.clock()
        for job in list(self.jobs.values()):
            expiry = self.expires_at(job)
            if expiry is not None and now >= expiry:
                self._expire(job.job_id)

    def _make_room(self) -> None:
        """Expire the oldest finished jobs until one more fits under ``MAX_KEPT_JOBS``."""
        finished = [job for job in self.jobs.values() if job.status in FINISHED]
        finished.sort(key=lambda job: job.finished_at or job.created_at)
        while len(self.jobs) >= MAX_KEPT_JOBS and finished:
            self._expire(finished.pop(0).job_id)
        if len(self.jobs) >= MAX_KEPT_JOBS:
            raise ApiError(
                HTTP_TOO_MANY_REQUESTS, "queue_full", f"{MAX_KEPT_JOBS} jobs are still active"
            )

    def _expire(self, job_id: str) -> None:
        """Drop a job's result and remember its id, forgetting the oldest ids past the cap."""
        self.jobs.pop(job_id, None)
        self.expired[job_id] = None
        while len(self.expired) > MAX_EXPIRED_IDS:
            self.expired.popitem(last=False)

    async def _execute(self, job: Job) -> None:
        """Wait for the job's turn, run it and keep its outcome."""
        try:
            async with self._turn:
                await self._run(job)
        except asyncio.CancelledError:
            self._finish(job, JobStatus.CANCELLED)
            raise

    async def _run(self, job: Job) -> None:
        """Run one job; a failure is kept on the job, never raised."""
        job.status, job.started_at = JobStatus.RUNNING, self.clock()
        try:
            result = await self.runner(job)
        except Exception as error:  # the job fails; the server and the queue carry on
            LOGGER.error("job %s failed", job.job_id, exc_info=error)
            job.failure = failure_text(error)
            self._finish(job, JobStatus.FAILED)
            return
        job.result, job.output_csv = result, render_csv(result)
        self._finish(job, JobStatus.DONE)

    def _finish(self, job: Job, status: JobStatus) -> None:
        """Mark a job finished now and release its parsed upload."""
        job.status, job.finished_at, job.boq = status, self.clock(), None


def strip_paths(text: str) -> str:
    """Replace every absolute path in a message by its last component.

    Args:
        text: A message.

    Returns:
        The message, e.g. ``global.yaml`` where it named ``C:/repo/data/global.yaml``.

    """
    return ABSOLUTE_PATH_RE.sub(r"\g<name>", text)


def failure_text(error: BaseException) -> str:
    """Return what a client may see of a job's failure: a code, never a server path.

    Args:
        error: What the run raised.

    Returns:
        ``server_misconfigured: <message>`` for a wiring or config error, also when an HTTP
        error wraps one; else an HTTP error's detail; else ``internal_error: <exception type>``.

    """
    if isinstance(error, HTTPException) and isinstance(error.__cause__, CONFIG_ERRORS):
        error = error.__cause__
    if isinstance(error, HTTPException):
        return strip_paths(str(error.detail))
    if isinstance(error, CONFIG_ERRORS):
        return f"{MISCONFIGURED}: {strip_paths(str(error))}"
    return f"{INTERNAL_ERROR}: {type(error).__name__}"


@dataclass(frozen=True)
class Serving:
    """How this server answers jobs.

    Attributes:
        mode: ``live`` (the configured model), ``replay`` (a recorded run's answers, no model
            call) or ``fake`` (the offline FakeLLM).
        replay_run: The replayed run's id, in replay mode.
        library_ids: The libraries jobs may use; None for every configured one.
        library_note: Why other libraries are refused, added to the 422 detail.

    """

    mode: str = LIVE_MODE
    replay_run: str | None = None
    library_ids: tuple[str, ...] | None = None
    library_note: str | None = None


class _MemoryMultiPartParser(MultiPartParser):
    """Starlette's multipart parser, keeping file parts up to the body limit in memory."""

    spool_max_size = MAX_BODY_BYTES


@dataclass(frozen=True)
class JobsApi:
    """The jobs routes and what they need from the application.

    Attributes:
        store: The job store.
        service: The match service, for the libraries and the policy preview.
        library_ids: The library ids jobs may use.
        key_configured: Tells whether the model's key is configured.
        serving: How the server answers.
        concurrency: Model calls in flight at once (``Settings.concurrency``).

    """

    store: JobStore
    service: MatchService
    library_ids: tuple[str, ...]
    key_configured: Callable[[], bool]
    serving: Serving
    concurrency: int

    async def create(self, request: Request) -> JSONResponse:
        """Accept an uploaded BoQ as a job (``POST /v1/jobs``).

        Args:
            request: The multipart request.

        Returns:
            202 with the job id, line count, library, policy resolution, encoding and warnings.

        Raises:
            ApiError: 400, 413, 422, 429 or 503, as the module docstring lists.

        """
        _check_content_length(request)
        form = await _parse_form(request, await _read_body(request))
        try:
            upload, library_id = _form_fields(form)
            self._check_library(library_id)
            data = await _read_limited(upload)
        finally:
            await form.close()
        filename = upload.filename or ""
        uploaded = _parse(filename, data)
        job = self._new_job(uploaded.boq, library_id, data, filename)
        self.store.submit(job)
        return JSONResponse(self._accepted(job, uploaded), status_code=HTTP_ACCEPTED)

    async def status(self, job_id: str) -> dict[str, Any]:
        """Report a job's progress (``GET /v1/jobs/{job_id}``).

        Args:
            job_id: The job's id.

        Returns:
            Status, lines done and total, failures, cost so far, elapsed and remaining seconds,
            and when the result expires.

        """
        job = self.store.get(job_id)
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "filename": job.filename,
            "library": job.library,
            **self._counts(job),
            **self._timing(job),
            "expires_at": iso(self.store.expires_at(job)),
            "run_id": job.result.run_id if job.result else None,
            "failure": job.failure,
            **self.serving_record(),
        }

    async def serving_info(self) -> dict[str, Any]:
        """Say how the server answers (``GET /v1/serving``).

        Returns:
            The mode, the replayed run's id, the model concurrency and the library ids.

        """
        return {**self.serving_record(), "libraries": sorted(self.library_ids)}

    def serving_record(self) -> dict[str, Any]:
        """Return the serving fields every job body carries.

        Returns:
            ``{mode, replay_run, concurrency}``.

        """
        return {
            "mode": self.serving.mode,
            "replay_run": self.serving.replay_run,
            "concurrency": self.concurrency,
        }

    async def result(self, job_id: str) -> dict[str, Any]:
        """Return a done job's summary and rows (``GET /v1/jobs/{job_id}/result``).

        Args:
            job_id: The job's id.

        Returns:
            ``{summary, rows}``, one row per input line in input order.

        """
        return result_record(self._finished_run(job_id)[0])

    async def result_csv(self, job_id: str) -> Response:
        """Return a done job's output CSV (``GET /v1/jobs/{job_id}/result.csv``).

        Args:
            job_id: The job's id.

        Returns:
            The CLI writer's bytes for the run, as an attachment.

        """
        result, data = self._finished_run(job_id)
        job = self.store.get(job_id)
        name = f"{_safe_stem(job.filename)}_matched_{result.library_id}.csv"
        disposition = f'attachment; filename="{name}"'
        headers = {"Content-Disposition": disposition}
        return Response(content=data, media_type=CSV_MEDIA_TYPE, headers=headers)

    async def libraries(self) -> list[dict[str, Any]]:
        """List the built-in libraries (``GET /v1/libraries``).

        Returns:
            ``{id, rows, sha256_12}`` per configured library, in id order.

        """
        entries = []
        for library_id in sorted(self.library_ids):
            record = library_record(library_id, self.service.library(library_id))
            entries.append({"id": record.pop("name"), **record})
        return entries

    def _check_library(self, library_id: str) -> None:
        """Refuse an unknown library (422) or a model with no key configured (503)."""
        if library_id not in self.library_ids:
            known = ", ".join(sorted(self.library_ids))
            note = f"; {self.serving.library_note}" if self.serving.library_note else ""
            raise ApiError(
                HTTP_UNPROCESSABLE,
                "unknown_library",
                f"unknown library {library_id!r}; {known}{note}",
            )
        if not self.key_configured():
            raise ApiError(HTTP_UNAVAILABLE, "model_unavailable", "no API key is configured")

    def _new_job(self, boq: BoqFile, library_id: str, data: bytes, filename: str) -> Job:
        """Build a queued job for a parsed upload."""
        return Job(
            job_id=secrets.token_hex(JOB_ID_BYTES),
            boq=boq,
            library_id=library_id,
            input_sha256=hashlib.sha256(data).hexdigest(),
            filename=filename,
            created_at=self.store.clock(),
            progress=RunProgress(self.service.resources.settings.passes_k),
            total_lines=len(boq.lines),
            library=library_record(library_id, self.service.library(library_id)),
        )

    def _accepted(self, job: Job, uploaded: UploadedBoq) -> dict[str, Any]:
        """Return the 202 body of an accepted job."""
        library = self.service.library(job.library_id)
        boq = uploaded.boq
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "filename": job.filename,
            "total_lines": job.total,
            "library": job.library,
            "policy_resolution": self._policy_resolution(boq, library.sha256),
            "encoding": boq.encoding,
            "delimiter": boq.delimiter,
            "warnings": list(uploaded.warnings),
            **self.serving_record(),
        }

    def _policy_resolution(self, boq: BoqFile, library_sha256: str) -> str:
        """Resolve, without a call, the policy the B3 run will decide under (§10.6, A38)."""
        resources = self.service.resources
        query = PolicyQuery(
            model_id=resources.requested_model,
            library_sha256=library_sha256,
            passes=resources.settings.passes_k,
            path_derived=boq.path_mode == PathMode.DERIVED,
        )
        resolved = resolve_policy(resources.policy, query, warn=False)
        return (as_override(resolved) if resources.policy_override else resolved).resolution.value

    def _counts(self, job: Job) -> dict[str, Any]:
        """Return a job's lines done, total, failures and cost so far."""
        if job.result is not None:
            summary = summary_record(job.result)
            done, errors, cost = job.total, summary["errors"], summary["cost_usd"]
        else:
            done, errors, cost = job.progress.lines_done, 0, job.progress.spent_usd
        return {"done": done, "total": job.total, "errors": errors, "cost_usd": cost}

    def _timing(self, job: Job) -> dict[str, float | None]:
        """Return elapsed seconds and the remaining-time estimate from the line rate."""
        if job.started_at is None:
            return {"elapsed_s": 0.0, "eta_s": None}
        elapsed = ((job.finished_at or self.store.clock()) - job.started_at).total_seconds()
        if job.status in FINISHED:
            return {"elapsed_s": elapsed, "eta_s": 0.0}
        done = job.progress.lines_done
        eta = elapsed * (job.total - done) / done if done else None
        return {"elapsed_s": elapsed, "eta_s": eta}

    def _finished_run(self, job_id: str) -> tuple[RunResult, bytes]:
        """Return a done job's run and CSV, or refuse with 409 while it is not done."""
        job = self.store.get(job_id)
        if job.result is None or job.output_csv is None:
            raise ApiError(HTTP_CONFLICT, "job_not_done", f"the job is {job.status.value}")
        return job.result, job.output_csv


def _check_content_length(request: Request) -> None:
    """Refuse a declared body far above the upload limit before reading it (413)."""
    declared = request.headers.get(CONTENT_LENGTH, "")
    if declared.isdecimal() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()


async def _read_body(request: Request) -> bytes:
    """Read the body, counting bytes, and refuse it once it passes the limit (413).

    A chunked body has no ``Content-Length`` to check up front, so the count is what bounds it.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


async def _parse_form(request: Request, body: bytes) -> FormData:
    """Parse a bounded multipart body in memory; a malformed or over-full form is 422."""
    if not request.headers.get(CONTENT_TYPE, "").startswith(MULTIPART_TYPE):
        raise ApiError(HTTP_UNPROCESSABLE, "missing_file", "send the BoQ as multipart/form-data")

    async def stream() -> AsyncGenerator[bytes, None]:
        """Yield the buffered body once."""
        yield body

    parser = _MemoryMultiPartParser(
        request.headers, stream(), max_files=MAX_FILE_PARTS, max_fields=MAX_FORM_FIELDS
    )
    try:
        return await parser.parse()
    except MultiPartException as error:
        raise ApiError(HTTP_UNPROCESSABLE, "unexpected_field", error.message) from error


def _too_large() -> ApiError:
    """Return the 413 for an upload over 2 MB."""
    return ApiError(
        HTTP_PAYLOAD_TOO_LARGE, "file_too_large", f"the limit is {MAX_UPLOAD_BYTES} bytes"
    )


def _form_fields(form: FormData) -> tuple[UploadFile, str]:
    """Return the uploaded file and the library id, refusing any other field (422)."""
    keys = [key for key, _ in form.multi_items()]
    if LIBRARY_FILE_FIELD in keys:
        raise ApiError(HTTP_UNPROCESSABLE, "unexpected_field", CUSTOM_LIBRARY_REFUSED)
    unexpected = sorted(set(keys) - FORM_FIELDS)
    if unexpected:
        raise ApiError(HTTP_UNPROCESSABLE, "unexpected_field", unexpected)
    if keys.count(FILE_FIELD) > 1:
        raise ApiError(HTTP_UNPROCESSABLE, "unexpected_field", "send exactly one 'file' part")
    upload = form.get(FILE_FIELD)
    if not isinstance(upload, UploadFile):
        raise ApiError(HTTP_UNPROCESSABLE, "missing_file", "send the BoQ as the 'file' part")
    library = form.get(LIBRARY_FIELD, DEFAULT_LIBRARY)
    if not isinstance(library, str):
        raise ApiError(HTTP_UNPROCESSABLE, "unexpected_field", [LIBRARY_FIELD])
    return upload, library.strip() or DEFAULT_LIBRARY


async def _read_limited(upload: UploadFile) -> bytes:
    """Read an upload, refusing more than 2 MB (413)."""
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise _too_large()
    return data


def _parse(filename: str, data: bytes) -> UploadedBoq:
    """Parse an upload, mapping each reader error to its status and code."""
    try:
        return read_upload(filename, data, max_rows=MAX_ROWS)
    except TooManyRowsError as error:
        raise ApiError(HTTP_PAYLOAD_TOO_LARGE, "too_many_rows", str(error)) from error
    except WorkbookTooLargeError as error:
        raise ApiError(HTTP_PAYLOAD_TOO_LARGE, "workbook_too_large", str(error)) from error
    except UnsupportedTypeError as error:
        raise ApiError(HTTP_BAD_REQUEST, "unsupported_type", str(error)) from error
    except EmptyBoqError as error:
        raise ApiError(HTTP_BAD_REQUEST, "empty_file", str(error)) from error
    except MissingColumnsError as error:
        raise ApiError(HTTP_BAD_REQUEST, "missing_columns", list(error.missing)) from error
    except BoqFormatError as error:
        raise ApiError(HTTP_BAD_REQUEST, "unreadable_file", str(error)) from error


def _safe_stem(filename: str) -> str:
    """Return an upload's stem with only ``[A-Za-z0-9._-]``, for a download name."""
    stem = UNSAFE_FILENAME_RE.sub("_", PurePath(filename).stem).strip("._")
    return stem or DEFAULT_STEM


def register_job_routes(router: APIRouter, jobs: JobsApi) -> None:
    """Add the jobs and libraries routes to the authorised ``/v1`` router.

    Args:
        router: The ``/v1`` router.
        jobs: The routes' handlers.

    """
    router.add_api_route(
        "/jobs",
        jobs.create,
        methods=["POST"],
        status_code=HTTP_ACCEPTED,
        openapi_extra=UPLOAD_SCHEMA,
    )
    router.add_api_route("/jobs/{job_id}", jobs.status, methods=["GET"])
    router.add_api_route("/jobs/{job_id}/result", jobs.result, methods=["GET"])
    router.add_api_route("/jobs/{job_id}/result.csv", jobs.result_csv, methods=["GET"])
    router.add_api_route("/libraries", jobs.libraries, methods=["GET"])
    router.add_api_route("/serving", jobs.serving_info, methods=["GET"])
