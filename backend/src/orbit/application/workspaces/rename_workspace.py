"""Rename a workspace, under optimistic concurrency control."""

from __future__ import annotations

from orbit.domain.access import AccessContext, Permission
from orbit.domain.models.entities import Workspace
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class RenameWorkspace:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, *, name: str, expected_version: int) -> Workspace:
        ctx.require(Permission.WORKSPACE_UPDATE)
        async with self._uow_factory() as uow:
            renamed = await uow.workspaces.rename(ctx, name, expected_version=expected_version)
            await uow.commit()
        return renamed
