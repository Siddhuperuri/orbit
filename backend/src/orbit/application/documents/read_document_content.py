"""The readable text of a document's current version.

What is shown is what was *indexed*: the passages the chunker produced from the
normalised text, in order. That is deliberately not a rendering of the original
file. A PDF's layout, images, and typography are not retained, and pretending
otherwise would mean a viewer that disagrees with what search and answers can
see. Showing the indexed text is the honest view -- it is exactly what a
citation points into.

Only the current version has passages (superseded versions lose theirs), so
this reads the current version and no other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from orbit.domain.access import AccessContext, Permission
from orbit.domain.documents import Passage, without_overlap
from orbit.domain.errors import NotFoundError
from orbit.domain.models.entities import DocumentVersion
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

MAX_PASSAGE_PAGE = 100


@dataclass(frozen=True, slots=True)
class DocumentContent:
    version: DocumentVersion
    #: Overlap between neighbouring passages already removed.
    items: tuple[Passage, ...]
    next_after: int | None


class ReadDocumentContent:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        after: int | None = None,
        limit: int = MAX_PASSAGE_PAGE,
    ) -> DocumentContent:
        ctx.require(Permission.DOCUMENT_READ)
        async with self._uow_factory() as uow:
            document = await uow.documents.get(ctx, document_id)
            if document is None or document.current_version is None:
                msg = "Document not found."
                raise NotFoundError(msg, document_id=str(document_id))
            version = document.current_version
            page = await uow.documents.list_passages(
                ctx, version.id, after=after, limit=min(max(limit, 1), MAX_PASSAGE_PAGE)
            )
        return DocumentContent(
            version=version,
            items=without_overlap(page.items, preceding_end=page.preceding_end),
            next_after=page.next_after,
        )
