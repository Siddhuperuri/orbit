"""The worker runtime: the synchronous shell Celery calls, over the async pipeline.

Plain (non-async) tests on purpose: the runtime owns its event loop via
`asyncio.run`, exactly as it does inside a Celery child process.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest
import structlog
from celery.exceptions import SoftTimeLimitExceeded

from orbit.application.processing.lifecycle import JobLifecycle, JobOutcome
from orbit.application.processing.process_document import ProcessDocumentJob
from orbit.application.processing.recover_stalled_jobs import RecoverStalledJobs
from orbit.composition.worker_runtime import InvalidTaskPayloadError, WorkerRuntime
from orbit.domain.models.entities import ProcessingStatus
from orbit.domain.processing.failures import ProcessingFailure
from orbit.domain.processing.jobs import JobStatus
from tests.unit.processing.harness import Pipeline, build_pipeline

PROSE = "Leases make worker restarts safe. Fencing stops a zombie overwriting its successor. " * 10


@dataclass
class Services:
    pipeline: Pipeline
    closed: list[bool] = field(default_factory=list)

    @property
    def process_document(self) -> ProcessDocumentJob:
        return self.pipeline.worker

    @property
    def recover_stalled_jobs(self) -> RecoverStalledJobs:
        return self.pipeline.recovery

    @property
    def lifecycle(self) -> JobLifecycle:
        return self.pipeline.lifecycle

    async def aclose(self) -> None:
        self.closed.append(True)


def _setup(
    filename: str = "a.txt", payload: bytes = PROSE.encode()
) -> tuple[Pipeline, Services, WorkerRuntime, uuid.UUID]:
    pipeline = asyncio.run(build_pipeline())
    asyncio.run(pipeline.upload(filename, payload))
    (job_id,) = pipeline.dispatched_job_ids()
    services = Services(pipeline)
    return pipeline, services, WorkerRuntime(lambda: services), job_id


def test_processes_a_job_and_closes_the_per_task_container() -> None:
    pipeline, services, runtime, job_id = _setup()
    result = runtime.process(str(job_id), request_id="01JB2X8N4K7QF3TVWZ9M5PDCRA")
    assert result.outcome is JobOutcome.SUCCEEDED
    assert services.closed == [True]
    (version,) = pipeline.uow_factory.state.versions.values()
    assert version.status is ProcessingStatus.READY


def test_each_execution_gets_a_distinct_worker_identity() -> None:
    assert WorkerRuntime.new_worker_id() != WorkerRuntime.new_worker_id()


def test_correlation_context_does_not_leak_between_tasks() -> None:
    _, _, runtime, job_id = _setup()
    runtime.process(str(job_id), request_id="01JB2X8N4K7QF3TVWZ9M5PDCRA")
    assert structlog.contextvars.get_contextvars() == {}


@pytest.mark.parametrize("payload", ["not-a-uuid", "", "1234"])
def test_an_unprocessable_payload_is_rejected_not_retried(payload: str) -> None:
    runtime = WorkerRuntime(lambda: pytest.fail("no container for a bad payload"))
    with pytest.raises(InvalidTaskPayloadError):
        runtime.process(payload, request_id=None)


def test_a_soft_time_limit_escaping_the_pipeline_is_recorded_as_a_timeout() -> None:
    """The limit can fire while the loop waits on I/O, in which case it unwinds
    `asyncio.run` instead of being caught by the pipeline."""
    pipeline, services, runtime, job_id = _setup()

    async def time_limit_fires() -> None:
        raise SoftTimeLimitExceeded

    # Raised from the embed hook, it would be caught and classified inside the
    # pipeline. To model the escape, the limit fires in the embedder *and* the
    # pipeline's handler is bypassed by making classification re-raise it.
    original_classify = pipeline.worker._classifier.classify

    def reraise(exc: BaseException):  # type: ignore[no-untyped-def]  # noqa: ANN202
        if isinstance(exc, SoftTimeLimitExceeded):
            raise exc
        return original_classify(exc)

    pipeline.worker._classifier.classify = reraise  # type: ignore[method-assign]
    pipeline.embedder.before_return = time_limit_fires

    result = runtime.process(str(job_id), request_id=None)

    assert (result.outcome, result.reason) == (JobOutcome.FAILED, "DOCUMENT_PROCESSING_TIMEOUT")
    (version,) = pipeline.uow_factory.state.versions.values()
    assert version.status is ProcessingStatus.FAILED
    assert "took too long" in (version.failure_reason or "")
    (job,) = pipeline.uow_factory.state.jobs.values()
    assert job.status is JobStatus.FAILED
    assert services.closed == [True, True], "each loop gets its own container"


def test_a_soft_time_limit_inside_the_pipeline_is_classified_the_same_way() -> None:
    pipeline, _, runtime, job_id = _setup()

    async def time_limit_fires() -> None:
        raise SoftTimeLimitExceeded

    pipeline.embedder.before_return = time_limit_fires
    result = runtime.process(str(job_id), request_id=None)
    assert result.reason == "DOCUMENT_PROCESSING_TIMEOUT"


def test_a_timeout_recorded_after_recovery_took_the_job_writes_nothing() -> None:
    pipeline, _, _, job_id = _setup()
    result = asyncio.run(
        pipeline.lifecycle.record_failure_by_id(
            job_id,
            worker_id="never-claimed",
            failure=ProcessingFailure.defect(detail="x"),
        )
    )
    assert result.outcome is JobOutcome.LEASE_LOST
    (job,) = pipeline.uow_factory.state.jobs.values()
    assert job.status is JobStatus.QUEUED


def test_recover_runs_a_sweep() -> None:
    _, services, runtime, _ = _setup()
    report = runtime.recover()
    assert report.abandoned_found == 0
    assert services.closed == [True]
