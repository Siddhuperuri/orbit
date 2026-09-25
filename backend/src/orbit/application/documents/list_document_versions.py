"""A document's revision history, newest first."""

from __future__ import annotations

import uuid

from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import NotFoundError
from orbit.domain.models.entities import VersionPage
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

MAX_VERSION_PAGE = 50


class ListDocumentVersions:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        before: int | None = None,
        limit: int = MAX_VERSION_PAGE,
    ) -> VersionPage:
        ctx.require(Permission.DOCUMENT_READ)
        async with self._uow_factory() as uow:
            # `page_versions` returns an empty page for a document that is not
            # there, which would read as "no history". A missing document has to
            # be a 404, so it is asked first.
            if await uow.documents.get(ctx, document_id) is None:
                msg = "Document not found."
                raise NotFoundError(msg, document_id=str(document_id))
            return await uow.documents.page_versions(
                ctx, document_id, before=before, limit=min(max(limit, 1), MAX_VERSION_PAGE)
            )
