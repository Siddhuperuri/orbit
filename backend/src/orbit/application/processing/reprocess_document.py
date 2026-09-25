"""Explicitly retry a document whose processing failed.

The controlled way out of `FAILED`. Automatic retries stop at a bounded number
of attempts (and never apply to permanent failures), so a document that failed
because a provider was down for an hour needs a deliberate second chance once
it is back. This starts a fresh processing run with a fresh retry budget.

Only `FAILED` versions qualify. Reprocessing a `READY` version would make a
searchable document unsearchable while it re-ran; reprocessing a `PENDING` or
`PROCESSING` one would race the job already doing it -- which the database's
one-active-job-per-version index would reject anyway.
"""

from __future__ import annotations

import uuid

from orbit.application.processing.dispatch import enqueue_after_commit
from orbit.core.logging import current_request_id, get_logger
from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.models.entities import Document
from orbit.domain.ports.processing import JobDispatch, ProcessingJobQueue
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

logger = get_logger(__name__)


class ReprocessDocument:
    def __init__(self, uow_factory: UnitOfWorkFactory, queue: ProcessingJobQueue) -> None:
        self._uow_factory = uow_factory
        self._queue = queue

    async def execute(self, ctx: AccessContext, document_id: uuid.UUID) -> Document:
        ctx.require(Permission.DOCUMENT_UPDATE)
        request_id = current_request_id()
        async with self._uow_factory() as uow:
            document = await uow.documents.get(ctx, document_id)
            if document is None or document.current_version is None:
                msg = "Document not found."
                raise NotFoundError(msg, document_id=str(document_id))

            job = await uow.processing.restart_failed_version(
                ctx, document.current_version.id, request_id=request_id
            )
            if job is None:
                msg = "Only a document whose processing failed can be reprocessed."
                raise ConflictError(
                    msg,
                    document_id=str(document_id),
                    status=document.current_version.status.value,
                )
            refreshed = await uow.documents.get(ctx, document_id)
            await uow.commit()

        await enqueue_after_commit(self._queue, JobDispatch(job_id=job.id, request_id=request_id))
        logger.info(
            "document.reprocess_requested", document_id=str(document_id), job_id=str(job.id)
        )
        assert refreshed is not None  # noqa: S101 -- read in the transaction that just committed
        return refreshed
