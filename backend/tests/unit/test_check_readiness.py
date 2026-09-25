"""Readiness use case.

The failure paths matter more than the happy path here: a readiness check that
reports "up" when a probe hangs or raises is worse than having no probe at all,
because it keeps a broken instance in the load balancer's rotation.
"""

from __future__ import annotations

import asyncio

import pytest

from orbit.application.health.check_readiness import CheckReadiness
from orbit.domain.errors import DatabaseUnavailableError
from orbit.domain.ports.health import DependencyStatus, HealthProbe


class HealthyProbe:
    critical = True

    def __init__(self, name: str = "healthy") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def check(self) -> None:
        return None


class FailingProbe:
    critical = True

    @property
    def name(self) -> str:
        return "failing"

    async def check(self) -> None:
        msg = "PostgreSQL is unreachable."
        raise DatabaseUnavailableError(msg, host="db.internal", port=5432)


class HangingProbe:
    critical = True

    @property
    def name(self) -> str:
        return "hanging"

    async def check(self) -> None:
        await asyncio.sleep(30)


class ExplodingProbe:
    """A probe with a bug in it, rather than a dependency that is down."""

    critical = True

    @property
    def name(self) -> str:
        return "exploding"

    async def check(self) -> None:
        raise RuntimeError("probe implementation is broken")


async def test_reports_ready_when_every_probe_passes() -> None:
    report = await CheckReadiness([HealthyProbe("database"), HealthyProbe("redis")]).execute()

    assert report.is_ready is True
    assert [d.name for d in report.dependencies] == ["database", "redis"]
    assert all(d.status is DependencyStatus.UP for d in report.dependencies)


async def test_a_single_failure_makes_the_instance_not_ready() -> None:
    report = await CheckReadiness([HealthyProbe(), FailingProbe()]).execute()

    assert report.is_ready is False
    failing = next(d for d in report.dependencies if d.name == "failing")
    assert failing.status is DependencyStatus.DOWN


async def test_failure_detail_never_leaks_dependency_internals() -> None:
    """The probe error carries a host and port; the report must not."""
    report = await CheckReadiness([FailingProbe()]).execute()

    detail = report.dependencies[0].detail
    assert detail == "unavailable"
    assert detail is not None
    assert "db.internal" not in detail
    assert "5432" not in detail


async def test_a_hanging_probe_times_out_rather_than_hanging_readiness() -> None:
    # Without a timeout the orchestrator's own probe deadline would expire and
    # the instance would be judged dead for the wrong reason.
    report = await CheckReadiness([HangingProbe()]).execute()

    assert report.is_ready is False
    assert report.dependencies[0].detail == "timed out"


async def test_a_broken_probe_reports_down_rather_than_passing() -> None:
    # An unexpected exception is not evidence the dependency is healthy.
    report = await CheckReadiness([ExplodingProbe()]).execute()

    assert report.is_ready is False
    assert report.dependencies[0].detail == "check failed"


async def test_probes_run_concurrently() -> None:
    """Sequential probes would multiply latency by the number of dependencies."""

    class SlowProbe:
        critical = True

        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        async def check(self) -> None:
            await asyncio.sleep(0.15)

    probes: list[HealthProbe] = [SlowProbe(f"dep{i}") for i in range(4)]
    loop = asyncio.get_running_loop()
    started = loop.time()
    await CheckReadiness(probes).execute()
    elapsed = loop.time() - started

    # Four 150 ms probes: ~0.15 s concurrently, ~0.6 s sequentially.
    assert elapsed < 0.4, f"probes appear to run sequentially ({elapsed:.2f}s)"


async def test_no_probes_is_ready() -> None:
    report = await CheckReadiness([]).execute()
    assert report.is_ready is True


@pytest.mark.parametrize("probe_count", [1, 3])
async def test_latency_is_recorded_for_every_dependency(probe_count: int) -> None:
    report = await CheckReadiness([HealthyProbe(f"d{i}") for i in range(probe_count)]).execute()
    assert len(report.dependencies) == probe_count
    assert all(d.latency_ms >= 0 for d in report.dependencies)
