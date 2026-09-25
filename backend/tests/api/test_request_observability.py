"""What one HTTP request leaves behind: a correlated log line, metrics, headers.

Each test drives a small real ASGI stack -- the production middleware and the
production exception handlers -- because the behaviours under test (context
crossing task boundaries, timing a stream to its last byte, handling an
exception outside the middleware) only exist in the assembled stack.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from orbit.api.deps import bind_request_context
from orbit.api.errors import register_exception_handlers
from orbit.api.middleware.request_context import UNMATCHED_ROUTE, RequestContextMiddleware
from orbit.core.config import LogFormat
from orbit.core.ids import new_ulid
from orbit.core.logging import bind_correlation, clear_correlation, configure_logging
from orbit.domain.errors import StorageUnavailableError
from tests.conftest import build_settings
from tests.unit.observability.helpers import sample

WORKSPACE = "0f8fad5b-d9cb-469f-a165-70867728950e"
DOCUMENT = "7c9e6679-7425-40de-944b-e07fc1f90ae7"

router = APIRouter(prefix="/w/{workspace_id}")


@router.get("/docs/{document_id}")
async def show_document(workspace_id: str, document_id: str) -> dict[str, str]:
    bind_correlation(user_id="user-1")
    return {"ok": "yes"}


@router.get("/stream")
async def stream(workspace_id: str) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        for _ in range(3):
            await asyncio.sleep(0.05)
            yield b"chunk"

    return StreamingResponse(body(), media_type="text/event-stream")


@router.get("/storage-down")
async def storage_down(workspace_id: str) -> None:
    raise StorageUnavailableError("Object storage is unreachable.", bucket="b")


@router.get("/boom")
async def boom(workspace_id: str) -> None:
    raise RuntimeError("secret internal detail")


@router.get("/slow")
async def slow(workspace_id: str) -> dict[str, str]:
    await asyncio.sleep(0.06)
    return {"ok": "yes"}


class _PassThrough(BaseHTTPMiddleware):
    """Stands in for CSRF/security-headers: runs the app in a child task."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        return await call_next(request)


def build_app(*, slow_threshold_ms: float = 2000.0) -> FastAPI:
    app = FastAPI()
    app.include_router(router, dependencies=[Depends(bind_request_context)])
    register_exception_handlers(app)
    app.add_middleware(_PassThrough)
    app.add_middleware(
        RequestContextMiddleware,
        trust_incoming_header=True,
        slow_request_threshold_ms=slow_threshold_ms,
    )
    return app


class _Recorder(logging.Handler):
    """Collects the structured event dict of every record.

    Reads the event *after* ORBIT's processors ran (context merged, secrets
    redacted) and *before* rendering, so it sees exactly the fields a log
    backend would index -- without depending on where stderr happens to point.
    """

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(record.msg, dict):
            self.events.append(record.msg)


@pytest.fixture(autouse=True)
def records() -> Iterator[_Recorder]:
    clear_correlation()
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="INFO"))
    recorder = _Recorder()
    logging.getLogger().addHandler(recorder)
    yield recorder
    logging.getLogger().removeHandler(recorder)
    clear_correlation()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(build_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def _access_lines(recorder: _Recorder) -> list[dict[str, object]]:
    return [e for e in recorder.events if e["event"] == "http.request"]


class TestAccessLog:
    def test_one_line_per_request_naming_who_and_what(
        self, client: TestClient, records: _Recorder
    ) -> None:
        client.get(f"/w/{WORKSPACE}/docs/{DOCUMENT}")

        (line,) = _access_lines(records)
        assert line["method"] == "GET"
        assert line["status_code"] == 200
        assert line["route"] == "/w/{workspace_id}/docs/{document_id}"
        # From the route's path parameters, bound by the dependency...
        assert line["workspace_id"] == WORKSPACE
        assert line["document_id"] == DOCUMENT
        assert line["operation"] == "test_request_observability.show_document"
        # ...and from the handler, which ran in a child task.
        assert line["user_id"] == "user-1"
        assert line["request_id"]
        assert isinstance(line["duration_ms"], float)

    def test_malformed_identifiers_in_the_path_are_never_bound(
        self, client: TestClient, records: _Recorder
    ) -> None:
        """Path parameters are user input; only a value that parses as a UUID may
        become a log field."""
        client.get("/w/not-a-uuid/docs/injected%20value")

        (line,) = _access_lines(records)
        assert "workspace_id" not in line
        assert "document_id" not in line

    def test_a_client_request_id_is_honoured_only_when_it_is_valid(
        self, client: TestClient
    ) -> None:
        valid = new_ulid()
        honoured = client.get("/w/a/stream", headers={"X-Request-ID": valid})
        assert honoured.headers["X-Request-ID"] == valid

        replaced = client.get("/w/a/slow", headers={"X-Request-ID": "not-a-ulid"})
        assert replaced.headers["X-Request-ID"] != "not-a-ulid"
        assert len(replaced.headers["X-Request-ID"]) == 26

    def test_a_slow_request_is_logged_at_warning(self, records: _Recorder) -> None:
        with TestClient(build_app(slow_threshold_ms=10)) as slow_client:
            slow_client.get("/w/a/slow")
        (line,) = _access_lines(records)
        assert line["level"] == "warning"
        assert line["slow"] is True

    def test_health_probes_are_not_logged(self, records: _Recorder) -> None:
        app = build_app()

        @app.get("/healthz")
        async def healthz() -> dict[str, str]:
            return {"status": "ok"}

        with TestClient(app) as probe_client:
            probe_client.get("/healthz")
        assert _access_lines(records) == []


class TestMetrics:
    def test_counts_by_route_template_never_by_url(self, client: TestClient) -> None:
        template = "/w/{workspace_id}/docs/{document_id}"
        before = sample("orbit_http_requests_total", method="GET", route=template, status="200")
        client.get(f"/w/{WORKSPACE}/docs/{DOCUMENT}")
        client.get(f"/w/{new_ulid()}/docs/{new_ulid()}")
        after = sample("orbit_http_requests_total", method="GET", route=template, status="200")
        assert after == before + 2

    def test_unmatched_paths_share_one_series(self, client: TestClient) -> None:
        """A scanner requesting a million distinct URLs must not mint a million series."""
        labels = {"method": "GET", "route": UNMATCHED_ROUTE, "status": "404"}
        before = sample("orbit_http_requests_total", **labels)
        for probe in ("/wp-admin", "/.env", f"/{new_ulid()}"):
            assert client.get(probe).status_code == 404
        assert sample("orbit_http_requests_total", **labels) == before + 3

    def test_a_streamed_response_is_timed_to_its_last_byte(self, client: TestClient) -> None:
        """Three 50 ms chunks: ~150 ms. Timed to the first byte it would read ~0."""
        labels = {"method": "GET", "route": "/w/{workspace_id}/stream"}
        before = sample("orbit_http_request_duration_seconds_sum", **labels)
        client.get("/w/a/stream")
        assert sample("orbit_http_request_duration_seconds_sum", **labels) - before >= 0.14

    def test_in_flight_returns_to_its_starting_value(self, client: TestClient) -> None:
        before = sample("orbit_http_requests_in_flight")
        client.get("/w/a/slow")
        client.get("/w/a/boom")
        assert sample("orbit_http_requests_in_flight") == before

    def test_error_codes_are_counted(self, client: TestClient) -> None:
        before = sample("orbit_http_errors_total", code="STORAGE_UNAVAILABLE")
        response = client.get("/w/a/storage-down")
        assert response.status_code == 503
        assert sample("orbit_http_errors_total", code="STORAGE_UNAVAILABLE") == before + 1


class TestUnhandledException:
    def test_the_client_gets_only_a_request_id(self, client: TestClient) -> None:
        response = client.get("/w/a/boom")
        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert "secret internal detail" not in response.text
        assert body["error"]["request_id"] == response.headers["X-Request-ID"]

    def test_the_traceback_record_carries_the_id_the_client_was_told(
        self, client: TestClient, records: _Recorder
    ) -> None:
        """The handler runs after the request middleware has cleared its context.
        Without the hand-off, the one record holding the traceback is the one
        record that cannot be found by the id the user quotes."""
        response = client.get("/w/a/boom")
        request_id = response.json()["error"]["request_id"]

        (failure,) = [e for e in records.events if e["event"] == "request.unhandled_exception"]
        assert failure["request_id"] == request_id
        assert failure["exc_info"] is True

    def test_it_is_counted_as_a_500_with_an_error_type(
        self, client: TestClient, records: _Recorder
    ) -> None:
        labels = {"method": "GET", "route": "/w/{workspace_id}/boom", "status": "500"}
        before = sample("orbit_http_requests_total", **labels)
        client.get("/w/a/boom")
        assert sample("orbit_http_requests_total", **labels) == before + 1
        (line,) = _access_lines(records)
        assert line["status_code"] == 500
        assert line["error_type"] == "RuntimeError"


def test_the_request_id_is_echoed_on_every_response(client: TestClient) -> None:
    for path in ("/w/a/slow", "/w/a/storage-down", "/nope"):
        assert len(client.get(path).headers["X-Request-ID"]) == 26


async def test_the_scope_carries_the_request_id_for_downstream_code() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(Request(scope).state.request_id)
        await Response("ok")(scope, receive, send)

    app = RequestContextMiddleware(inner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.get("/")
    assert seen == [response.headers["X-Request-ID"]]
