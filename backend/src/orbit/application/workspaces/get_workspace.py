"""Fetch a workspace the caller already has resolved access to."""

from __future__ import annotations

from orbit.domain.access import AccessContext
from orbit.domain.errors import NotFoundError
from orbit.domain.models.entities import Workspace
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class GetWorkspace:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext) -> Workspace:
        async with self._uow_factory() as uow:
            workspace = await uow.workspaces.get(ctx)
        if workspace is None:
            msg = "Workspace not found."
            raise NotFoundError(msg, workspace_id=str(ctx.workspace_id))
        return workspace
