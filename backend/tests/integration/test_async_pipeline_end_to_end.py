"""Upload to READY through the real asynchronous pipeline, across processes.

Everything is real: the FastAPI application with its full middleware and auth,
PostgreSQL, MinIO, Redis as the Celery broker, and a **separate Celery worker
process** started from `python -m orbit.composition.worker`. Nothing is called
in-process between the upload and the worker -- the only path from one to the
other is a message on the broker.

The restart test kills the worker process with no chance to clean up
(`TerminateProcess` on Windows, `SIGKILL` elsewhere) while it holds a job,
verifies the half-finished state is consistent, starts a new worker, and
verifies the document reaches READY with exactly one set of chunks.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from orbit.composition.app import create_app
from orbit.core.config import Settings
from tests.conftest import build_settings
from tests.fixtures.pdf import build_pdf, paragraph_lines

pytestmark = pytest.mark.integration

REAL_PDF = pathlib.Path(__file__).parents[1] / "fixtures" / "documents" / "employee-handbook.pdf"
BACKEND = pathlib.Path(__file__).parents[2]

# Dedicated Redis databases, so a developer's own running worker never steals
# these messages and these tests never consume a developer's.
BROKER_DB, RESULT_DB, CACHE_DB = 12, 13, 14
LEASE_SECONDS = 10  # the minimum Settings accepts


def _environment() -> dict[str, str]:
    required = ("ORBIT_TEST_DATABASE_URL", "ORBIT_S3_BUCKET", "ORBIT_S3_ACCESS_KEY_ID")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"not configured for integration: missing {', '.join(missing)}")
    return {
        "ORBIT_ENV": "test",
        "ORBIT_LOG_FORMAT": "json",
        "ORBIT_LOG_LEVEL": "INFO",
        "ORBIT_SERVICE_NAME": "orbit-worker-e2e",
        "ORBIT_DATABASE_URL": os.environ["ORBIT_TEST_DATABASE_URL"],
        "ORBIT_REDIS_URL": f"redis://localhost:6379/{CACHE_DB}",
        "ORBIT_CELERY_BROKER_URL": f"redis://localhost:6379/{BROKER_DB}",
        "ORBIT_CELERY_RESULT_BACKEND": f"redis://localhost:6379/{RESULT_DB}",
        "ORBIT_S3_ENDPOINT_URL": os.environ.get("ORBIT_S3_ENDPOINT_URL", "http://localhost:9000"),
        "ORBIT_S3_BUCKET": os.environ["ORBIT_S3_BUCKET"],
        "ORBIT_S3_ACCESS_KEY_ID": os.environ["ORBIT_S3_ACCESS_KEY_ID"],
        "ORBIT_S3_SECRET_ACCESS_KEY": os.environ["ORBIT_S3_SECRET_ACCESS_KEY"],
        "ORBIT_SECRET_KEY": "e2e-secret-key-not-for-any-real-environment-0123456789abcdef",
        "ORBIT_AI_PROVIDER": "fake",
        "ORBIT_PROCESSING_LEASE_SECONDS": str(LEASE_SECONDS),
        "ORBIT_PROCESSING_RETRY_BASE_SECONDS": "1",
        "ORBIT_PROCESSING_RETRY_MAX_SECONDS": "2",
    }


class WorkerProcess:
    """A real worker, in its own OS process."""

    def __init__(self, env: dict[str, str], log_path: pathlib.Path) -> None:
        self._env = {**os.environ, **env, "PYTHONUNBUFFERED": "1"}
        self._log_path = log_path
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        log = self._log_path.open("ab")
        # The interpreter directly, not `uv run`: killing a wrapper would leave
        # the real worker running, and this test is about the worker dying.
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "orbit.composition.worker",
                "worker",
                "--pool=solo",
                "--loglevel=INFO",
                "--without-heartbeat",
                "--without-gossip",
                "--without-mingle",
            ],
            cwd=BACKEND,
            env=self._env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    def kill(self) -> None:
        """No graceful shutdown, no signal handlers, no `finally` blocks."""
        assert self._process is not None
        self._process.kill()
        self._process.wait(timeout=30)

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.kill()
            self._process.wait(timeout=30)

    @property
    def exited(self) -> bool:
        return self._process is not None and self._process.poll() is not None

    def log(self) -> str:
        return self._log_path.read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def env(migrated_engine: AsyncEngine) -> dict[str, str]:
    del migrated_engine  # ensures the schema exists before any process uses it
    environment = _environment()
    redis.Redis.from_url(environment["ORBIT_CELERY_BROKER_URL"]).flushdb()
    return environment


@pytest.fixture
def api(env: dict[str, str]) -> Iterator[TestClient]:
    settings: Settings = build_settings(
        database_url=env["ORBIT_DATABASE_URL"],
        redis_url=env["ORBIT_REDIS_URL"],
        celery_broker_url=env["ORBIT_CELERY_BROKER_URL"],
        celery_result_backend=env["ORBIT_CELERY_RESULT_BACKEND"],
        s3_endpoint_url=env["ORBIT_S3_ENDPOINT_URL"],
        s3_bucket=env["ORBIT_S3_BUCKET"],
        s3_access_key_id=env["ORBIT_S3_ACCESS_KEY_ID"],
        s3_secret_access_key=env["ORBIT_S3_SECRET_ACCESS_KEY"],
        secret_key=env["ORBIT_SECRET_KEY"],
    )
    # A real peer address: the audit trail stores it in an INET column, and
    # TestClient's default placeholder ("testclient") is not an address.
    # https, because the session cookies are `Secure` and would not be sent back.
    with TestClient(
        create_app(settings),
        base_url="https://testserver",
        headers={"Origin": "https://testserver"},
        client=("127.0.0.1", 50000),
    ) as client:
        yield client


@pytest.fixture
def worker_factory(
    env: dict[str, str], tmp_path: pathlib.Path
) -> Iterator[Callable[[], WorkerProcess]]:
    workers: list[WorkerProcess] = []

    def start() -> WorkerProcess:
        worker = WorkerProcess(env, tmp_path / f"worker-{len(workers)}.log")
        worker.start()
        workers.append(worker)
        return worker

    yield start
    for worker in workers:
        worker.stop()


@pytest.fixture
def workspace_url(api: TestClient) -> str:
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.test"
    password = "a-long-passphrase-for-e2e-tests"
    assert (
        api.post(
            "/api/v1/auth/register", json={"email": email, "password": password, "full_name": "E2E"}
        ).status_code
        == 201
    )
    assert (
        api.post("/api/v1/auth/login", json={"email": email, "password": password}).status_code
        == 200
    )
    created = api.post("/api/v1/workspaces", json={"name": "E2E"})
    assert created.status_code == 201, created.text
    return f"/api/v1/workspaces/{created.json()['id']}/documents"


def _upload(api: TestClient, workspace_url: str, filename: str, data: bytes) -> str:
    response = api.post(workspace_url, params={"filename": filename}, content=data)
    assert response.status_code == 201, response.text
    return str(response.json()["document"]["id"])


def _status(api: TestClient, workspace_url: str, document_id: str) -> dict[str, Any]:
    response = api.get(f"{workspace_url}/{document_id}/processing")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _wait_for(  # noqa: PLR0913 -- a polling helper's knobs
    api: TestClient,
    workspace_url: str,
    document_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float,
    worker: WorkerProcess,
    interval: float = 0.05,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body = _status(api, workspace_url, document_id)
    while not predicate(body):
        if worker.exited:
            pytest.fail(f"worker process exited\n--- worker log ---\n{worker.log()[-6000:]}")
        if time.monotonic() > deadline:
            pytest.fail(
                f"timed out; last status {body}\n--- worker log ---\n{worker.log()[-6000:]}"
            )
        time.sleep(interval)
        body = _status(api, workspace_url, document_id)
    return body


def _version_status(body: dict[str, Any]) -> str:
    return str(body["version"]["status"])


def test_a_real_pdf_goes_from_http_upload_to_ready_through_a_separate_worker_process(
    api: TestClient, workspace_url: str, worker_factory: Callable[[], WorkerProcess]
) -> None:
    worker = worker_factory()
    document_id = _upload(api, workspace_url, "employee-handbook.pdf", REAL_PDF.read_bytes())

    immediately = _status(api, workspace_url, document_id)
    assert _version_status(immediately) in {"pending", "processing"}, (
        "upload did not process inline"
    )

    body = _wait_for(
        api,
        workspace_url,
        document_id,
        lambda b: _version_status(b) in {"ready", "failed"},
        timeout=90,
        worker=worker,
    )

    version = body["version"]
    assert _version_status(body) == "ready", body
    assert version["page_count"] == 2
    assert version["chunk_count"] > 0
    (attempt,) = body["attempts"]
    assert (attempt["status"], attempt["stage"]) == ("succeeded", "done")
    log = worker.log()
    for stage in ("fetch", "parse", "normalize", "chunk", "embed", "index"):
        assert f'"stage": "{stage}"' in log, f"stage {stage} not observable in worker logs"


def test_killing_the_worker_mid_job_and_restarting_it_does_not_corrupt_the_document(
    api: TestClient, workspace_url: str, worker_factory: Callable[[], WorkerProcess]
) -> None:
    prose = (
        "Leases make a restart safe; fencing stops a zombie from overwriting its successor. " * 3
    )
    # Large enough that the worker is reliably still inside the job when killed.
    big_pdf = build_pdf(
        [[f"{page} Section {page}", *paragraph_lines(prose * 4)] for page in range(1, 401)]
    )

    doomed = worker_factory()
    document_id = _upload(api, workspace_url, "large.pdf", big_pdf)
    _wait_for(
        api,
        workspace_url,
        document_id,
        lambda b: any(a["status"] == "running" for a in b["attempts"]),
        timeout=90,
        worker=doomed,
        interval=0.01,
    )
    doomed.kill()

    interrupted = _status(api, workspace_url, document_id)
    assert _version_status(interrupted) == "processing"
    (attempt,) = interrupted["attempts"]
    assert attempt["status"] == "running"
    assert interrupted["version"]["chunk_count"] == 0

    # Past the dead worker's lease, a newly started worker recovers the job at
    # startup and processes the retry.
    time.sleep(LEASE_SECONDS + 1)
    restarted = worker_factory()
    body = _wait_for(
        api,
        workspace_url,
        document_id,
        lambda b: _version_status(b) in {"ready", "failed"},
        timeout=180,
        worker=restarted,
    )

    assert _version_status(body) == "ready", body
    attempts = sorted(body["attempts"], key=lambda a: a["attempt"])
    assert [(a["status"], a["error_code"]) for a in attempts] == [
        ("failed", "WORKER_LOST"),
        ("succeeded", None),
    ]
    assert body["version"]["page_count"] == 400
    assert "worker.startup_recovery_completed" in restarted.log()
