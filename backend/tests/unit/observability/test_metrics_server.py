"""Serving metrics: the endpoint, and the gauges that must never lie."""

from __future__ import annotations

import asyncio
import math
import re
import socket
import sys
import urllib.request

import pytest

from orbit.composition.container import Container
from orbit.composition.metrics_server import GaugeRefresher, MetricsServer
from orbit.core.metrics import QUEUE_JOBS, QUEUE_OLDEST_READY_AGE
from tests.unit.observability.helpers import sample


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _scrape(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
        return str(response.read().decode())


class TestEndpoint:
    def test_serves_orbit_metrics_on_its_own_port(self) -> None:
        port = _free_port()
        server = MetricsServer(host="127.0.0.1", port=port)
        try:
            assert server.start() is True
            body = _scrape(port)
        finally:
            server.stop()
        assert "orbit_http_requests_total" in body
        assert "orbit_pipeline_stage_duration_seconds" in body

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows lets two sockets bind one port (SO_REUSEADDR semantics)",
    )
    def test_a_taken_port_is_not_fatal(self) -> None:
        """Metrics are a window onto the service, never part of it."""
        port = _free_port()
        first, second = (
            MetricsServer(host="127.0.0.1", port=port),
            MetricsServer(host="127.0.0.1", port=port),
        )
        try:
            assert first.start() is True
            assert second.start() is False
        finally:
            first.stop()

    def test_no_sample_carries_a_secret_or_credential(self) -> None:
        port = _free_port()
        server = MetricsServer(host="127.0.0.1", port=port)
        server.start()
        try:
            body = _scrape(port)
        finally:
            server.stop()
        # Sample lines only: `# HELP` text is prose we wrote ourselves.
        # Route templates such as `/auth/password-reset` are legitimate label
        # values, so they are removed before looking for credential-ish words.
        samples = [
            re.sub(r'route="[^"]*"', "", line)
            for line in body.splitlines()
            if not line.startswith("#")
        ]
        for forbidden in ("password", "api_key", "secret", "authorization", "bearer"):
            assert not [line for line in samples if forbidden in line.lower()]


class TestRefresher:
    async def test_a_failed_refresh_reports_unknown_not_the_last_value(self) -> None:
        QUEUE_JOBS.labels(state="ready").set(0)  # a comforting stale value
        calls = 0

        async def refresh() -> None:
            nonlocal calls
            calls += 1
            msg = "database unreachable"
            raise ConnectionError(msg)

        refresher = GaugeRefresher(
            refresh, on_failure=Container.mark_scrape_gauges_unknown, interval_seconds=60
        )

        assert await refresher.refresh_once() is False

        assert calls == 1
        assert math.isnan(sample("orbit_queue_jobs", state="ready"))
        assert math.isnan(QUEUE_OLDEST_READY_AGE._value.get())

    async def test_a_successful_refresh_returns_true_and_recovers_the_gauge(self) -> None:
        async def refresh() -> None:
            QUEUE_JOBS.labels(state="ready").set(4)

        refresher = GaugeRefresher(
            refresh, on_failure=Container.mark_scrape_gauges_unknown, interval_seconds=60
        )

        assert await refresher.refresh_once() is True
        assert sample("orbit_queue_jobs", state="ready") == 4

    async def test_the_loop_survives_failures_and_stops_cleanly(self) -> None:
        attempts = 0

        async def refresh() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                msg = "blip"
                raise ConnectionError(msg)

        refresher = GaugeRefresher(refresh, on_failure=lambda: None, interval_seconds=0.01)
        refresher.start()
        await asyncio.sleep(0.15)
        await refresher.stop()

        assert attempts >= 2, "one failure must not end the refresh loop"


@pytest.mark.parametrize("state", ["ready", "scheduled", "running", "lease_expired"])
def test_every_queue_state_is_marked_unknown_together(state: str) -> None:
    for name in ("ready", "scheduled", "running", "lease_expired"):
        QUEUE_JOBS.labels(state=name).set(1)
    Container.mark_scrape_gauges_unknown()
    assert math.isnan(sample("orbit_queue_jobs", state=state))
