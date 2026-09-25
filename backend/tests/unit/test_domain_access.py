"""The role-to-permission mapping and `AccessContext.require`.

This mapping is the entire authorization model (domain/access.py) -- every
route's boundary is a `require()` call against it, so its correctness is
tested directly here rather than only incidentally through API tests.
"""

from __future__ import annotations

import uuid

import pytest

from orbit.domain.access import ROLE_PERMISSIONS, AccessContext, Permission, Role
from orbit.domain.errors import PermissionDeniedError


def _ctx(role: Role) -> AccessContext:
    return AccessContext(user_id=uuid.uuid4(), workspace_id=uuid.uuid4(), role=role)


class TestRoleHierarchy:
    """Each role's permissions are a strict superset of the one below it.

    This is a property of the *design*, not an accident of how the sets were
    typed out, so it is asserted directly rather than trusted.
    """

    def test_member_includes_every_viewer_permission(self) -> None:
        assert ROLE_PERMISSIONS[Role.VIEWER] <= ROLE_PERMISSIONS[Role.MEMBER]

    def test_admin_includes_every_member_permission(self) -> None:
        assert ROLE_PERMISSIONS[Role.MEMBER] <= ROLE_PERMISSIONS[Role.ADMIN]

    def test_owner_includes_every_admin_permission(self) -> None:
        assert ROLE_PERMISSIONS[Role.ADMIN] <= ROLE_PERMISSIONS[Role.OWNER]

    def test_owner_holds_every_permission_that_exists(self) -> None:
        """By construction (`frozenset(Permission)`), so adding a new
        permission can never silently leave the owner unable to perform it."""
        assert ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission)

    def test_every_role_has_a_declared_permission_set(self) -> None:
        assert set(ROLE_PERMISSIONS) == set(Role)


class TestViewerIsReadOnly:
    @pytest.mark.parametrize(
        "permission",
        [
            Permission.DOCUMENT_CREATE,
            Permission.DOCUMENT_UPDATE,
            Permission.DOCUMENT_DELETE,
            Permission.FOLDER_WRITE,
            Permission.TAG_WRITE,
            Permission.WORKSPACE_UPDATE,
            Permission.WORKSPACE_DELETE,
            Permission.MEMBER_INVITE,
            Permission.MEMBER_REMOVE,
        ],
    )
    def test_cannot_mutate_anything(self, permission: Permission) -> None:
        assert not _ctx(Role.VIEWER).has(permission)

    @pytest.mark.parametrize(
        "permission",
        [
            Permission.WORKSPACE_READ,
            Permission.DOCUMENT_READ,
            Permission.SEARCH_QUERY,
            Permission.CHAT_USE,
        ],
    )
    def test_can_read_and_ask_questions(self, permission: Permission) -> None:
        assert _ctx(Role.VIEWER).has(permission)


class TestMemberCanManageDocumentsNotMembers:
    def test_can_create_documents(self) -> None:
        assert _ctx(Role.MEMBER).has(Permission.DOCUMENT_CREATE)

    @pytest.mark.parametrize(
        "permission",
        [
            Permission.MEMBER_INVITE,
            Permission.MEMBER_UPDATE_ROLE,
            Permission.MEMBER_REMOVE,
            Permission.WORKSPACE_DELETE,
            Permission.AUDIT_READ,
        ],
    )
    def test_cannot_manage_the_workspace_or_its_members(self, permission: Permission) -> None:
        assert not _ctx(Role.MEMBER).has(permission)


class TestAdminManagesMembersNotTheWorkspaceItself:
    @pytest.mark.parametrize(
        "permission",
        [Permission.MEMBER_INVITE, Permission.MEMBER_UPDATE_ROLE, Permission.MEMBER_REMOVE],
    )
    def test_can_manage_members(self, permission: Permission) -> None:
        assert _ctx(Role.ADMIN).has(permission)

    def test_cannot_delete_the_workspace(self) -> None:
        """Deletion is reserved for owners specifically -- an admin who could
        delete the workspace could lock every owner out of their own data."""
        assert not _ctx(Role.ADMIN).has(Permission.WORKSPACE_DELETE)


class TestRequire:
    def test_passes_silently_when_the_role_holds_the_permission(self) -> None:
        _ctx(Role.OWNER).require(Permission.WORKSPACE_DELETE)  # does not raise

    def test_raises_permission_denied_when_it_does_not(self) -> None:
        with pytest.raises(PermissionDeniedError):
            _ctx(Role.VIEWER).require(Permission.DOCUMENT_DELETE)

    def test_the_error_never_reveals_whether_the_resource_exists(self) -> None:
        """`PermissionDeniedError` maps to 403, which is only correct because
        the caller already provably has *some* access to this workspace --
        it must never be raised for a caller who might not (see NotFoundError
        and ADR-0004)."""
        error = None
        try:
            _ctx(Role.VIEWER).require(Permission.WORKSPACE_DELETE)
        except PermissionDeniedError as exc:
            error = exc
        assert error is not None
        assert error.http_status == 403

    def test_error_context_carries_the_permission_and_role_for_logs(self) -> None:
        ctx = _ctx(Role.VIEWER)
        try:
            ctx.require(Permission.WORKSPACE_DELETE)
        except PermissionDeniedError as exc:
            assert exc.context["permission"] == Permission.WORKSPACE_DELETE.value
            assert exc.context["role"] == Role.VIEWER.value
        else:
            pytest.fail("expected PermissionDeniedError")
