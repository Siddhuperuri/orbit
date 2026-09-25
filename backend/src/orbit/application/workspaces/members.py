"""Membership management use cases.

Grouped in one module, unlike the workspace use cases, because they share a
single genuinely interesting rule -- "a workspace always has at least one
owner" -- and keeping the operations that can trip it next to each other makes
that rule easier to audit than four files would.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from orbit.domain.access import AccessContext, Permission, Role
from orbit.domain.errors import NotFoundError
from orbit.domain.models.entities import Membership
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class ListMembers:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext) -> Sequence[Membership]:
        ctx.require(Permission.MEMBER_READ)
        async with self._uow_factory() as uow:
            return await uow.memberships.list_members(ctx)


class InviteMember:
    """Adds an existing account to the workspace directly.

    Invitation by email to someone without an account -- a signed token,
    expiry, a claim step -- is a real feature with its own state machine and
    is deliberately out of scope here; this covers the case that exercises the
    same authorization and uniqueness rules without that machinery.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, audit: AuditSink) -> None:
        self._uow_factory = uow_factory
        self._audit = audit

    async def execute(self, ctx: AccessContext, *, user_id: uuid.UUID, role: Role) -> Membership:
        ctx.require(Permission.MEMBER_INVITE)

        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                msg = "No account with that ID was found."
                raise NotFoundError(msg, user_id=str(user_id))

            added = await uow.memberships.add(ctx, user_id=user_id, role=role)
            await uow.commit()

        await self._audit.record(
            AuditEvent(
                action=AuditAction.MEMBER_ADDED,
                actor_user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
                resource_type="membership",
                resource_id=user_id,
                metadata={"role": role.value},
            )
        )
        return added


class ChangeMemberRole:
    def __init__(self, uow_factory: UnitOfWorkFactory, audit: AuditSink) -> None:
        self._uow_factory = uow_factory
        self._audit = audit

    async def execute(self, ctx: AccessContext, *, user_id: uuid.UUID, role: Role) -> Membership:
        ctx.require(Permission.MEMBER_UPDATE_ROLE)
        async with self._uow_factory() as uow:
            previous = await uow.memberships.get(ctx.workspace_id, user_id)
            changed = await uow.memberships.change_role(ctx, user_id=user_id, role=role)
            await uow.commit()

        # Both roles are recorded. A privilege *escalation* and a
        # demotion are the same event type, and only the pair tells them
        # apart when the trail is read months later.
        await self._audit.record(
            AuditEvent(
                action=AuditAction.MEMBER_ROLE_CHANGED,
                actor_user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
                resource_type="membership",
                resource_id=user_id,
                metadata={
                    "from_role": previous.role.value if previous else None,
                    "to_role": role.value,
                },
            )
        )
        return changed


class RemoveMember:
    def __init__(self, uow_factory: UnitOfWorkFactory, audit: AuditSink) -> None:
        self._uow_factory = uow_factory
        self._audit = audit

    async def execute(self, ctx: AccessContext, *, user_id: uuid.UUID) -> None:
        ctx.require(Permission.MEMBER_REMOVE)
        async with self._uow_factory() as uow:
            await uow.memberships.remove(ctx, user_id=user_id)
            await uow.commit()

        await self._audit.record(
            AuditEvent(
                action=AuditAction.MEMBER_REMOVED,
                actor_user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
                resource_type="membership",
                resource_id=user_id,
            )
        )
