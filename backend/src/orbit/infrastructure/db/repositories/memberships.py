"""Membership repository.

Memberships are **hard-deleted**, never soft-deleted. Revoking access has to
actually revoke it; a soft-deleted membership row is one forgotten
`WHERE deleted_at IS NULL` away from being a privilege-escalation bug, and that
is not a risk worth taking for the ability to undo a removal.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.models.entities import Membership
from orbit.infrastructure.db.errors import flush_translating_conflicts
from orbit.infrastructure.db.models import WorkspaceMember


def to_entity(row: WorkspaceMember) -> Membership:
    return Membership(
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        role=row.role,
        created_at=row.created_at,
        invited_by_user_id=row.invited_by_user_id,
    )


class SqlMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
        """Resolve a user's role in a workspace.

        Takes raw identifiers because this is the call that *produces* an
        `AccessContext` -- it necessarily runs before one exists. It is the only
        membership method with that shape, and it is read-only.
        """
        row = await self._session.get(WorkspaceMember, (workspace_id, user_id))
        return to_entity(row) if row else None

    async def list_members(self, ctx: AccessContext) -> Sequence[Membership]:
        stmt = (
            select(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == ctx.workspace_id)
            .order_by(WorkspaceMember.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [to_entity(row) for row in rows]

    async def add(
        self,
        ctx: AccessContext,
        *,
        user_id: uuid.UUID,
        role: Role,
        invited_by_user_id: uuid.UUID | None = None,
    ) -> Membership:
        row = WorkspaceMember(
            workspace_id=ctx.workspace_id,
            user_id=user_id,
            role=role,
            invited_by_user_id=invited_by_user_id,
        )
        self._session.add(row)
        await flush_translating_conflicts(self._session)
        await self._session.refresh(row)
        return to_entity(row)

    async def add_owner(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
        """Seed the first owner, at workspace creation.

        The one membership write with no `AccessContext`, because at this moment
        the caller has no role in a workspace that did not exist a statement
        ago. It is called only from workspace creation, inside the same
        transaction, so a workspace with no owner is never committed.
        """
        row = WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=Role.OWNER)
        self._session.add(row)
        await flush_translating_conflicts(self._session)
        await self._session.refresh(row)
        return to_entity(row)

    async def change_role(
        self, ctx: AccessContext, *, user_id: uuid.UUID, role: Role
    ) -> Membership:
        await self._guard_last_owner(ctx, user_id=user_id, new_role=role)

        stmt = (
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == ctx.workspace_id,
                WorkspaceMember.user_id == user_id,
            )
            .values(role=role)
            .returning(WorkspaceMember)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            msg = "That member was not found in this workspace."
            raise NotFoundError(msg, workspace_id=str(ctx.workspace_id))
        return to_entity(row)

    async def remove(self, ctx: AccessContext, *, user_id: uuid.UUID) -> None:
        await self._guard_last_owner(ctx, user_id=user_id, new_role=None)

        # RETURNING rather than rowcount: it is typed, and it distinguishes
        # "deleted nothing" from "deleted something" in one round trip.
        stmt = (
            delete(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == ctx.workspace_id,
                WorkspaceMember.user_id == user_id,
            )
            .returning(WorkspaceMember.user_id)
        )
        removed = (await self._session.execute(stmt)).scalar_one_or_none()
        if removed is None:
            msg = "That member was not found in this workspace."
            raise NotFoundError(msg, workspace_id=str(ctx.workspace_id))

    async def count_owners(self, workspace_id: uuid.UUID) -> int:
        """Served by `ix_workspace_members_owners`, a partial index over owners."""
        count = await self._session.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == Role.OWNER,
            )
        )
        return count or 0

    async def _guard_last_owner(
        self, ctx: AccessContext, *, user_id: uuid.UUID, new_role: Role | None
    ) -> None:
        """Refuse to leave a workspace with no owner.

        No table constraint can express "at least one row with this role", so it
        is enforced here and covered by tests. A workspace without an owner is
        unadministrable: nobody could invite a member, change a role, or delete
        it, and recovery would require direct database access.

        `FOR UPDATE` on the owner rows serialises two concurrent demotions;
        without it both would see two owners, both would proceed, and the
        workspace would end up with none.
        """
        if new_role is Role.OWNER:
            return

        current = await self._session.get(WorkspaceMember, (ctx.workspace_id, user_id))
        if current is None or current.role is not Role.OWNER:
            return

        owner_ids = (
            (
                await self._session.execute(
                    select(WorkspaceMember.user_id)
                    .where(
                        WorkspaceMember.workspace_id == ctx.workspace_id,
                        WorkspaceMember.role == Role.OWNER,
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if len(owner_ids) <= 1:
            msg = (
                "This workspace would be left without an owner. "
                "Promote another member to owner first."
            )
            raise ConflictError(msg, workspace_id=str(ctx.workspace_id))
