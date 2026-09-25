"""Attach a tag to a document, or remove it.

Both are idempotent, and both are *set* operations rather than "replace the tag
list": two people tagging one document at once must both succeed, which a
replace cannot offer (the second write would erase the first tag).
"""

from __future__ import annotations

import uuid

from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import NotFoundError, ValidationError
from orbit.domain.models.entities import Document
from orbit.domain.organization import MAX_TAGS_PER_DOCUMENT
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


def _not_found(document_id: uuid.UUID) -> NotFoundError:
    return NotFoundError("Document not found.", document_id=str(document_id))


class AddDocumentTag:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, ctx: AccessContext, document_id: uuid.UUID, tag_id: uuid.UUID
    ) -> Document:
        ctx.require(Permission.TAG_WRITE)
        async with self._uow_factory() as uow:
            added = await uow.tags.attach(ctx, document_id, tag_id)
            # Checked after the insert, inside the transaction: not committing on
            # failure rolls the insert back, and "was it already there?" needs no
            # separate read (an idempotent re-add must not trip the limit).
            if (
                added
                and await uow.tags.count_for_document(ctx, document_id) > MAX_TAGS_PER_DOCUMENT
            ):
                msg = f"A document can carry at most {MAX_TAGS_PER_DOCUMENT} tags."
                raise ValidationError(msg)
            await uow.commit()
            document = await uow.documents.get(ctx, document_id)
        if document is None:
            raise _not_found(document_id)
        return document


class RemoveDocumentTag:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, ctx: AccessContext, document_id: uuid.UUID, tag_id: uuid.UUID
    ) -> Document:
        ctx.require(Permission.TAG_WRITE)
        async with self._uow_factory() as uow:
            await uow.tags.detach(ctx, document_id, tag_id)
            await uow.commit()
            document = await uow.documents.get(ctx, document_id)
        if document is None:
            raise _not_found(document_id)
        return document
