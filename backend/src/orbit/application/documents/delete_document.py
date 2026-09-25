"""Soft-delete a document.

Chunks, embeddings, and the stored object are reclaimed asynchronously
(docs/architecture/data-flow.md) -- that reclamation is a worker job introduced
in M4 and is out of scope here; this use case only satisfies the user-visible
half of the contract, which is that the document disappears immediately.
"""

from __future__ import annotations

import uuid

from orbit.domain.access import AccessContext, Permission
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class DeleteDocument:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, document_id: uuid.UUID) -> None:
        ctx.require(Permission.DOCUMENT_DELETE)
        async with self._uow_factory() as uow:
            await uow.documents.soft_delete(ctx, document_id)
            await uow.commit()
