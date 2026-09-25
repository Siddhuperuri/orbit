"""Tag repository.

Attaching and detaching are set operations -- add this, remove that -- rather
than "replace the document's tags with this list". Two people tagging one
document at the same moment therefore both succeed, where a replace would have
let the second silently erase the first's tag. Nothing here bumps the
document's own `version` for the same reason: tagging commutes, so it must not
make an unrelated rename fail as a conflict.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import NoReturn

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.domain.access import AccessContext
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.models.entities import Tag, TagListing
from orbit.domain.organization import DEFAULT_TAG_COLOR
from orbit.infrastructure.db.errors import flush_translating_conflicts, translate_integrity_error
from orbit.infrastructure.db.models import Document as DocumentRow
from orbit.infrastructure.db.models import DocumentTag as DocumentTagRow
from orbit.infrastructure.db.models import Tag as TagRow


def tag_to_entity(row: TagRow) -> Tag:
    return Tag(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        color=row.color or DEFAULT_TAG_COLOR,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlTagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self, ctx: AccessContext) -> Sequence[TagListing]:
        # Counts only documents that still exist; a soft-deleted document's join
        # rows are left behind, and counting them would make a tag look used.
        counts = (
            select(DocumentTagRow.tag_id.label("tag_id"), func.count().label("n"))
            .join(
                DocumentRow,
                and_(
                    DocumentRow.workspace_id == DocumentTagRow.workspace_id,
                    DocumentRow.id == DocumentTagRow.document_id,
                ),
            )
            .where(
                DocumentTagRow.workspace_id == ctx.workspace_id,
                DocumentRow.deleted_at.is_(None),
            )
            .group_by(DocumentTagRow.tag_id)
            .subquery()
        )
        stmt = (
            select(TagRow, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.tag_id == TagRow.id)
            .where(TagRow.workspace_id == ctx.workspace_id)
            .order_by(func.lower(TagRow.name), TagRow.id)
        )
        return [
            TagListing(tag=tag_to_entity(row), document_count=count)
            for row, count in (await self._session.execute(stmt)).all()
        ]

    async def get(self, ctx: AccessContext, tag_id: uuid.UUID) -> Tag | None:
        row = (
            await self._session.execute(
                select(TagRow).where(TagRow.workspace_id == ctx.workspace_id, TagRow.id == tag_id)
            )
        ).scalar_one_or_none()
        return tag_to_entity(row) if row else None

    async def count(self, ctx: AccessContext) -> int:
        return (
            await self._session.scalar(
                select(func.count())
                .select_from(TagRow)
                .where(TagRow.workspace_id == ctx.workspace_id)
            )
            or 0
        )

    async def create(self, ctx: AccessContext, *, name: str, color: str) -> Tag:
        row = TagRow(workspace_id=ctx.workspace_id, name=name, color=color)
        self._session.add(row)
        try:
            await flush_translating_conflicts(self._session)
        except IntegrityError as exc:
            raise translate_integrity_error(exc) from exc
        await self._session.refresh(row)
        return tag_to_entity(row)

    async def update(
        self,
        ctx: AccessContext,
        tag_id: uuid.UUID,
        *,
        name: str | None,
        color: str | None,
        expected_version: int,
    ) -> Tag:
        values: dict[str, object] = {"version": TagRow.version + 1}
        if name is not None:
            values["name"] = name
        if color is not None:
            values["color"] = color

        stmt = (
            update(TagRow)
            .where(
                TagRow.workspace_id == ctx.workspace_id,
                TagRow.id == tag_id,
                TagRow.version == expected_version,
            )
            .values(**values)
            .returning(TagRow)
        )
        try:
            row = (await self._session.execute(stmt)).scalar_one_or_none()
        except IntegrityError as exc:
            raise translate_integrity_error(exc) from exc
        if row is None:
            await self._raise_missing_or_conflicting(ctx, tag_id)
        return tag_to_entity(row)

    async def delete(self, ctx: AccessContext, tag_id: uuid.UUID) -> None:
        # The join rows go with it: `fk_document_tags_tag_within_workspace` is
        # `ON DELETE CASCADE`.
        deleted = await self._session.scalar(
            delete(TagRow)
            .where(TagRow.workspace_id == ctx.workspace_id, TagRow.id == tag_id)
            .returning(TagRow.id)
        )
        if deleted is None:
            msg = "That tag no longer exists."
            raise NotFoundError(msg, tag_id=str(tag_id))

    async def _require_target(
        self, ctx: AccessContext, document_id: uuid.UUID, tag_id: uuid.UUID
    ) -> None:
        """Both ends must exist in this workspace, or the caller learns nothing
        about which one is missing -- and nothing about other workspaces at all."""
        document = await self._session.scalar(
            select(DocumentRow.id).where(
                DocumentRow.id == document_id,
                DocumentRow.workspace_id == ctx.workspace_id,
                DocumentRow.deleted_at.is_(None),
            )
        )
        if document is None:
            msg = "Document not found."
            raise NotFoundError(msg, document_id=str(document_id))
        tag = await self._session.scalar(
            select(TagRow.id).where(TagRow.workspace_id == ctx.workspace_id, TagRow.id == tag_id)
        )
        if tag is None:
            msg = "That tag no longer exists."
            raise NotFoundError(msg, tag_id=str(tag_id))

    async def attach(self, ctx: AccessContext, document_id: uuid.UUID, tag_id: uuid.UUID) -> bool:
        await self._require_target(ctx, document_id, tag_id)
        inserted = await self._session.scalar(
            insert(DocumentTagRow)
            .values(workspace_id=ctx.workspace_id, document_id=document_id, tag_id=tag_id)
            .on_conflict_do_nothing()
            .returning(DocumentTagRow.tag_id)
        )
        return inserted is not None

    async def detach(self, ctx: AccessContext, document_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        await self._require_target(ctx, document_id, tag_id)
        await self._session.execute(
            delete(DocumentTagRow).where(
                DocumentTagRow.workspace_id == ctx.workspace_id,
                DocumentTagRow.document_id == document_id,
                DocumentTagRow.tag_id == tag_id,
            )
        )

    async def count_for_document(self, ctx: AccessContext, document_id: uuid.UUID) -> int:
        return (
            await self._session.scalar(
                select(func.count())
                .select_from(DocumentTagRow)
                .where(
                    DocumentTagRow.workspace_id == ctx.workspace_id,
                    DocumentTagRow.document_id == document_id,
                )
            )
            or 0
        )

    async def _raise_missing_or_conflicting(
        self, ctx: AccessContext, tag_id: uuid.UUID
    ) -> NoReturn:
        exists = await self._session.scalar(
            select(func.count())
            .select_from(TagRow)
            .where(TagRow.workspace_id == ctx.workspace_id, TagRow.id == tag_id)
        )
        if exists:
            msg = "The tag was changed by someone else. Reload and try again."
            raise ConflictError(msg, tag_id=str(tag_id))
        msg = "That tag no longer exists."
        raise NotFoundError(msg, tag_id=str(tag_id))
