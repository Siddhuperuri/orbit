"""Workspace repository.

Note what is missing: there is no `get(workspace_id)`. The only way to read a
workspace is `get(ctx)`, where the context already carries a resolved
membership. A method taking a bare id would be usable without proving access,
and would eventually be used that way (ADR-0004).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import NoReturn

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.core.ids import new_uuid7
from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.models.entities import Workspace
from orbit.infrastructure.db.errors import flush_translating_conflicts
from orbit.infrastructure.db.models import Workspace as WorkspaceRow
from orbit.infrastructure.db.models import WorkspaceMember


def to_entity(row: WorkspaceRow) -> Workspace:
    return Workspace(
        id=row.id,
        name=row.name,
        slug=row.slug,
        created_at=row.created_at,
        version=row.version,
        created_by_user_id=row.created_by_user_id,
        deleted_at=row.deleted_at,
    )


class SqlWorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, ctx: AccessContext) -> Workspace | None:
        stmt = select(WorkspaceRow).where(
            WorkspaceRow.id == ctx.workspace_id, WorkspaceRow.deleted_at.is_(None)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return to_entity(row) if row else None

    async def create(self, *, name: str, slug: str, created_by_user_id: uuid.UUID) -> Workspace:
        """Create a workspace.

        Takes no context because creation precedes membership. The caller is
        responsible for adding the owner membership in the same transaction --
        a workspace with no owner must never be committed, and the unit of work
        is what makes that atomic.
        """
        row = WorkspaceRow(
            id=new_uuid7(),
            name=name.strip(),
            slug=slug.strip().lower(),
            created_by_user_id=created_by_user_id,
        )
        self._session.add(row)
        await flush_translating_conflicts(self._session)
        await self._session.refresh(row)
        return to_entity(row)

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[tuple[Workspace, Role]]:
        """Workspaces this user belongs to, with their role in each.

        A join rather than two queries: fetching workspaces and then roles would
        be an N+1 in the sidebar of every page.
        """
        stmt = (
            select(WorkspaceRow, WorkspaceMember.role)
            .join(
                WorkspaceMember,
                (WorkspaceMember.workspace_id == WorkspaceRow.id)
                & (WorkspaceMember.user_id == user_id),
            )
            .where(WorkspaceRow.deleted_at.is_(None))
            .order_by(WorkspaceRow.name)
        )
        result = await self._session.execute(stmt)
        return [(to_entity(row), role) for row, role in result.all()]

    async def rename(self, ctx: AccessContext, name: str, *, expected_version: int) -> Workspace:
        """Rename under optimistic concurrency control.

        The `version` predicate is what turns two simultaneous renames into a
        visible conflict for the loser instead of a silent overwrite of the
        winner. `UPDATE ... RETURNING` does it in one round trip, and a zero-row
        result is unambiguous: either the row moved or it is gone.
        """
        stmt = (
            update(WorkspaceRow)
            .where(
                WorkspaceRow.id == ctx.workspace_id,
                WorkspaceRow.deleted_at.is_(None),
                WorkspaceRow.version == expected_version,
            )
            .values(name=name.strip(), version=WorkspaceRow.version + 1)
            .returning(WorkspaceRow)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            await self._raise_missing_or_conflicting(ctx.workspace_id)
        return to_entity(row)

    async def soft_delete(self, ctx: AccessContext) -> None:
        """Make the workspace invisible without destroying its contents.

        Its documents, folders, and conversations are left in place: the
        expectation "it is gone" is satisfied by this row's `deleted_at`, and a
        purge -- which cascades to everything -- is a separate, deliberate,
        retention-governed operation.
        """
        stmt = (
            update(WorkspaceRow)
            .where(WorkspaceRow.id == ctx.workspace_id, WorkspaceRow.deleted_at.is_(None))
            .values(deleted_at=func.now(), version=WorkspaceRow.version + 1)
        )
        await self._session.execute(stmt)

    async def _raise_missing_or_conflicting(self, workspace_id: uuid.UUID) -> NoReturn:
        """Distinguish "gone" from "changed underneath you".

        Both produce zero updated rows, but they need different responses: one is
        a 404, the other a 409 the client can resolve by refetching.
        """
        exists = await self._session.scalar(
            select(func.count())
            .select_from(WorkspaceRow)
            .where(WorkspaceRow.id == workspace_id, WorkspaceRow.deleted_at.is_(None))
        )
        if exists:
            msg = "The workspace was modified by someone else. Reload and try again."
            raise ConflictError(msg, workspace_id=str(workspace_id))
        msg = "Workspace not found."
        raise NotFoundError(msg, workspace_id=str(workspace_id))
