"""Archive or restore a document.

Archiving takes a document out of the working set and out of every answer
without touching it: the row, its versions, its passages and its stored object
all stay, so restoring is instant. That is the difference from deleting, which
is why the two are separate actions with separate permissions.
"""

from __future__ import annotations

import uuid

from orbit.domain.access import AccessContext, Permission
from orbit.domain.models.entities import Document
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class ArchiveDocument:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, ctx: AccessContext, document_id: uuid.UUID, *, archived: bool
    ) -> Document:
        # Reversible, so it needs only the update permission; deleting is the
        # irreversible one and keeps its own.
        ctx.require(Permission.DOCUMENT_UPDATE)
        async with self._uow_factory() as uow:
            document = await uow.documents.set_archived(ctx, document_id, archived=archived)
            await uow.commit()
        return document
