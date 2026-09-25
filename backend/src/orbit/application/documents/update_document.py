"""Rename and/or refile a document, under optimistic concurrency control."""

from __future__ import annotations

import uuid

from orbit.domain.access import AccessContext, Permission
from orbit.domain.documents import DocumentEdit
from orbit.domain.models.entities import Document
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class UpdateDocument:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        edit: DocumentEdit,
        expected_version: int,
    ) -> Document:
        # Renaming and refiling both change how a document is *described*, never
        # its bytes -- the same permission as replacing them
        # (`AddDocumentVersion`), the other update.
        ctx.require(Permission.DOCUMENT_UPDATE)
        async with self._uow_factory() as uow:
            updated = await uow.documents.update(
                ctx, document_id, edit=edit, expected_version=expected_version
            )
            await uow.commit()
        return updated
