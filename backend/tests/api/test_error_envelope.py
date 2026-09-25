"""The error envelope is a published contract, so its shape is pinned by tests.

The disclosure tests are the important ones: they assert that internals which
would be genuinely useful to an attacker -- stack traces, exception text,
module paths -- never cross the HTTP boundary.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.domain.errors import NotFoundError, PermissionDeniedError

_REQUIRED_KEYS = {"code", "message", "request_id", "details"}


@pytest.fixture
def error_client(app: FastAPI) -> Iterator[TestClient]:
    """Client with routes that raise, so the handlers can be exercised."""

    @app.get("/_test/domain-error")
    async def _domain_error() -> None:
        raise NotFoundError(
            "The requested document could not be found.",
            document_id="doc_123",
            workspace_id="ws_456",
        )

    @app.get("/_test/permission-error")
    async def _permission_error() -> None:
        msg = "You do not have permission to perform this action."
        raise PermissionDeniedError(msg)

    @app.get("/_test/unexpected")
    async def _unexpected() -> None:
        # Message deliberately resembles something that must never be returned.
        msg = "connection to postgres://orbit:hunter2@db.internal:5432 failed"
        raise RuntimeError(msg)

    @app.get("/_test/validated")
    async def _validated(count: int) -> dict[str, int]:
        return {"count": count}

    # raise_server_exceptions=False makes TestClient return the 500 response the
    # handler produced instead of re-raising, which is what a real client sees.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestEnvelopeShape:
    def test_domain_error_uses_the_envelope(self, error_client: TestClient) -> None:
        response = error_client.get("/_test/domain-error")
        assert response.status_code == 404
        body = response.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == _REQUIRED_KEYS

    def test_code_is_the_machine_readable_half(self, error_client: TestClient) -> None:
        body = error_client.get("/_test/domain-error").json()
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["error"]["message"] == "The requested document could not be found."

    def test_request_id_is_present_and_matches_the_header(self, error_client: TestClient) -> None:
        response = error_client.get("/_test/domain-error")
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]

    def test_details_is_present_but_null_for_non_validation_errors(
        self, error_client: TestClient
    ) -> None:
        # The shape is identical for every error so clients need one parser.
        assert error_client.get("/_test/domain-error").json()["error"]["details"] is None

    def test_permission_denied_maps_to_403(self, error_client: TestClient) -> None:
        response = error_client.get("/_test/permission-error")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"


class TestValidationErrors:
    def test_returns_422_with_field_details(self, error_client: TestClient) -> None:
        response = error_client.get("/_test/validated", params={"count": "not-a-number"})
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"], "validation errors must carry field-level detail"

    def test_field_paths_are_dotted(self, error_client: TestClient) -> None:
        details = error_client.get("/_test/validated").json()["error"]["details"]
        assert any("count" in detail["field"] for detail in details)


class TestUnhandledExceptions:
    def test_returns_a_generic_500(self, error_client: TestClient) -> None:
        response = error_client.get("/_test/unexpected")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"

    def test_never_discloses_the_exception_text(self, error_client: TestClient) -> None:
        raw = error_client.get("/_test/unexpected").text
        for secret in ("hunter2", "db.internal", "postgres://", "RuntimeError", "Traceback"):
            assert secret not in raw, f"{secret!r} leaked into the response body"

    def test_still_returns_a_request_id_for_support(self, error_client: TestClient) -> None:
        # The request id is the only thread connecting what the user saw to the
        # logged exception.
        assert len(error_client.get("/_test/unexpected").json()["error"]["request_id"]) == 26


class TestFrameworkErrors:
    def test_unknown_route_uses_the_same_envelope(self, error_client: TestClient) -> None:
        # Without a handler for Starlette's own exceptions this would return
        # `{"detail": "Not Found"}` and clients would need two parsers.
        response = error_client.get("/_test/does-not-exist")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_method_not_allowed_uses_the_same_envelope(self, error_client: TestClient) -> None:
        response = error_client.post("/healthz")
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


class TestErrorContextIsNotDisclosed:
    def test_structured_context_stays_in_logs(self, error_client: TestClient) -> None:
        # document_id and workspace_id are attached for the operator's logs and
        # must not appear in the response.
        raw = error_client.get("/_test/domain-error").text
        assert "doc_123" not in raw
        assert "ws_456" not in raw
