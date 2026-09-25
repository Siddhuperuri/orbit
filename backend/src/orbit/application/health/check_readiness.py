"""Readiness use case.

Liveness and readiness answer different questions, and conflating them causes
outages. ``/healthz`` asks "is this process alive?" and checks nothing external;
``/readyz`` asks "can this process serve traffic?" and checks its dependencies.

A liveness probe that touched the database would restart every API pod during a
database blip, converting a recoverable dependency failure into a self-inflicted
restart storm. See docs/decisions/0015-observability-strategy.md.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import replace

from orbit.core.logging import get_logger
from orbit.core.metrics import DEPENDENCY_CHECK_DURATION, DEPENDENCY_UP
from orbit.domain.errors import DependencyUnavailableError
from orbit.domain.ports.health import (
    DependencyHealth,
    DependencyStatus,
    HealthProbe,
    ReadinessReport,
)

logger = get_logger(__name__)

# A readiness check must answer faster than the orchestrator's probe timeout, or
# the orchestrator concludes the instance is dead for reasons unrelated to its
# dependencies. Kept deliberately tight: a slow dependency is an unready one.
_PROBE_TIMEOUT_SECONDS = 2.0


class CheckReadiness:
    """Runs every dependency probe concurrently and reports the outcome."""

    def __init__(self, probes: Sequence[HealthProbe]) -> None:
        self._probes = tuple(probes)

    async def execute(self) -> ReadinessReport:
        results = await asyncio.gather(*(self._run(probe) for probe in self._probes))
        for result in results:
            # The orchestrator polls this every few seconds, so these series are
            # a continuous record of each dependency's state and latency --
            # what to alert on, instead of scraping /readyz.
            DEPENDENCY_UP.labels(dependency=result.name).set(1 if result.is_up else 0)
            DEPENDENCY_CHECK_DURATION.labels(dependency=result.name).observe(
                result.latency_ms / 1000
            )
        return ReadinessReport(dependencies=tuple(results))

    async def _run(self, probe: HealthProbe) -> DependencyHealth:
        result = await self._probe(probe)
        # Stamped here so a probe cannot forget to say, and a probe that omits
        # the attribute (a test double) is treated as critical -- the safe
        # default: unready rather than wrongly ready.
        critical: bool = getattr(probe, "critical", True)
        return replace(result, critical=critical)

    async def _probe(self, probe: HealthProbe) -> DependencyHealth:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
                await probe.check()
        except TimeoutError:
            elapsed = (time.perf_counter() - started) * 1000
            logger.warning(
                "readiness.probe_timeout", dependency=probe.name, timeout_s=_PROBE_TIMEOUT_SECONDS
            )
            return DependencyHealth(
                name=probe.name,
                status=DependencyStatus.DOWN,
                latency_ms=round(elapsed, 2),
                detail="timed out",
            )
        except DependencyUnavailableError as exc:
            elapsed = (time.perf_counter() - started) * 1000
            # `exc_info` carries the cause for the operator; the response body
            # gets only the generic detail below.
            logger.warning(
                "readiness.probe_failed", dependency=probe.name, error_code=exc.code, exc_info=exc
            )
            return DependencyHealth(
                name=probe.name,
                status=DependencyStatus.DOWN,
                latency_ms=round(elapsed, 2),
                detail="unavailable",
            )
        except Exception:
            # An unexpected exception from a probe is a defect in the probe, not
            # evidence the dependency is healthy. Report DOWN and log loudly
            # rather than letting readiness pass on a broken check.
            elapsed = (time.perf_counter() - started) * 1000
            logger.exception("readiness.probe_error", dependency=probe.name)
            return DependencyHealth(
                name=probe.name,
                status=DependencyStatus.DOWN,
                latency_ms=round(elapsed, 2),
                detail="check failed",
            )

        elapsed = (time.perf_counter() - started) * 1000
        return DependencyHealth(
            name=probe.name,
            status=DependencyStatus.UP,
            latency_ms=round(elapsed, 2),
        )
