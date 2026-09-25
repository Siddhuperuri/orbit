"""Soft-delete a workspace."""

from __future__ import annotations

from orbit.domain.access import AccessContext, Permission
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class DeleteWorkspace:
    def __init__(self, uow_factory: UnitOfWorkFactory, audit: AuditSink) -> None:
        self._uow_factory = uow_factory
        self._audit = audit

    async def execute(self, ctx: AccessContext) -> None:
        ctx.require(Permission.WORKSPACE_DELETE)
        async with self._uow_factory() as uow:
            await uow.workspaces.soft_delete(ctx)
            await uow.commit()

        # Audited because the audit log outlives what it describes: this
        # record survives in a table with no foreign keys, so the deletion
        # is still attributable after the workspace itself is purged.
        await self._audit.record(
            AuditEvent(
                action=AuditAction.WORKSPACE_DELETED,
                actor_user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
                resource_type="workspace",
                resource_id=ctx.workspace_id,
            )
        )
