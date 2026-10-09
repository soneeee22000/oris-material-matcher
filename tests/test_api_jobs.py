"""Operator UI jobs API (docs/ui-spec.md §2-§4): U1-U3, limits, auth, expiry, CSV parity."""

import asyncio
import csv
import io
import shutil
import threading
import time
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from oris_matcher import cli
from oris_matcher.api import jobs as jobs_module
from oris_matcher.api.app import REQUEST_ID_HEADER, create_app
from oris_matcher.api.jobs import MAX_QUEUED, MAX_ROWS, MAX_UPLOAD_BYTES, RESULT_TTL
from oris_matcher.doctor import Runtime
from oris_matcher.io.boq_reader import read_boq
from oris_matcher.llm.base import LLMRequest, LLMResult
from oris_matcher.llm.fake_llm import FakeBehaviour, FakeLLM, Fault, FaultKind
from test_api import (
    ALLOWLIST,
    CONFIG,
    LABELS,
    LIBRARIES,
    ROOT,
    SMALL_BOQ,
    TOKEN,
    CliFactory,
    Recorder,
    _cli_run,
    _matching_answers,
    fake_git,
    make_settings,
    no_sleep,
)

EN_INPUT = ROOT / "input" / "boq_dataset_input_en.csv"
SUBMISSION_RUN = ROOT / "runs" / "submission" / "20261008T023928Z-56f85fb8"
IMPROVED_EN = ROOT / "output" / "improved_output_en.csv"
HEADER = "Item No.,Short Description,Long Description,Unit,BoQ Qty\r\n"
POLL_S = 0.02
WAIT_S = 60.0
CSV_TYPE = "text/csv"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run from a temporary folder holding data/enrichment/, so run folders stay out of the repo."""
    shutil.copytree(ROOT / "data" / "enrichment", tmp_path / "data" / "enrichment")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class Clock:
    """A settable UTC clock."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class Gate:
    """Holds every model call until opened, so a job stays running."""

    def __init__(self, free_calls: int = 0) -> None:
        self.event = threading.Event()
        self.free_calls = free_calls
        self.started = 0

    def open(self) -> None:
        self.event.set()


class GatedFake(FakeLLM):
    """A FakeLLM whose calls wait for the gate, once its free calls are used."""

    def __init__(self, model: str, gate: Gate) -> None:
        super().__init__(model, ALLOWLIST)
        self.gate = gate

    async def complete(self, req: LLMRequest) -> LLMResult:
        self.gate.started += 1
        if self.gate.started > self.gate.free_calls:
            while not self.gate.event.is_set():
                await asyncio.sleep(POLL_S)
        return await super().complete(req)


def gated_factory(gate: Gate) -> Any:
    def factory(model: str) -> FakeLLM:
        return GatedFake(model, gate)

    return factory


def make_client(
    factory: Any = None, clock: Clock | None = None, ui_dir: Path | None = None, **overrides: Any
) -> TestClient:
    runtime = Runtime(sleep=no_sleep, git=fake_git)
    app = create_app(
        make_settings(**overrides),
        factory or Recorder(),
        runtime=runtime,
        job_clock=clock,
        ui_dir=ui_dir,
    )
    return TestClient(app)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client() as test_client:
        yield test_client


def upload(
    client: TestClient,
    data: bytes,
    name: str = "boq.csv",
    *,
    library: str | None = "global",
    headers: dict[str, str] | None = None,
) -> Any:
    fields = {} if library is None else {"library": library}
    content_type = XLSX_TYPE if name.endswith(".xlsx") else CSV_TYPE
    return client.post(
        "/v1/jobs",
        files={"file": (name, data, content_type)},
        data=fields,
        headers=headers or {},
    )


def wait_for(client: TestClient, job_id: str, *wanted: str) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        body: dict[str, Any] = client.get(f"/v1/jobs/{job_id}").json()
        if body["status"] in wanted:
            return body
        time.sleep(POLL_S)
    raise AssertionError(f"job {job_id} never reached {wanted}")


def run_job(client: TestClient, data: bytes, name: str = "boq.csv", library: str = "global") -> str:
    response = upload(client, data, name, library=library)
    assert response.status_code == 202, response.text
    job_id: str = response.json()["job_id"]
    assert wait_for(client, job_id, "done", "failed")["status"] == "done"
    return job_id


def xlsx_bytes(path: Path) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.reader(handle):
            sheet.append(record)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_u1_csv_and_xlsx_give_identical_line_ids_and_cells(client: TestClient) -> None:
    from_csv = client.get(f"/v1/jobs/{run_job(client, SMALL_BOQ.read_bytes())}/result").json()
    xlsx_job = run_job(client, xlsx_bytes(SMALL_BOQ), "boq.xlsx")
    from_xlsx = client.get(f"/v1/jobs/{xlsx_job}/result").json()

    cells = ("line_id", "item_no", "short", "long", "unit", "qty", "level")
    assert [[row[c] for c in cells] for row in from_xlsx["rows"]] == [
        [row[c] for c in cells] for row in from_csv["rows"]
    ]


def test_submit_answers_202_with_the_file_and_library_facts(client: TestClient) -> None:
    response = upload(client, SMALL_BOQ.read_bytes(), library="fr")

    assert response.status_code == 202
    body = response.json()
    assert body["total_lines"] == len(read_boq(SMALL_BOQ).lines)
    assert body["library"]["name"] == "fr"
    assert body["library"]["rows"] > 0
    assert len(body["library"]["sha256_12"]) == 12
    assert body["policy_resolution"]
    assert body["encoding"] == "utf-8"
    assert body["warnings"] == []
    assert response.headers[REQUEST_ID_HEADER]


def test_a_cp1252_file_is_read_with_a_warning(client: TestClient) -> None:
    data = (HEADER + "01.01.0010.,Béton,,m3,4\r\n").encode("cp1252")

    body = upload(client, data).json()

    assert body["encoding"] == "cp1252"
    assert any("cp1252" in warning for warning in body["warnings"])


def test_u2_job_goes_queued_running_done() -> None:
    gate = Gate()
    with make_client(gated_factory(gate)) as client:
        first = upload(client, SMALL_BOQ.read_bytes()).json()["job_id"]
        wait_for(client, first, "running")
        second = upload(client, SMALL_BOQ.read_bytes()).json()["job_id"]
        queued = client.get(f"/v1/jobs/{second}").json()
        gate.open()
        running_or_done = wait_for(client, second, "running", "done")
        done = wait_for(client, second, "done")

    assert queued["status"] == "queued"
    assert queued["done"] == 0
    assert running_or_done["status"] in {"running", "done"}
    assert done["done"] == done["total"] == len(read_boq(SMALL_BOQ).lines)
    assert done["expires_at"] is not None
    assert done["elapsed_s"] >= 0
    assert done["eta_s"] == 0


def test_a_running_job_reports_lines_done_cost_and_eta() -> None:
    gate = Gate(free_calls=40)
    fr_input = ROOT / "input" / "boq_dataset_input_fr.csv"
    with make_client(gated_factory(gate)) as client:
        job_id = upload(client, fr_input.read_bytes(), library="fr").json()["job_id"]
        deadline = time.monotonic() + WAIT_S
        status = client.get(f"/v1/jobs/{job_id}").json()
        while status["done"] == 0 and time.monotonic() < deadline:
            time.sleep(POLL_S)
            status = client.get(f"/v1/jobs/{job_id}").json()
        gate.open()
        done = wait_for(client, job_id, "done")
        rows = client.get(f"/v1/jobs/{job_id}/result").json()["rows"]
        job = client.app.state.job_store.get(job_id)  # type: ignore[attr-defined]

    assert status["status"] == "running"
    assert 0 < status["done"] < status["total"]
    assert status["eta_s"] is not None and status["eta_s"] > 0
    assert status["cost_usd"] >= 0
    assert status["expires_at"] is None
    assert done["done"] == done["total"] == len(rows)
    assert job.progress.lines_done == sum(bool(row["call_ids"]) for row in rows)


def test_u3_result_rows_follow_the_input_count_and_order(client: TestClient) -> None:
    job_id = run_job(client, SMALL_BOQ.read_bytes())

    result = client.get(f"/v1/jobs/{job_id}/result").json()

    lines = read_boq(SMALL_BOQ).lines
    assert [row["line_id"] for row in result["rows"]] == [line.line_id for line in lines]
    assert [row["item_no"] for row in result["rows"]] == [line.item_no for line in lines]
    summary = result["summary"]
    counts = sum(summary[name] for name in ("matched", "needs_review", "not_a_material"))
    assert counts == len(lines)
    assert summary["policy_resolution"]
    assert summary["wall_clock_s_per_line"] >= 0


def test_result_rows_carry_the_decision_and_its_audit(client: TestClient) -> None:
    job_id = run_job(client, SMALL_BOQ.read_bytes(), library="fr")

    rows = client.get(f"/v1/jobs/{job_id}/result").json()["rows"]

    header = rows[0]
    assert header["decision"] == "not_a_material"
    assert header["audit"]["gate"]
    assert header["audit"]["error"] is None
    routed = [row for row in rows if row["call_ids"]]
    assert routed
    assert all(row["audit"]["raw_line_response"] for row in routed)
    assert all(row["decision"] in {"matched", "needs_review", "not_a_material"} for row in rows)


def test_result_csv_is_the_writer_output_of_the_run(client: TestClient) -> None:
    job_id = run_job(client, SMALL_BOQ.read_bytes())

    response = client.get(f"/v1/jobs/{job_id}/result.csv")
    rows = client.get(f"/v1/jobs/{job_id}/result").json()["rows"]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(CSV_TYPE)
    records = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"), newline="")))
    assert [r["decision"] for r in records] == [row["decision"] for row in rows]
    assert [r["call_ids"] for r in records] == [";".join(row["call_ids"]) for row in rows]
    assert response.content.startswith(b"Item No.,Short Description")


def test_result_csv_matches_the_cli_on_the_same_input_and_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = _matching_answers()
    recorder = Recorder(FakeBehaviour(answers=answers))
    fr_input = ROOT / "input" / "boq_dataset_input_fr.csv"
    with make_client(recorder) as client:
        job_id = run_job(client, fr_input.read_bytes(), library="fr")
        data = client.get(f"/v1/jobs/{job_id}/result.csv").content
    job_rows = [
        tuple(row[label] for label in LABELS)
        for row in csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
    ]
    factory = CliFactory(answers)

    cli_rows = _cli_run(tmp_path, monkeypatch, factory)

    assert job_rows == cli_rows
    cli_requests = [request for fake in factory.fakes for request in fake.calls]
    assert sorted(r.sha256() for r in recorder.calls) == sorted(r.sha256() for r in cli_requests)


def test_model_failures_finish_the_job_with_every_line_kept() -> None:
    def always_down(call_no: int, req: LLMRequest) -> Fault | None:
        del call_no, req
        return Fault(FaultKind.SERVER_ERROR)

    recorder = Recorder(FakeBehaviour(rule=always_down))
    with make_client(recorder, max_retries=0) as client:
        job_id = run_job(client, SMALL_BOQ.read_bytes())
        status = client.get(f"/v1/jobs/{job_id}").json()
        result = client.get(f"/v1/jobs/{job_id}/result").json()

    assert len(result["rows"]) == len(read_boq(SMALL_BOQ).lines)
    failed = [row for row in result["rows"] if row["audit"]["error"]]
    assert failed
    assert all(row["decision"] == "needs_review" for row in failed)
    assert status["errors"] == result["summary"]["errors"] == len(failed)


def test_a_file_over_2_mb_is_413(client: TestClient) -> None:
    data = HEADER.encode() + b"x" * MAX_UPLOAD_BYTES

    response = upload(client, data)

    assert response.status_code == 413
    assert response.json()["error"] == "file_too_large"


def test_a_declared_body_far_over_2_mb_is_413_before_it_is_read(client: TestClient) -> None:
    data = HEADER.encode() + b"x" * (2 * MAX_UPLOAD_BYTES)

    response = upload(client, data)

    assert response.status_code == 413
    assert response.json()["error"] == "file_too_large"


def test_exactly_2000_rows_pass_and_2001_are_413(client: TestClient) -> None:
    row = "01.01.0010.,Concrete,,m3,4\r\n"
    passing = upload(client, (HEADER + row * MAX_ROWS).encode())
    too_many = upload(client, (HEADER + row * (MAX_ROWS + 1)).encode())

    assert passing.status_code == 202
    assert too_many.status_code == 413
    assert too_many.json()["error"] == "too_many_rows"


def test_an_xlsx_over_2000_rows_is_413(client: TestClient) -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(HEADER.strip().split(","))
    for _ in range(MAX_ROWS + 1):
        sheet.append(["01.01.0010.", "Concrete", "", "m3", "4"])
    buffer = io.BytesIO()
    book.save(buffer)

    response = upload(client, buffer.getvalue(), "boq.xlsx")

    assert response.status_code == 413
    assert response.json()["error"] == "too_many_rows"


def test_unknown_library_is_422(client: TestClient) -> None:
    response = upload(client, SMALL_BOQ.read_bytes(), library="mars")

    assert response.status_code == 422
    assert response.json()["error"] == "unknown_library"


def test_an_unexpected_form_field_is_422(client: TestClient) -> None:
    response = client.post(
        "/v1/jobs",
        files={"file": ("boq.csv", SMALL_BOQ.read_bytes(), CSV_TYPE)},
        data={"library": "global", "library_file": "x"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "unexpected_field"


def test_the_library_defaults_to_global(client: TestClient) -> None:
    response = upload(client, SMALL_BOQ.read_bytes(), library=None)

    assert response.status_code == 202
    assert response.json()["library"]["name"] == "global"


def test_a_missing_column_is_400_and_named(client: TestClient) -> None:
    data = b"Item No.,Short Description,Long Description,Unit\r\n01,Concrete,,m3\r\n"

    response = upload(client, data)

    assert response.status_code == 400
    assert response.json() == {"error": "missing_columns", "detail": ["BoQ Qty"]}


@pytest.mark.parametrize("name", ["boq.xlsm", "boq.xls", "boq.txt"])
def test_an_unsupported_type_is_400(client: TestClient, name: str) -> None:
    response = upload(client, SMALL_BOQ.read_bytes(), name)

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_type"


@pytest.mark.parametrize("data", [b"", b"\r\n\r\n"])
def test_an_empty_file_is_400(client: TestClient, data: bytes) -> None:
    response = upload(client, data)

    assert response.status_code == 400
    assert response.json()["error"] == "empty_file"


def test_a_header_only_file_is_400_empty(client: TestClient) -> None:
    response = upload(client, HEADER.encode())

    assert response.status_code == 400
    assert response.json()["error"] == "empty_file"


def test_a_fifth_queued_job_is_429() -> None:
    gate = Gate()
    with make_client(gated_factory(gate)) as client:
        running = upload(client, SMALL_BOQ.read_bytes()).json()["job_id"]
        wait_for(client, running, "running")
        queued = [upload(client, SMALL_BOQ.read_bytes()).status_code for _ in range(MAX_QUEUED)]
        refused = upload(client, SMALL_BOQ.read_bytes())
        gate.open()

    assert queued == [202] * MAX_QUEUED
    assert refused.status_code == 429
    assert refused.json()["error"] == "queue_full"


def test_an_unknown_job_is_404(client: TestClient) -> None:
    for path in ("", "/result", "/result.csv"):
        response = client.get(f"/v1/jobs/nope{path}")
        assert response.status_code == 404
        assert response.json()["error"] == "job_not_found"


def test_a_result_is_404_expired_an_hour_after_the_job_finished() -> None:
    clock = Clock()
    with make_client(clock=clock) as client:
        job_id = run_job(client, SMALL_BOQ.read_bytes())
        expires_at = client.get(f"/v1/jobs/{job_id}").json()["expires_at"]
        clock.now += RESULT_TTL - timedelta(seconds=1)
        still = client.get(f"/v1/jobs/{job_id}/result").status_code
        clock.now += timedelta(seconds=2)
        expired = [client.get(f"/v1/jobs/{job_id}{path}") for path in ("", "/result")]

    assert datetime.fromisoformat(expires_at) == datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    assert still == 200
    assert [response.status_code for response in expired] == [404, 404]
    assert {response.json()["error"] for response in expired} == {"job_expired"}


def test_a_result_before_the_job_is_done_is_409() -> None:
    gate = Gate()
    with make_client(gated_factory(gate)) as client:
        job_id = upload(client, SMALL_BOQ.read_bytes()).json()["job_id"]
        result = client.get(f"/v1/jobs/{job_id}/result")
        result_csv = client.get(f"/v1/jobs/{job_id}/result.csv")
        gate.open()

    assert result.status_code == result_csv.status_code == 409
    assert result.json()["error"] == "job_not_done"


def test_queued_and_running_jobs_are_cancelled_on_shutdown() -> None:
    gate = Gate()
    client = make_client(gated_factory(gate))
    with client:
        running = upload(client, SMALL_BOQ.read_bytes()).json()["job_id"]
        wait_for(client, running, "running")
        queued = upload(client, SMALL_BOQ.read_bytes()).json()["job_id"]
        store = client.app.state.job_store  # type: ignore[attr-defined]

    assert store.get(running).status == "cancelled"
    assert store.get(queued).status == "cancelled"


def test_jobs_need_the_bearer_token_when_set() -> None:
    with make_client(api_token=TOKEN) as client:
        refused = upload(client, SMALL_BOQ.read_bytes())
        libraries = client.get("/v1/libraries")
        status = client.get("/v1/jobs/nope")
        allowed = upload(
            client, SMALL_BOQ.read_bytes(), headers={"Authorization": f"Bearer {TOKEN}"}
        )

    assert refused.status_code == libraries.status_code == status.status_code == 401
    assert allowed.status_code == 202


def test_a_blank_token_leaves_the_api_open() -> None:
    """``.env.example`` ships ``ORIS_API_TOKEN=``; blank means unset, not "Bearer " (A51)."""
    with make_client(api_token="") as client:
        accepted = upload(client, SMALL_BOQ.read_bytes())
        libraries = client.get("/v1/libraries")

    assert accepted.status_code == 202
    assert libraries.status_code == 200


def test_every_jobs_response_carries_a_request_id(client: TestClient) -> None:
    job_id = run_job(client, SMALL_BOQ.read_bytes())
    responses = [
        client.get(f"/v1/jobs/{job_id}"),
        client.get(f"/v1/jobs/{job_id}/result"),
        client.get(f"/v1/jobs/{job_id}/result.csv"),
        client.get("/v1/jobs/nope"),
        client.get("/v1/libraries"),
        upload(client, b""),
    ]

    assert all(response.headers.get(REQUEST_ID_HEADER) for response in responses)


def test_libraries_lists_the_configured_ones(client: TestClient) -> None:
    body = client.get("/v1/libraries").json()

    assert [entry["id"] for entry in body] == sorted(LIBRARIES)
    assert all(entry["rows"] > 0 and len(entry["sha256_12"]) == 12 for entry in body)


def test_the_ui_is_served_when_its_folder_exists(tmp_path: Path) -> None:
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<!doctype html><title>BoQ Matcher</title>", "utf-8")
    with make_client(ui_dir=ui_dir) as client:
        page = client.get("/ui/")
    with make_client(ui_dir=tmp_path / "absent") as client:
        missing = client.get("/ui/")

    assert page.status_code == 200
    assert "BoQ Matcher" in page.text
    assert missing.status_code == 404


def test_serve_runs_uvicorn_on_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(app: object, host: str, port: int, **kwargs: Any) -> None:
        seen.update(app=app, host=host, port=port, **kwargs)

    monkeypatch.setattr(cli, "run_server", fake_run)
    monkeypatch.setenv("ORIS_CONFIG_DIR", str(CONFIG))

    result = CliRunner().invoke(cli.app, ["serve", "--llm", "fake", "--port", "8123"])

    assert result.exit_code == 0, result.output
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8123
    assert seen["workers"] == 1


def test_serve_refuses_a_bad_llm_spec() -> None:
    result = CliRunner().invoke(cli.app, ["serve", "--llm", "mars"])

    assert result.exit_code == 2


@pytest.mark.skipif(not SUBMISSION_RUN.is_dir(), reason="the submission run is not present")
def test_a_replayed_upload_of_the_en_input_reproduces_the_improved_output(
    tmp_path: Path,
) -> None:
    base = make_settings()
    runtime = Runtime(root=lambda: ROOT, sleep=no_sleep, git=fake_git)
    app = cli.serve_app(f"replay:{SUBMISSION_RUN.as_posix()}", base, runtime, tmp_path / "runs")
    with TestClient(app) as client:
        job_id = run_job(client, EN_INPUT.read_bytes(), "boq_dataset_input_en.csv")
        data = client.get(f"/v1/jobs/{job_id}/result.csv").content

    assert data == IMPROVED_EN.read_bytes()


def test_status_names_the_file_and_the_library(client: TestClient) -> None:
    created = upload(client, SMALL_BOQ.read_bytes(), "my boq.csv", library="fr").json()

    status = client.get(f"/v1/jobs/{created['job_id']}").json()

    assert status["filename"] == "my boq.csv"
    assert status["library"] == created["library"]
    assert status["library"]["name"] == "fr"


def test_202_and_status_report_the_serving_mode_and_concurrency() -> None:
    with make_client(concurrency=3) as client:
        created = upload(client, SMALL_BOQ.read_bytes()).json()
        status = client.get(f"/v1/jobs/{created['job_id']}").json()
        serving = client.get("/v1/serving").json()

    for body in (created, status, serving):
        assert body["mode"] == "live"
        assert body["replay_run"] is None
        assert body["concurrency"] == 3
    assert serving["libraries"] == sorted(LIBRARIES)


def test_the_serving_endpoint_needs_the_token_when_set() -> None:
    with make_client(api_token=TOKEN) as client:
        refused = client.get("/v1/serving")

    assert refused.status_code == 401


def test_a_whitespace_token_leaves_the_api_open() -> None:
    with make_client(api_token="   ") as client:
        accepted = upload(client, SMALL_BOQ.read_bytes())

    assert accepted.status_code == 202


def test_a_failed_job_reports_a_code_and_no_server_path(tmp_path: Path) -> None:
    secret = tmp_path / "private" / "global.yaml"

    def broken(model: str) -> FakeLLM:
        raise RuntimeError(f"{secret} was generated from another library")

    with make_client(broken) as client:
        job_id = upload(client, SMALL_BOQ.read_bytes()).json()["job_id"]
        status = wait_for(client, job_id, "failed")

    assert status["failure"].startswith("internal_error")
    assert str(tmp_path) not in status["failure"]
    assert tmp_path.as_posix() not in status["failure"]


def test_a_misconfigured_job_reports_server_misconfigured_without_paths(tmp_path: Path) -> None:
    secret = tmp_path / "data" / "enrichment" / "global.yaml"

    def broken(model: str) -> FakeLLM:
        raise cli.WiringError(f"{secret.as_posix()} was generated from library 96bea2506a61")

    with make_client(broken) as client:
        job_id = upload(client, SMALL_BOQ.read_bytes()).json()["job_id"]
        status = wait_for(client, job_id, "failed")

    assert status["failure"].startswith("server_misconfigured")
    assert "global.yaml" in status["failure"]
    assert tmp_path.as_posix() not in status["failure"]


def test_a_chunked_body_over_2_mb_is_413(client: TestClient) -> None:
    boundary = "smokeboundary"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="boq.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode()
    chunk = b"x" * (256 * 1024)

    def body() -> Iterator[bytes]:
        yield head
        for _ in range(3 * MAX_UPLOAD_BYTES // len(chunk)):
            yield chunk
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post(
        "/v1/jobs",
        content=body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == 413
    assert response.json()["error"] == "file_too_large"


def test_a_declared_oversize_body_is_refused_before_any_of_it_is_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*args: object) -> bytes:
        raise AssertionError("the body was read")

    monkeypatch.setattr(jobs_module, "_read_body", never)
    data = HEADER.encode() + b"x" * (2 * MAX_UPLOAD_BYTES)

    response = upload(client, data)

    assert response.status_code == 413
    assert response.json()["error"] == "file_too_large"


def test_a_library_file_part_is_422_custom_library_not_supported(client: TestClient) -> None:
    response = client.post(
        "/v1/jobs",
        files=[
            ("file", ("boq.csv", SMALL_BOQ.read_bytes(), CSV_TYPE)),
            ("library_file", ("lib.csv", b"material_type\r\n", CSV_TYPE)),
        ],
    )

    assert response.status_code == 422
    assert response.json()["error"] == "unexpected_field"
    assert "custom library upload is not supported" in str(response.json()["detail"])


@pytest.mark.parametrize("parts", [2, 3])
def test_more_than_one_file_part_is_422(client: TestClient, parts: int) -> None:
    files = [("file", ("boq.csv", SMALL_BOQ.read_bytes(), CSV_TYPE))] * parts

    response = client.post("/v1/jobs", files=files)

    assert response.status_code == 422
    assert response.json()["error"] == "unexpected_field"


def test_finished_jobs_past_the_cap_expire_oldest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs_module, "MAX_KEPT_JOBS", 2)
    with make_client() as client:
        first, second, third = (run_job(client, SMALL_BOQ.read_bytes()) for _ in range(3))
        statuses = [client.get(f"/v1/jobs/{job_id}") for job_id in (first, second, third)]
        store = client.app.state.job_store  # type: ignore[attr-defined]

    assert [response.status_code for response in statuses] == [404, 200, 200]
    assert statuses[0].json()["error"] == "job_expired"
    assert len(store.jobs) == 2


def test_forgotten_ids_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs_module, "MAX_KEPT_JOBS", 1)
    monkeypatch.setattr(jobs_module, "MAX_EXPIRED_IDS", 1)
    with make_client() as client:
        first, second, _ = (run_job(client, SMALL_BOQ.read_bytes()) for _ in range(3))
        oldest = client.get(f"/v1/jobs/{first}").json()
        newer = client.get(f"/v1/jobs/{second}").json()
        store = client.app.state.job_store  # type: ignore[attr-defined]

    assert oldest["error"] == "job_not_found"
    assert newer["error"] == "job_expired"
    assert len(store.expired) == 1


def test_a_finished_job_releases_its_parsed_upload(client: TestClient) -> None:
    job_id = run_job(client, SMALL_BOQ.read_bytes())

    job = client.app.state.job_store.get(job_id)  # type: ignore[attr-defined]

    assert job.boq is None
    assert job.total == len(read_boq(SMALL_BOQ).lines)


def test_an_inflating_workbook_is_413(client: TestClient) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/sharedStrings.xml", b"a" * (4 * MAX_UPLOAD_BYTES))

    response = upload(client, buffer.getvalue(), "boq.xlsx")

    assert response.status_code == 413
    assert response.json()["error"] == "workbook_too_large"


def test_serve_warns_when_the_ui_build_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "run_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "DEFAULT_UI_DIR", tmp_path / "absent")
    monkeypatch.setenv("ORIS_CONFIG_DIR", str(CONFIG))

    result = CliRunner().invoke(cli.app, ["serve", "--llm", "fake"])

    assert result.exit_code == 0, result.output
    assert "operator UI build is missing" in result.output


def test_serve_warns_when_it_binds_beyond_loopback_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "run_server", lambda *args, **kwargs: None)
    monkeypatch.setenv("ORIS_CONFIG_DIR", str(CONFIG))
    monkeypatch.setenv("ORIS_API_TOKEN", "")

    exposed = CliRunner().invoke(cli.app, ["serve", "--llm", "fake", "--host", "0.0.0.0"])
    local = CliRunner().invoke(cli.app, ["serve", "--llm", "fake", "--host", "localhost"])

    assert exposed.exit_code == local.exit_code == 0
    assert "no ORIS_API_TOKEN" in exposed.output
    assert "no ORIS_API_TOKEN" not in local.output


@pytest.mark.skipif(not SUBMISSION_RUN.is_dir(), reason="the submission run is not present")
def test_replay_serves_only_the_recorded_library(tmp_path: Path) -> None:
    base = make_settings()
    runtime = Runtime(root=lambda: ROOT, sleep=no_sleep, git=fake_git)
    app = cli.serve_app(f"replay:{SUBMISSION_RUN.as_posix()}", base, runtime, tmp_path / "runs")
    with TestClient(app) as client:
        libraries = client.get("/v1/libraries").json()
        serving = client.get("/v1/serving").json()
        refused = upload(client, SMALL_BOQ.read_bytes(), library="fr")
        accepted = upload(client, SMALL_BOQ.read_bytes(), library="global").json()

    assert [entry["id"] for entry in libraries] == ["global"]
    assert serving["mode"] == "replay"
    assert serving["replay_run"] == SUBMISSION_RUN.name
    assert serving["libraries"] == ["global"]
    assert refused.status_code == 422
    assert refused.json()["error"] == "unknown_library"
    assert "recorded on library global" in refused.json()["detail"]
    assert accepted["mode"] == "replay"
    assert accepted["replay_run"] == SUBMISSION_RUN.name
