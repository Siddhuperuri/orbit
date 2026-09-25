"""Authorization primitives.

`AccessContext` is the value every workspace-scoped repository method requires.
Its presence in the signature is the mechanism that makes forgetting a tenant
check a type error rather than a data breach (ADR-0004).

The role-to-permission mapping lives here, in code, rather than in a
`permissions` table. It is a closed, static set that does not vary by tenant or
by deployment, so a table would add a database round trip to answer a question a
frozen mapping answers, and would create a second place for the rule to live and
therefore a way for the database and the code to disagree. If per-workspace
custom roles ever become a requirement, that is the point at which a table earns
its place -- and this mapping becomes the seed data for it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from orbit.domain.errors import PermissionDeniedError


class Role(StrEnum):
    """A member's role within one workspace."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Permission(StrEnum):
    """A capability a route requires.

    Routes declare the *permission* they need, never the role. Adding a role is
    then a change to one mapping instead of an audit of every endpoint.
    """

    WORKSPACE_READ = "workspace:read"
    WORKSPACE_UPDATE = "workspace:update"
    WORKSPACE_DELETE = "workspace:delete"

    MEMBER_READ = "member:read"
    MEMBER_INVITE = "member:invite"
    MEMBER_UPDATE_ROLE = "member:update_role"
    MEMBER_REMOVE = "member:remove"

    DOCUMENT_READ = "document:read"
    DOCUMENT_CREATE = "document:create"
    DOCUMENT_UPDATE = "document:update"
    DOCUMENT_DELETE = "document:delete"

    FOLDER_READ = "folder:read"
    FOLDER_WRITE = "folder:write"

    TAG_READ = "tag:read"
    TAG_WRITE = "tag:write"

    SEARCH_QUERY = "search:query"
    CHAT_USE = "chat:use"

    AUDIT_READ = "audit:read"


_VIEWER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.WORKSPACE_READ,
        Permission.MEMBER_READ,
        Permission.DOCUMENT_READ,
        Permission.FOLDER_READ,
        Permission.TAG_READ,
        Permission.SEARCH_QUERY,
        Permission.CHAT_USE,
    }
)

_MEMBER_PERMISSIONS: frozenset[Permission] = _VIEWER_PERMISSIONS | {
    Permission.DOCUMENT_CREATE,
    Permission.DOCUMENT_UPDATE,
    Permission.DOCUMENT_DELETE,
    Permission.FOLDER_WRITE,
    Permission.TAG_WRITE,
}

_ADMIN_PERMISSIONS: frozenset[Permission] = _MEMBER_PERMISSIONS | {
    Permission.WORKSPACE_UPDATE,
    Permission.MEMBER_INVITE,
    Permission.MEMBER_UPDATE_ROLE,
    Permission.MEMBER_REMOVE,
    Permission.AUDIT_READ,
}

# The owner holds every permission by construction, so adding a permission can
# never silently leave the owner unable to perform it.
_OWNER_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.OWNER: _OWNER_PERMISSIONS,
        Role.ADMIN: _ADMIN_PERMISSIONS,
        Role.MEMBER: _MEMBER_PERMISSIONS,
        Role.VIEWER: _VIEWER_PERMISSIONS,
    }
)


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Who is acting, in which workspace, with what role.

    Constructed once per request after authentication and membership lookup, and
    threaded through every repository call. It is immutable so that no layer can
    widen its own authority mid-request.
    """

    user_id: uuid.UUID
    workspace_id: uuid.UUID
    role: Role

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        """Raise unless the caller holds ``permission``.

        Used where the caller provably has workspace access but may lack this
        specific capability. Where the caller may not know the resource exists
        at all, raise ``NotFoundError`` instead -- a 403 would confirm existence
        and leak it across tenants.
        """
        if not self.has(permission):
            msg = "You do not have permission to perform this action."
            raise PermissionDeniedError(
                msg,
                permission=permission.value,
                role=self.role.value,
                workspace_id=str(self.workspace_id),
            )


@dataclass(frozen=True, slots=True)
class SystemContext:
    """Authority for operations that belong to no user.

    Scheduled pruning, orphan reclamation, and migrations act on every tenant by
    design. Giving those an explicit, named context -- rather than letting them
    pass ``None`` or a synthetic admin -- keeps privileged access visible in code
    review and greppable in the codebase.
    """

    reason: str
