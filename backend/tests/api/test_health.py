"""Health endpoint HTTP contract.

These endpoints are consumed by an orchestrator, so the status code is the
contract: 200 means keep this instance in rotation, 503 means take it out.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.api.deps import get_check_readiness
from orbit.domain.ports.health import DependencyHealth, DependencyStatus, ReadinessReport


class StubReadiness:
    def __init__(self, report: ReadinessReport) -> None:
        self._report = report

    async def execute(self) -> ReadinessReport:
        return self._report


def _override(app: FastAPI, report: ReadinessReport) -> None:
    app.dependency_overrides[get_check_readiness] = lambda: StubReadiness(report)


@pytest.fixture
def ready_client(app: FastAPI) -> Iterator[TestClient]:
    _override(
        app,
        ReadinessReport(
            dependencies=(
                DependencyHealth("database", DependencyStatus.UP, 1.2),
                DependencyHealth("redis", DependencyStatus.UP, 0.4),
                DependencyHealth("object_storage", DependencyStatus.UP, 3.1),
            )
        ),
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def degraded_client(app: FastAPI) -> Iterator[TestClient]:
    _override(
        app,
        ReadinessReport(
            dependencies=(
                DependencyHealth("database", DependencyStatus.UP, 1.0),
                DependencyHealth("redis", DependencyStatus.DOWN, 2000.0, "timed out"),
                DependencyHealth("object_storage", DependencyStatus.UP, 2.0),
            )
        ),
    )
    with TestClient(app) as client:
        yield client


class TestLiveness:
    def test_returns_ok(self, client: TestClient) -> None:
        response = client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] and body["version"]

    def test_does_not_depend_on_any_external_service(self, client: TestClient) -> None:
        """No dependency is configured to be reachable in this test, and
        liveness must still pass -- otherwise a database blip would restart
        every healthy instance."""
        assert client.get("/healthz").status_code == 200

    def test_reports_no_dependency_information(self, client: TestClient) -> None:
        assert "dependencies" not in client.get("/healthz").json()


class TestReadiness:
    def test_returns_200_when_all_dependencies_are_up(self, ready_client: TestClient) -> None:
        response = ready_client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert {d["name"] for d in body["dependencies"]} == {
            "database",
            "redis",
            "object_storage",
        }

    def test_returns_503_when_a_dependency_is_down(self, degraded_client: TestClient) -> None:
        # The status code is what removes the instance from rotation.
        response = degraded_client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"

    def test_names_the_failing_dependency(self, degraded_client: TestClient) -> None:
        dependencies = degraded_client.get("/readyz").json()["dependencies"]
        down = [d for d in dependencies if d["status"] == "down"]
        assert [d["name"] for d in down] == ["redis"]

    def test_detail_is_generic(self, degraded_client: TestClient) -> None:
        dependencies = degraded_client.get("/readyz").json()["dependencies"]
        redis = next(d for d in dependencies if d["name"] == "redis")
        assert redis["detail"] == "timed out"
        assert "localhost" not in str(redis)


class TestRequestCorrelation:
    def test_every_response_carries_a_request_id(self, client: TestClient) -> None:
        response = client.get("/healthz")
        request_id = response.headers.get("X-Request-ID")
        assert request_id is not None
        assert len(request_id) == 26

    def test_each_request_gets_a_distinct_id(self, client: TestClient) -> None:
        first = client.get("/healthz").headers["X-Request-ID"]
        second = client.get("/healthz").headers["X-Request-ID"]
        assert first != second

    def test_client_supplied_id_is_ignored_outside_production(self, client: TestClient) -> None:
        # Accepting a caller-supplied id would let them collide their requests
        # with someone else's in the logs.
        supplied = "01JB2X8N4K7QF3TVWZ9M5PDCRA"
        returned = client.get("/healthz", headers={"X-Request-ID": supplied}).headers[
            "X-Request-ID"
        ]
        assert returned != supplied


class TestSecurityHeaders:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
        ],
    )
    def test_present_on_every_response(
        self, client: TestClient, header: str, expected: str
    ) -> None:
        assert client.get("/healthz").headers[header] == expected

    def test_content_security_policy_denies_everything(self, client: TestClient) -> None:
        csp = client.get("/healthz").headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_hsts_is_absent_outside_production(self, client: TestClient) -> None:
        # Sending HSTS over plain-HTTP localhost would pin the browser to HTTPS
        # for localhost and break every other local project on the machine.
        assert "Strict-Transport-Security" not in client.get("/healthz").headers
