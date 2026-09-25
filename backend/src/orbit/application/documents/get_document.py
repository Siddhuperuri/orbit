"""Fetch one document by ID."""

from __future__ import annotations

import uuid

from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import NotFoundError
from orbit.domain.models.entities import Document
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class GetDocument:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, document_id: uuid.UUID) -> Document:
        ctx.require(Permission.DOCUMENT_READ)
        async with self._uow_factory() as uow:
            document = await uow.documents.get(ctx, document_id)
        if document is None:
            msg = "Document not found."
            raise NotFoundError(msg, document_id=str(document_id))
        return document
