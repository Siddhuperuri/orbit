"""Resolve an `AccessContext` from a user and a requested workspace.

This is the one function call that decides whether a caller may act in a
workspace at all -- every API route that touches workspace-scoped data calls it
before doing anything else, and every workspace-scoped repository method then
requires the `AccessContext` it returns (ADR-0004).
"""

from __future__ import annotations

import uuid

from orbit.domain.access import AccessContext
from orbit.domain.errors import NotFoundError
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class ResolveAccessContext:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, *, user_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
        async with self._uow_factory() as uow:
            membership = await uow.memberships.get(workspace_id, user_id)

        if membership is None:
            # Not a member and "workspace does not exist" are indistinguishable
            # on purpose: telling a non-member which workspace IDs are real
            # would leak their existence across the tenant boundary.
            msg = "Workspace not found."
            raise NotFoundError(msg, workspace_id=str(workspace_id))

        return AccessContext(user_id=user_id, workspace_id=workspace_id, role=membership.role)
