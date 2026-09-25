"""Serving metrics: the scrape endpoint and the gauges that need refreshing.

Two independent pieces, both deliberately small.

**The endpoint** is ``prometheus_client``'s own HTTP server on a *separate
port*, bound to ``ORBIT_METRICS_HOST`` (ADR-0015). It is not a route on the
public API: a metrics route on the public port is one ingress rule away from
being an unauthenticated view of internal state, whereas a separate port is
structurally unreachable unless someone routes it on purpose.

**The refresher** exists because a few gauges (queue depth, connection-pool
occupancy) are *state read from somewhere*, not events counted as they happen.
Rather than making every scrape run a database query -- which couples scrape
latency to database health, and multiplies query load by the number of scrapers
-- a background task refreshes them on an interval, and the scrape just renders
what is there. If a refresh fails the gauges are set to NaN, not left at their
last value: a stale "queue depth 0" during a database outage is a lie that
looks like good news.

Neither piece may ever take the process down. Metrics are a window onto the
service, not part of it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from prometheus_client import CollectorRegistry, start_http_server

from orbit.core.logging import get_logger
from orbit.core.metrics import serving_registry

logger = get_logger(__name__)


class MetricsServer:
    """Serves the metrics registry on ``host:port`` in a daemon thread."""

    def __init__(self, *, host: str, port: int, registry: CollectorRegistry | None = None) -> None:
        self._host = host
        self._port = port
        self._registry = registry
        self._server: Any = None

    def start(self) -> bool:
        """Start serving. Returns whether this process is now the server.

        A bind failure is not an error. With several API processes on one host
        the first to bind serves, and -- when ``PROMETHEUS_MULTIPROC_DIR`` is
        set -- what it serves is every process's samples, so which one wins does
        not matter. Without multiprocess mode the winner serves only its own
        samples; the runbook says so.
        """
        try:
            server, _thread = start_http_server(
                self._port, addr=self._host, registry=self._registry or serving_registry()
            )
        except OSError as exc:
            logger.info(
                "metrics.not_serving",
                host=self._host,
                port=self._port,
                reason=type(exc).__name__,
            )
            return False
        self._server = server
        logger.info("metrics.serving", host=self._host, port=self._port)
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None


class GaugeRefresher:
    """Runs ``refresh`` every ``interval_seconds`` until stopped."""

    def __init__(
        self,
        refresh: Callable[[], Awaitable[None]],
        *,
        on_failure: Callable[[], None],
        interval_seconds: float,
    ) -> None:
        self._refresh = refresh
        self._on_failure = on_failure
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="metrics-gauge-refresher")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def refresh_once(self) -> bool:
        """One refresh. Never raises; returns whether it succeeded."""
        try:
            await self._refresh()
        except Exception as exc:
            # Warning, not exception: during a database outage this fires every
            # interval, and the traceback adds nothing readiness does not say.
            logger.warning("metrics.refresh_failed", error=type(exc).__name__)
            self._on_failure()
            return False
        return True

    async def _run(self) -> None:
        while True:
            await self.refresh_once()
            await asyncio.sleep(self._interval)
