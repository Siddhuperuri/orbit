"""The bridge between Celery's synchronous tasks and the async pipeline.

Kept separate from `worker.py` (which is import-time wiring for the Celery CLI)
so that it can be constructed and exercised by tests with a fake container,
no broker, and no signal handlers.

What happens to an exception depends on where it came from:

* **Inside the pipeline**, every failure is classified and recorded by the
  pipeline itself; nothing escapes.
* **Before or while recording** (the database is unreachable, so the claim or
  the outcome cannot be written), the message is redelivered after a delay via
  Celery's own retry. Nothing about the job has changed, so redelivery is
  exactly right.
* **The soft time limit** can fire while the event loop is waiting on I/O, in
  which case it escapes `asyncio.run` rather than being raised inside the
  pipeline. It is caught here and recorded as a timeout on a fresh loop.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from celery.exceptions import SoftTimeLimitExceeded

from orbit.application.processing.lifecycle import JobLifecycle, JobRunResult
from orbit.application.processing.process_document import ProcessDocumentJob
from orbit.application.processing.recover_stalled_jobs import RecoverStalledJobs, RecoveryReport
from orbit.core.ids import new_ulid
from orbit.core.logging import bind_correlation, clear_correlation, get_logger, operation
from orbit.core.metrics import JOB_EXECUTIONS
from orbit.domain.errors import DocumentProcessingTimeoutError
from orbit.domain.processing.failures import ProcessingFailure

logger = get_logger(__name__)

_T = TypeVar("_T")

TIMEOUT_MESSAGE = "This document took too long to process. It may be too large or too complex."


class WorkerServices(Protocol):
    @property
    def process_document(self) -> ProcessDocumentJob: ...
    @property
    def recover_stalled_jobs(self) -> RecoverStalledJobs: ...
    @property
    def lifecycle(self) -> JobLifecycle: ...
    async def aclose(self) -> None: ...


ContainerFactory = Callable[[], WorkerServices]


class InvalidTaskPayloadError(ValueError):
    """A message that can never be processed. Logged and dropped -- redelivering
    it would only fail the same way forever."""


class WorkerRuntime:
    def __init__(self, container_factory: ContainerFactory) -> None:
        self._container_factory = container_factory

    @staticmethod
    def new_worker_id() -> str:
        """Unique per *execution*, not per process: a redelivered message
        handled by the same process must not inherit the fence of the attempt
        that came before it."""
        return f"{socket.gethostname()}/{os.getpid()}/{new_ulid()}"

    def process(self, raw_job_id: str, *, request_id: str | None) -> JobRunResult:
        clear_correlation()
        try:
            job_id = _parse_job_id(raw_job_id)
            # `request_id` is the *upload's*: every attempt, and every retry,
            # logs under the id of the request that created the work.
            bind_correlation(
                job_id=str(job_id), request_id=request_id, operation="document.process"
            )
            worker_id = self.new_worker_id()
            try:
                result = self._run(
                    lambda services: services.process_document.execute(job_id, worker_id=worker_id)
                )
            except SoftTimeLimitExceeded:
                logger.warning("task.soft_time_limit_exceeded")
                result = self._run(lambda services: _record_timeout(services, job_id, worker_id))
            JOB_EXECUTIONS.labels(outcome=result.outcome.value).inc()
            logger.info(
                "task.finished",
                outcome=result.outcome.value,
                reason=result.reason,
                chunk_count=result.chunk_count,
                next_job_id=str(result.next_job_id) if result.next_job_id else None,
            )
            return result
        finally:
            clear_correlation()

    def recover(self) -> RecoveryReport:
        clear_correlation()
        try:
            with operation("job.recover"):
                return self._run(lambda services: services.recover_stalled_jobs.execute())
        finally:
            clear_correlation()

    def _run(self, work: Callable[[WorkerServices], Awaitable[_T]]) -> _T:
        async def main() -> _T:
            services = self._container_factory()
            try:
                return await work(services)
            finally:
                await services.aclose()

        return asyncio.run(main())


async def _record_timeout(
    services: WorkerServices, job_id: uuid.UUID, worker_id: str
) -> JobRunResult:
    """Record the soft time limit against the attempt this execution claimed.

    Fenced like every other outcome: if this execution never got as far as
    claiming, or recovery has already taken the job, nothing is written.
    """
    failure = ProcessingFailure.permanent(DocumentProcessingTimeoutError(TIMEOUT_MESSAGE))
    return await services.lifecycle.record_failure_by_id(
        job_id, worker_id=worker_id, failure=failure
    )


def _parse_job_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        msg = "Task payload does not carry a valid job id."
        raise InvalidTaskPayloadError(msg) from exc
