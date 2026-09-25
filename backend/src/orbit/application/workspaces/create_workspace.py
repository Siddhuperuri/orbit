"""Create a workspace and seed its creator as owner."""

from __future__ import annotations

import re
import uuid

from orbit.domain.models.entities import Workspace
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

# Same charset as ck_workspaces_slug_format. A random suffix is appended so
# that two workspaces named identically (a common case -- "Engineering",
# "Marketing") do not collide on the first attempt.
_SLUG_UNSAFE = re.compile(r"[^a-z0-9-]+")
_SLUG_SUFFIX_LENGTH = 6


class CreateWorkspace:
    """Owns the invariant that a workspace is never committed without an
    owner: the workspace row and the owner membership are written in one
    transaction (ADR-0004's "at least one owner" rule starts here)."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, *, name: str, created_by_user_id: uuid.UUID) -> Workspace:
        slug = _derive_slug(name)

        async with self._uow_factory() as uow:
            workspace = await uow.workspaces.create(
                name=name, slug=slug, created_by_user_id=created_by_user_id
            )
            await uow.memberships.add_owner(workspace.id, created_by_user_id)
            await uow.commit()
        return workspace


def _derive_slug(name: str) -> str:
    base = _SLUG_UNSAFE.sub("-", name.strip().lower()).strip("-") or "workspace"
    suffix = uuid.uuid4().hex[:_SLUG_SUFFIX_LENGTH]
    # Truncated so the suffix always fits within the 64-character column bound
    # even for a name that is already near the limit.
    return f"{base[:55]}-{suffix}"
