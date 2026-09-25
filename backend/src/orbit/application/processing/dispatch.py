"""Publishing a job after its row is committed."""

from __future__ import annotations

from orbit.core.logging import get_logger
from orbit.core.metrics import QUEUE_PUBLISH
from orbit.domain.ports.processing import JobDispatch, ProcessingJobQueue

logger = get_logger(__name__)


async def enqueue_after_commit(queue: ProcessingJobQueue, dispatch: JobDispatch) -> bool:
    """Publish a job whose row is already committed. Never raises.

    The action that created the job succeeded the moment its transaction
    committed: the job exists and will run. A failed publish only delays it
    until recovery re-publishes it after the grace period, so surfacing the
    failure as an error would report something as failed that did not
    (docs/architecture/data-flow.md). Returns whether the publish succeeded,
    for the caller's log line.
    """
    try:
        await queue.enqueue(dispatch)
    except Exception:
        QUEUE_PUBLISH.labels(outcome="failed").inc()
        logger.exception(
            "processing.enqueue_failed",
            job_id=str(dispatch.job_id),
            delay_seconds=dispatch.delay.total_seconds(),
        )
        return False
    QUEUE_PUBLISH.labels(outcome="ok").inc()
    return True
