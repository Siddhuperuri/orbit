"""Folder repository.

A folder tree is an adjacency list (`parent_folder_id`), so the operations that
matter -- list everything, add a child, rename, delete -- are each a single
indexed statement or two. What needs care is deletion, which must not race a
document being filed into the folder (see `delete`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import NoReturn

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.domain.access import AccessContext
from orbit.domain.errors import ConflictError, FolderNotEmptyError, NotFoundError, ValidationError
from orbit.domain.models.entities import Folder, FolderListing
from orbit.domain.organization import MAX_FOLDER_DEPTH
from orbit.infrastructure.db.errors import flush_translating_conflicts, translate_integrity_error
from orbit.infrastructure.db.models import Document as DocumentRow
from orbit.infrastructure.db.models import Folder as FolderRow


def folder_to_entity(row: FolderRow) -> Folder:
    return Folder(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        parent_folder_id=row.parent_folder_id,
        depth=row.depth,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by_user_id=row.created_by_user_id,
    )


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class SqlFolderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _live(self, ctx: AccessContext) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
        """This workspace's folders that have not been deleted. Applied to every
        query so none can be written without the tenant and soft-delete filters."""
        return (FolderRow.workspace_id == ctx.workspace_id, FolderRow.deleted_at.is_(None))

    async def list_all(self, ctx: AccessContext) -> Sequence[FolderListing]:
        documents = (
            select(
                DocumentRow.folder_id.label("folder_id"),
                func.count().filter(DocumentRow.archived_at.is_(None)).label("active"),
                func.count().filter(DocumentRow.archived_at.is_not(None)).label("archived"),
            )
            .where(
                DocumentRow.workspace_id == ctx.workspace_id,
                DocumentRow.deleted_at.is_(None),
                DocumentRow.folder_id.is_not(None),
            )
            .group_by(DocumentRow.folder_id)
            .subquery()
        )
        children = (
            select(FolderRow.parent_folder_id.label("parent_id"), func.count().label("n"))
            .where(*self._live(ctx), FolderRow.parent_folder_id.is_not(None))
            .group_by(FolderRow.parent_folder_id)
            .subquery()
        )
        stmt = (
            select(
                FolderRow,
                func.coalesce(documents.c.active, 0),
                func.coalesce(documents.c.archived, 0),
                func.coalesce(children.c.n, 0),
            )
            .outerjoin(documents, documents.c.folder_id == FolderRow.id)
            .outerjoin(children, children.c.parent_id == FolderRow.id)
            .where(*self._live(ctx))
            # Parents before children, so a client can build the tree in one pass.
            .order_by(FolderRow.depth, func.lower(FolderRow.name), FolderRow.id)
        )
        return [
            FolderListing(
                folder=folder_to_entity(row),
                document_count=active,
                archived_document_count=archived,
                child_count=child_count,
            )
            for row, active, archived, child_count in (await self._session.execute(stmt)).all()
        ]

    async def get(self, ctx: AccessContext, folder_id: uuid.UUID) -> Folder | None:
        row = (
            await self._session.execute(
                select(FolderRow).where(*self._live(ctx), FolderRow.id == folder_id)
            )
        ).scalar_one_or_none()
        return folder_to_entity(row) if row else None

    async def count(self, ctx: AccessContext) -> int:
        return (
            await self._session.scalar(
                select(func.count()).select_from(FolderRow).where(*self._live(ctx))
            )
            or 0
        )

    async def create(self, ctx: AccessContext, *, name: str, parent_id: uuid.UUID | None) -> Folder:
        depth = 0
        if parent_id is not None:
            # Shared lock: the parent cannot be deleted between this read and the
            # insert below, which would leave a live child under a dead parent.
            parent = (
                await self._session.execute(
                    select(FolderRow)
                    .where(*self._live(ctx), FolderRow.id == parent_id)
                    .with_for_update(read=True)
                )
            ).scalar_one_or_none()
            if parent is None:
                msg = "That folder no longer exists."
                raise NotFoundError(msg, folder_id=str(parent_id))
            depth = parent.depth + 1
            if depth > MAX_FOLDER_DEPTH:
                msg = f"Folders can be nested at most {MAX_FOLDER_DEPTH} levels deep."
                raise ValidationError(msg)

        row = FolderRow(
            workspace_id=ctx.workspace_id,
            parent_folder_id=parent_id,
            name=name,
            depth=depth,
            created_by_user_id=ctx.user_id,
        )
        self._session.add(row)
        await flush_translating_conflicts(self._session)
        await self._session.refresh(row)
        return folder_to_entity(row)

    async def rename(
        self, ctx: AccessContext, folder_id: uuid.UUID, *, name: str, expected_version: int
    ) -> Folder:
        stmt = (
            update(FolderRow)
            .where(
                *self._live(ctx),
                FolderRow.id == folder_id,
                FolderRow.version == expected_version,
            )
            .values(name=name, version=FolderRow.version + 1)
            .returning(FolderRow)
        )
        try:
            row = (await self._session.execute(stmt)).scalar_one_or_none()
        except IntegrityError as exc:
            raise translate_integrity_error(exc) from exc
        if row is None:
            await self._raise_missing_or_conflicting(ctx, folder_id)
        return folder_to_entity(row)

    async def delete(self, ctx: AccessContext, folder_id: uuid.UUID) -> None:
        # `FOR UPDATE` conflicts with the `FOR SHARE` that filing a document (or
        # creating a subfolder) takes on this row, so the emptiness check below
        # cannot be invalidated by a concurrent write before we commit.
        locked = await self._session.scalar(
            select(FolderRow.id)
            .where(*self._live(ctx), FolderRow.id == folder_id)
            .with_for_update()
        )
        if locked is None:
            msg = "That folder no longer exists."
            raise NotFoundError(msg, folder_id=str(folder_id))

        documents = (
            await self._session.scalar(
                select(func.count())
                .select_from(DocumentRow)
                .where(
                    DocumentRow.workspace_id == ctx.workspace_id,
                    DocumentRow.folder_id == folder_id,
                    DocumentRow.deleted_at.is_(None),
                )
            )
            or 0
        )
        children = (
            await self._session.scalar(
                select(func.count())
                .select_from(FolderRow)
                .where(*self._live(ctx), FolderRow.parent_folder_id == folder_id)
            )
            or 0
        )
        if documents or children:
            contents = [
                _plural(count, noun)
                for count, noun in ((documents, "document"), (children, "subfolder"))
                if count
            ]
            msg = f"This folder isn't empty: it still holds {' and '.join(contents)}."
            raise FolderNotEmptyError(
                msg, folder_id=str(folder_id), documents=documents, folders=children
            )

        await self._session.execute(
            update(FolderRow)
            .where(FolderRow.id == folder_id)
            .values(deleted_at=func.now(), version=FolderRow.version + 1)
        )

    async def _raise_missing_or_conflicting(
        self, ctx: AccessContext, folder_id: uuid.UUID
    ) -> NoReturn:
        exists = await self._session.scalar(
            select(func.count())
            .select_from(FolderRow)
            .where(*self._live(ctx), FolderRow.id == folder_id)
        )
        if exists:
            msg = "The folder was changed by someone else. Reload and try again."
            raise ConflictError(msg, folder_id=str(folder_id))
        msg = "That folder no longer exists."
        raise NotFoundError(msg, folder_id=str(folder_id))
