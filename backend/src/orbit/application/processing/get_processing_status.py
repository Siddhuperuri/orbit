"""Where a document is in the pipeline, and what happened on each attempt."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import NotFoundError
from orbit.domain.models.entities import Document, DocumentVersion
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory
from orbit.domain.processing.jobs import ProcessingJob


@dataclass(frozen=True, slots=True)
class ProcessingStatusView:
    document: Document
    version: DocumentVersion
    #: Newest attempt first.
    jobs: Sequence[ProcessingJob]


class GetProcessingStatus:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, document_id: uuid.UUID) -> ProcessingStatusView:
        ctx.require(Permission.DOCUMENT_READ)
        async with self._uow_factory() as uow:
            document = await uow.documents.get(ctx, document_id)
            if document is None or document.current_version is None:
                msg = "Document not found."
                raise NotFoundError(msg, document_id=str(document_id))
            jobs = await uow.processing.list_jobs(ctx, document.current_version.id)
        return ProcessingStatusView(document=document, version=document.current_version, jobs=jobs)
