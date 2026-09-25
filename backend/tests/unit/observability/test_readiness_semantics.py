"""Readiness: which dependency failures remove an instance from rotation.

The rule under test is the one docs/operations/failure-modes.md states: a
dependency failure degrades the feature that needs it and nothing else. If
Redis being down made every instance unready, "we lose Redis and everything
keeps working" would be false in exactly the situation it was written for.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.api.deps import get_check_readiness
from orbit.application.health.check_readiness import CheckReadiness
from orbit.domain.errors import (
    AIProviderUnavailableError,
    DatabaseUnavailableError,
    DependencyUnavailableError,
)
from orbit.domain.ports.health import DependencyHealth, DependencyStatus, ReadinessReport
from orbit.infrastructure.ai.resilient_llm import CircuitBreaker
from orbit.infrastructure.health import (
    DatabaseProbe,
    EmbeddingSchemaProbe,
    LanguageModelCircuitProbe,
    ObjectStorageProbe,
    RedisProbe,
)
from tests.unit.observability.helpers import sample


class Probe:
    def __init__(self, name: str, *, critical: bool, healthy: bool = True) -> None:
        self._name, self.critical, self._healthy = name, critical, healthy

    @property
    def name(self) -> str:
        return self._name

    async def check(self) -> None:
        if not self._healthy:
            msg = "down"
            raise DependencyUnavailableError(msg)


class TestReport:
    async def test_a_critical_dependency_down_is_not_ready(self) -> None:
        report = await CheckReadiness(
            [Probe("database", critical=True, healthy=False), Probe("redis", critical=False)]
        ).execute()
        assert report.is_ready is False
        assert report.is_degraded is False

    async def test_a_non_critical_dependency_down_is_degraded_but_ready(self) -> None:
        report = await CheckReadiness(
            [Probe("database", critical=True), Probe("redis", critical=False, healthy=False)]
        ).execute()
        assert report.is_ready is True, "an instance must keep serving without Redis"
        assert report.is_degraded is True

    async def test_everything_up_is_ready_and_not_degraded(self) -> None:
        report = await CheckReadiness([Probe("database", critical=True)]).execute()
        assert (report.is_ready, report.is_degraded) == (True, False)

    async def test_the_report_says_which_dependencies_are_critical(self) -> None:
        report = await CheckReadiness(
            [Probe("database", critical=True), Probe("redis", critical=False)]
        ).execute()
        assert {d.name: d.critical for d in report.dependencies} == {
            "database": True,
            "redis": False,
        }

    async def test_a_probe_that_does_not_say_is_treated_as_critical(self) -> None:
        """The safe default: unready rather than wrongly ready."""

        class Undeclared:
            name = "mystery"

            async def check(self) -> None:
                msg = "down"
                raise DependencyUnavailableError(msg)

        report = await CheckReadiness([Undeclared()]).execute()  # type: ignore[list-item]
        assert report.is_ready is False


class TestProductionProbes:
    """Which of ORBIT's real dependencies may be lost without leaving rotation."""

    def test_criticality_matches_the_documented_failure_modes(self) -> None:
        assert DatabaseProbe.critical is True
        assert EmbeddingSchemaProbe.critical is True
        assert RedisProbe.critical is False
        assert ObjectStorageProbe.critical is False
        assert LanguageModelCircuitProbe.critical is False


class TestDependencyMetrics:
    async def test_up_and_latency_are_exported_per_dependency(self) -> None:
        await CheckReadiness([Probe("metrics_probe_ok", critical=True)]).execute()
        assert sample("orbit_dependency_up", dependency="metrics_probe_ok") == 1
        assert (
            sample("orbit_dependency_check_duration_seconds_count", dependency="metrics_probe_ok")
            >= 1
        )

    async def test_a_failing_dependency_reads_zero(self) -> None:
        await CheckReadiness([Probe("metrics_probe_bad", critical=False, healthy=False)]).execute()
        assert sample("orbit_dependency_up", dependency="metrics_probe_bad") == 0

    async def test_recovery_flips_it_back(self) -> None:
        probe = Probe("metrics_probe_flap", critical=True, healthy=False)
        readiness = CheckReadiness([probe])
        await readiness.execute()
        assert sample("orbit_dependency_up", dependency="metrics_probe_flap") == 0
        probe._healthy = True
        await readiness.execute()
        assert sample("orbit_dependency_up", dependency="metrics_probe_flap") == 1


class TestLanguageModelCircuitProbe:
    async def test_passes_while_the_circuit_is_closed(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2, reset_after_seconds=30)
        await LanguageModelCircuitProbe(breaker).check()

    async def test_fails_while_the_circuit_is_open_without_calling_the_provider(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, reset_after_seconds=30)
        breaker.record_failure(AIProviderUnavailableError("down"))
        assert breaker.is_open

        with pytest.raises(DependencyUnavailableError):
            await LanguageModelCircuitProbe(breaker).check()
        assert sample("orbit_llm_circuit_open") == 1

    async def test_closing_the_circuit_clears_the_gauge(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, reset_after_seconds=30)
        breaker.record_failure(AIProviderUnavailableError("down"))
        breaker.record_success()
        assert sample("orbit_llm_circuit_open") == 0


class TestEndpoint:
    """The HTTP contract an orchestrator acts on."""

    @staticmethod
    def _client(app: FastAPI, report: ReadinessReport) -> Iterator[TestClient]:
        class Stub:
            async def execute(self) -> ReadinessReport:
                return report

        app.dependency_overrides[get_check_readiness] = Stub
        with TestClient(app) as client:
            yield client

    @pytest.fixture
    def degraded(self, app: FastAPI) -> Iterator[TestClient]:
        yield from self._client(
            app,
            ReadinessReport(
                (
                    DependencyHealth("database", DependencyStatus.UP, 1.0, critical=True),
                    DependencyHealth(
                        "redis", DependencyStatus.DOWN, 2000.0, "timed out", critical=False
                    ),
                )
            ),
        )

    @pytest.fixture
    def not_ready(self, app: FastAPI) -> Iterator[TestClient]:
        yield from self._client(
            app,
            ReadinessReport(
                (
                    DependencyHealth(
                        "database", DependencyStatus.DOWN, 2000.0, "unavailable", critical=True
                    ),
                    DependencyHealth("redis", DependencyStatus.UP, 1.0, critical=False),
                )
            ),
        )

    def test_a_lost_non_critical_dependency_keeps_the_instance_in_rotation(
        self, degraded: TestClient
    ) -> None:
        response = degraded.get("/readyz")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"

    def test_the_body_names_what_is_down_and_whether_it_matters(self, degraded: TestClient) -> None:
        by_name = {d["name"]: d for d in degraded.get("/readyz").json()["dependencies"]}
        assert by_name["redis"]["status"] == "down"
        assert by_name["redis"]["critical"] is False
        assert by_name["database"]["critical"] is True

    def test_a_lost_critical_dependency_removes_the_instance(self, not_ready: TestClient) -> None:
        response = not_ready.get("/readyz")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"


def test_database_failures_are_domain_errors_the_probe_reports() -> None:
    """`DatabaseUnavailableError` is a `DependencyUnavailableError`: readiness
    reports it as down rather than as a defect in the probe."""
    assert issubclass(DatabaseUnavailableError, DependencyUnavailableError)
