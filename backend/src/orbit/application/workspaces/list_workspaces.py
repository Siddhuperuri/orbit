"""List the workspaces a user belongs to, with their role in each."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from orbit.domain.access import Role
from orbit.domain.models.entities import Workspace
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class ListWorkspaces:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, *, user_id: uuid.UUID) -> Sequence[tuple[Workspace, Role]]:
        async with self._uow_factory() as uow:
            return await uow.workspaces.list_for_user(user_id)
