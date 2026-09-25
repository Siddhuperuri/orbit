"""Worker-side metrics and log context: what an operator can see about a document's
journey through the pipeline without reproducing it.

Runs the production pipeline on the in-memory harness, so the metrics asserted
here are emitted by the same code that runs in the worker.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import timedelta

import pytest

from orbit.application.processing.lifecycle import JobOutcome
from orbit.application.processing.queue_snapshot import ReadQueueSnapshot
from orbit.core.logging import clear_correlation, configure_logging
from orbit.domain.errors import AIProviderUnavailableError
from tests.conftest import build_settings
from tests.unit.observability.helpers import sample
from tests.unit.processing.harness import LEASE, WorkerKilledError, build_pipeline

PROSE = (
    "Refresh tokens rotate on every use. A reused token revokes its whole family, "
    "which contains the damage a stolen token can do. Sessions expire after thirty days. "
)


@pytest.fixture
def events() -> Iterator[list[dict[str, object]]]:
    configure_logging(build_settings(log_level="DEBUG"))
    captured: list[dict[str, object]] = []

    class Recorder(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if isinstance(record.msg, dict):
                captured.append(dict(record.msg))

    handler = Recorder()
    logging.getLogger().addHandler(handler)
    yield captured
    logging.getLogger().removeHandler(handler)
    clear_correlation()


def _stage(stage: str, outcome: str) -> float:
    return sample("orbit_pipeline_stage_duration_seconds_count", stage=stage, outcome=outcome)


def _failures(code: str, kind: str, terminal: str) -> float:
    return sample(
        "orbit_processing_failures_total", error_code=code, failure_kind=kind, terminal=terminal
    )


class TestSuccessfulRun:
    async def test_every_stage_is_timed(self) -> None:
        stages = ("fetch", "parse", "normalize", "chunk", "embed", "index")
        before = {stage: _stage(stage, "ok") for stage in stages}

        pipeline = await build_pipeline()
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        await pipeline.drain()

        for stage in stages:
            assert _stage(stage, "ok") == before[stage] + 1, stage

    async def test_the_execution_is_counted_and_timed_by_format(self) -> None:
        executions = sample("orbit_job_executions_total", outcome="succeeded")
        durations = sample(
            "orbit_document_processing_duration_seconds_count", format="text", outcome="succeeded"
        )

        pipeline = await build_pipeline()
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        await pipeline.drain()

        assert (
            sample(
                "orbit_document_processing_duration_seconds_count",
                format="text",
                outcome="succeeded",
            )
            == durations + 1
        )
        # `orbit_job_executions_total` is counted by the task runtime, which the
        # harness bypasses; asserting it is untouched proves nothing is counted twice.
        assert sample("orbit_job_executions_total", outcome="succeeded") == executions

    async def test_the_queue_wait_is_measured_at_claim(self) -> None:
        before = sample("orbit_job_queue_wait_seconds_count")
        pipeline = await build_pipeline()
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        await pipeline.drain()
        assert sample("orbit_job_queue_wait_seconds_count") == before + 1

    async def test_upload_to_ready_latency_is_recorded_for_a_first_run(self) -> None:
        before = sample("orbit_document_ready_latency_seconds_count")
        pipeline = await build_pipeline()
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        await pipeline.drain()
        assert sample("orbit_document_ready_latency_seconds_count") == before + 1

    async def test_a_manual_reprocess_does_not_pollute_the_latency_the_slo_is_written_against(
        self,
    ) -> None:
        """A document reprocessed a day later would otherwise record a day of
        'latency' and bury what an upload actually waits for."""
        pipeline = await build_pipeline(max_attempts=1)
        pipeline.embedder.failures.append(AIProviderUnavailableError("down"))
        document = await pipeline.upload("a.txt", (PROSE * 4).encode())
        await pipeline.drain()  # exhausts its one attempt
        pipeline.clock.advance(timedelta(days=1))
        await pipeline.reprocess.execute(pipeline.ctx, document.id)

        before = sample("orbit_document_ready_latency_seconds_count")
        await pipeline.drain()

        assert pipeline.version(document).status.value == "ready"
        assert sample("orbit_document_ready_latency_seconds_count") == before


class TestFailures:
    async def test_a_permanent_failure_is_counted_by_code_and_marked_terminal(self) -> None:
        before = _failures("DOCUMENT_EMPTY", "permanent", "true")
        pipeline = await build_pipeline()
        await pipeline.upload("blank.txt", b" \n\n\t \n")
        await pipeline.drain()
        assert _failures("DOCUMENT_EMPTY", "permanent", "true") == before + 1

    async def test_a_transient_failure_is_counted_as_non_terminal_then_terminal(self) -> None:
        retry_before = _failures("AI_PROVIDER_UNAVAILABLE", "transient", "false")
        terminal_before = _failures("AI_PROVIDER_UNAVAILABLE", "transient", "true")
        pipeline = await build_pipeline(max_attempts=2)
        pipeline.embedder.failures.extend(
            [AIProviderUnavailableError("503"), AIProviderUnavailableError("503")]
        )
        await pipeline.upload("a.txt", (PROSE * 4).encode())

        await pipeline.drain()

        assert _failures("AI_PROVIDER_UNAVAILABLE", "transient", "false") == retry_before + 1
        assert _failures("AI_PROVIDER_UNAVAILABLE", "transient", "true") == terminal_before + 1

    async def test_a_failing_stage_is_timed_as_an_error_and_named_in_the_log(
        self, events: list[dict[str, object]]
    ) -> None:
        before = _stage("embed", "error")
        pipeline = await build_pipeline()
        pipeline.embedder.failures.append(AIProviderUnavailableError("503"))
        await pipeline.upload("a.txt", (PROSE * 4).encode())

        await pipeline.drain()

        assert _stage("embed", "error") == before + 1
        (failure,) = [e for e in events if e["event"] == "pipeline.attempt_failed"]
        # Where it failed and how far it got: enough to see "embedding failed
        # after parse took 40 ms" without reproducing anything.
        assert failure["failed_stage"] == "embed"
        assert set(failure["stages_ms"]) == {"fetch", "parse", "normalize", "chunk"}  # type: ignore[call-overload]
        assert failure["error_code"] == "AI_PROVIDER_UNAVAILABLE"

    async def test_the_document_text_is_never_in_a_failure_record(
        self, events: list[dict[str, object]]
    ) -> None:
        pipeline = await build_pipeline()
        pipeline.embedder.failures.append(KeyError("internal_column_name"))
        await pipeline.upload("a.txt", b"the confidential merger terms " * 20)
        await pipeline.drain()
        assert "confidential merger" not in repr(events)


class TestRecovery:
    async def test_an_abandoned_job_is_counted(self) -> None:
        before = sample("orbit_job_recoveries_total", kind="abandoned")
        pipeline = await build_pipeline()
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        (job_id,) = pipeline.dispatched_job_ids()
        pipeline.embedder.before_return = _die
        with pytest.raises(WorkerKilledError):
            await pipeline.run(job_id)
        pipeline.clock.advance(LEASE + timedelta(seconds=1))

        report = await pipeline.recovery.execute()

        assert report.abandoned_recovered == 1
        assert sample("orbit_job_recoveries_total", kind="abandoned") == before + 1
        assert _failures("WORKER_LOST", "transient", "false") >= 1

    async def test_a_redelivered_job_is_counted(self) -> None:
        before = sample("orbit_job_recoveries_total", kind="redelivered")
        pipeline = await build_pipeline()
        pipeline.queue.fail = True
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        pipeline.queue.fail = False
        pipeline.clock.advance(timedelta(minutes=11))

        await pipeline.recovery.execute()

        assert sample("orbit_job_recoveries_total", kind="redelivered") == before + 1

    async def test_publish_outcomes_are_counted(self) -> None:
        ok_before = sample("orbit_queue_publish_total", outcome="ok")
        failed_before = sample("orbit_queue_publish_total", outcome="failed")
        pipeline = await build_pipeline()
        await pipeline.upload("a.txt", b"first document " * 10)
        pipeline.queue.fail = True
        await pipeline.upload("b.txt", b"second document " * 10)

        assert sample("orbit_queue_publish_total", outcome="ok") == ok_before + 1
        assert sample("orbit_queue_publish_total", outcome="failed") == failed_before + 1


class TestQueueSnapshot:
    async def test_distinguishes_ready_scheduled_running_and_abandoned(self) -> None:
        pipeline = await build_pipeline()
        reader = ReadQueueSnapshot(pipeline.uow_factory, pipeline.clock)

        # Ready: one due job.
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        snapshot = await reader.execute()
        assert (snapshot.ready, snapshot.scheduled, snapshot.running, snapshot.lease_expired) == (
            1,
            0,
            0,
            0,
        )
        assert snapshot.oldest_ready_age_seconds == 0

        # Waiting: the same job ages while nothing claims it.
        pipeline.clock.advance(timedelta(seconds=90))
        assert (await reader.execute()).oldest_ready_age_seconds == 90

        # Running with a live lease.
        (job_id,) = pipeline.dispatched_job_ids()
        pipeline.embedder.before_return = _die
        with pytest.raises(WorkerKilledError):
            await pipeline.run(job_id)
        snapshot = await reader.execute()
        assert (snapshot.ready, snapshot.running, snapshot.lease_expired) == (0, 1, 0)
        assert snapshot.oldest_ready_age_seconds is None

        # Abandoned: the lease expired and nothing has recovered it yet.
        pipeline.clock.advance(LEASE + timedelta(seconds=1))
        snapshot = await reader.execute()
        assert (snapshot.running, snapshot.lease_expired) == (0, 1)

    async def test_a_retry_backoff_is_scheduled_not_ready(self) -> None:
        pipeline = await build_pipeline(base_delay=timedelta(minutes=4))
        pipeline.embedder.failures.append(AIProviderUnavailableError("503"))
        await pipeline.upload("a.txt", (PROSE * 4).encode())
        (job_id,) = pipeline.dispatched_job_ids()
        result = await pipeline.run(job_id)
        assert result.outcome is JobOutcome.RETRY_SCHEDULED

        snapshot = await ReadQueueSnapshot(pipeline.uow_factory, pipeline.clock).execute()

        assert (snapshot.ready, snapshot.scheduled) == (0, 1), (
            "a job waiting out its backoff is not a backlog"
        )


async def _die() -> None:
    raise WorkerKilledError
