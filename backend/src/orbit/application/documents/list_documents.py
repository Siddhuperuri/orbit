"""List documents in a workspace, keyset-paginated."""

from __future__ import annotations

from orbit.domain.access import AccessContext, Permission
from orbit.domain.documents import DocumentListQuery
from orbit.domain.models.pagination import Page
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class ListDocuments:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        ctx: AccessContext,
        *,
        limit: int,
        cursor: str | None = None,
        query: DocumentListQuery | None = None,
    ) -> Page:
        ctx.require(Permission.DOCUMENT_READ)
        async with self._uow_factory() as uow:
            return await uow.documents.list_page(ctx, limit=limit, cursor=cursor, query=query)
