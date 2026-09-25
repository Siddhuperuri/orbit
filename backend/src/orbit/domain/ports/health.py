"""Health probe port.

Readiness has to inspect PostgreSQL, Redis, and object storage, but the HTTP
layer may not import ``infrastructure`` (see backend/.importlinter). Probes are
therefore declared here as a protocol, implemented by adapters, and injected by
the composition root.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class DependencyStatus(StrEnum):
    UP = "up"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class DependencyHealth:
    name: str
    status: DependencyStatus
    latency_ms: float
    #: User-safe summary. Never a raw exception message: those carry hostnames,
    #: credentials, and driver internals.
    detail: str | None = None
    #: Whether this instance can serve *any* useful traffic without it. A
    #: non-critical dependency being down degrades a feature, not the instance.
    critical: bool = True

    @property
    def is_up(self) -> bool:
        return self.status is DependencyStatus.UP


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    dependencies: tuple[DependencyHealth, ...]

    @property
    def is_ready(self) -> bool:
        """Whether the instance should stay in the load balancer's rotation.

        Only *critical* dependencies decide this. Taking every instance out of
        rotation because Redis is down would turn a dependency ORBIT is built
        to lose (ADR-0024) into a total outage -- the failure the design says
        cannot happen (docs/operations/failure-modes.md).
        """
        return all(dependency.is_up for dependency in self.dependencies if dependency.critical)

    @property
    def is_degraded(self) -> bool:
        """Ready, but a non-critical dependency is down: serving, with a
        feature impaired. Surfaced so it is visible without paging anyone."""
        return self.is_ready and any(not dependency.is_up for dependency in self.dependencies)


@runtime_checkable
class HealthProbe(Protocol):
    """A single dependency check.

    Implementations raise :class:`orbit.domain.errors.DependencyUnavailableError`
    when the dependency is unusable, and return normally when it is healthy.
    Timing and timeout enforcement belong to the caller, so that every probe is
    measured and bounded identically.
    """

    @property
    def name(self) -> str: ...

    @property
    def critical(self) -> bool:
        """Whether this dependency being down makes the instance not ready."""
        ...

    async def check(self) -> None: ...
