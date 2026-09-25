"""Publishing processing jobs to Celery.

Tasks are sent **by name**. The API process never imports the task function --
which lives in the worker's composition root and would drag the whole pipeline
(parsers, PDF library, embedding client) into the web process for the sake of
one `apply_async` call.
"""

from __future__ import annotations

from functools import partial

import anyio
import kombu.exceptions
import redis.exceptions
from celery import Celery

from orbit.domain.errors import QueueUnavailableError
from orbit.domain.ports.processing import JobDispatch
from orbit.infrastructure.queue.task_names import PROCESS_DOCUMENT_TASK

#: Publishing happens on the request path (after the upload commits), so a
#: broker outage must cost a bounded delay, not a hung request: two quick
#: retries, then give up and let recovery re-publish later.
_PUBLISH_RETRY_POLICY = {
    "max_retries": 2,
    "interval_start": 0.0,
    "interval_step": 0.5,
    "interval_max": 1.0,
}


class CeleryProcessingJobQueue:
    def __init__(self, app: Celery) -> None:
        self._app = app

    async def enqueue(self, dispatch: JobDispatch) -> None:
        countdown = max(dispatch.delay.total_seconds(), 0.0)
        send = partial(
            self._app.send_task,
            PROCESS_DOCUMENT_TASK,
            kwargs={
                "job_id": str(dispatch.job_id),
                "request_id": dispatch.request_id,
            },
            countdown=countdown or None,
            retry=True,
            retry_policy=_PUBLISH_RETRY_POLICY,
            ignore_result=True,
        )
        try:
            # kombu's publish is blocking network I/O.
            await anyio.to_thread.run_sync(send)
        except (
            kombu.exceptions.OperationalError,
            redis.exceptions.RedisError,
            OSError,
        ) as exc:
            msg = "The job queue is unavailable."
            raise QueueUnavailableError(msg, job_id=str(dispatch.job_id)) from exc
