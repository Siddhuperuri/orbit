"""Workspace and membership use cases, and their authorization boundaries.

Every mutating use case calls `ctx.require(...)` before touching a
repository (domain/access.py). These tests exist to prove that boundary holds
for each one specifically -- a VIEWER (or MEMBER, where members-only
permissions are involved) must be rejected before any write happens, not
merely documented as rejected.
"""

from __future__ import annotations

import uuid

import pytest

from orbit.application.access import ResolveAccessContext
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.application.workspaces.delete_workspace import DeleteWorkspace
from orbit.application.workspaces.get_workspace import GetWorkspace
from orbit.application.workspaces.list_workspaces import ListWorkspaces
from orbit.application.workspaces.members import (
    ChangeMemberRole,
    InviteMember,
    ListMembers,
    RemoveMember,
)
from orbit.application.workspaces.rename_workspace import RenameWorkspace
from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from tests.unit.fakes.in_memory_unit_of_work import FakeUnitOfWorkFactory
from tests.unit.fakes.security_doubles import RecordingAuditSink


@pytest.fixture
def uow_factory() -> FakeUnitOfWorkFactory:
    return FakeUnitOfWorkFactory()


@pytest.fixture
async def owner_id(uow_factory: FakeUnitOfWorkFactory) -> uuid.UUID:
    user = await uow_factory().users.create(
        email="owner@example.com", password_hash="h", full_name="Owner"
    )
    return user.id


@pytest.fixture
async def other_user_id(uow_factory: FakeUnitOfWorkFactory) -> uuid.UUID:
    user = await uow_factory().users.create(
        email="other@example.com", password_hash="h", full_name="Other"
    )
    return user.id


@pytest.fixture
async def workspace_id(uow_factory: FakeUnitOfWorkFactory, owner_id: uuid.UUID) -> uuid.UUID:
    create = CreateWorkspace(uow_factory)
    workspace = await create.execute(name="Research", created_by_user_id=owner_id)
    return workspace.id


def _ctx(workspace_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> AccessContext:
    return AccessContext(user_id=user_id, workspace_id=workspace_id, role=role)


class TestCreateWorkspace:
    async def test_the_creator_becomes_owner_atomically(
        self, uow_factory: FakeUnitOfWorkFactory, owner_id: uuid.UUID
    ) -> None:
        """The invariant this use case exists to guarantee: a workspace with
        no owner must never be a state the fake (or the database) accepts."""
        create = CreateWorkspace(uow_factory)
        workspace = await create.execute(name="Research", created_by_user_id=owner_id)

        membership = await uow_factory().memberships.get(workspace.id, owner_id)
        assert membership is not None
        assert membership.role is Role.OWNER

    async def test_derives_a_url_safe_slug(
        self, uow_factory: FakeUnitOfWorkFactory, owner_id: uuid.UUID
    ) -> None:
        create = CreateWorkspace(uow_factory)
        workspace = await create.execute(
            name="R&D / Special Projects!!", created_by_user_id=owner_id
        )
        assert workspace.slug
        assert " " not in workspace.slug
        assert workspace.slug == workspace.slug.lower()


class TestListWorkspaces:
    async def test_lists_only_workspaces_the_user_belongs_to(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        other_user_id: uuid.UUID,
    ) -> None:
        await CreateWorkspace(uow_factory).execute(
            name="Someone Else's", created_by_user_id=other_user_id
        )
        listed = await ListWorkspaces(uow_factory).execute(user_id=owner_id)
        assert [w.id for w, _ in listed] == [workspace_id]

    async def test_reports_the_caller_s_role_in_each(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        listed = await ListWorkspaces(uow_factory).execute(user_id=owner_id)
        assert listed[0][1] is Role.OWNER


class TestGetWorkspace:
    async def test_a_member_can_read_it(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        get = GetWorkspace(uow_factory)
        workspace = await get.execute(_ctx(workspace_id, owner_id, Role.VIEWER))
        assert workspace.id == workspace_id

    async def test_reading_is_available_to_every_role_including_viewer(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        """Read access has no authorization boundary to test *against* --
        this asserts that fact explicitly, so a future change that adds one
        by accident is caught here."""
        get = GetWorkspace(uow_factory)
        for role in Role:
            workspace = await get.execute(_ctx(workspace_id, owner_id, role))
            assert workspace.id == workspace_id


class TestRenameWorkspaceAuthorizationBoundary:
    async def test_owner_can_rename(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        rename = RenameWorkspace(uow_factory)
        renamed = await rename.execute(
            _ctx(workspace_id, owner_id, Role.OWNER), name="New Name", expected_version=1
        )
        assert renamed.name == "New Name"

    async def test_admin_can_rename(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        rename = RenameWorkspace(uow_factory)
        renamed = await rename.execute(
            _ctx(workspace_id, owner_id, Role.ADMIN), name="New Name", expected_version=1
        )
        assert renamed.name == "New Name"

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    async def test_member_and_viewer_cannot_rename(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        role: Role,
    ) -> None:
        rename = RenameWorkspace(uow_factory)
        with pytest.raises(PermissionDeniedError):
            await rename.execute(
                _ctx(workspace_id, owner_id, role), name="Hijacked", expected_version=1
            )

    async def test_a_rejected_rename_leaves_the_name_unchanged(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        """The permission check happens before any repository call, so a
        denied rename must not even reach -- let alone mutate -- the row."""
        rename = RenameWorkspace(uow_factory)
        with pytest.raises(PermissionDeniedError):
            await rename.execute(
                _ctx(workspace_id, owner_id, Role.VIEWER), name="Hijacked", expected_version=1
            )
        workspace = uow_factory.state.workspaces[workspace_id]
        assert workspace.name == "Research"


class TestDeleteWorkspaceAuthorizationBoundary:
    async def test_owner_can_delete(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        delete = DeleteWorkspace(uow_factory, RecordingAuditSink())
        await delete.execute(_ctx(workspace_id, owner_id, Role.OWNER))
        assert uow_factory.state.workspaces[workspace_id].is_deleted

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.MEMBER, Role.VIEWER])
    async def test_nobody_but_the_owner_can_delete(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        role: Role,
    ) -> None:
        """Deletion is reserved for owners specifically -- even an admin, who
        can manage every member, cannot delete the workspace out from under
        the owners (see domain/access.py)."""
        delete = DeleteWorkspace(uow_factory, RecordingAuditSink())
        with pytest.raises(PermissionDeniedError):
            await delete.execute(_ctx(workspace_id, owner_id, role))
        assert not uow_factory.state.workspaces[workspace_id].is_deleted


class TestMembershipAuthorizationBoundary:
    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    async def test_member_and_viewer_cannot_invite(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        other_user_id: uuid.UUID,
        role: Role,
    ) -> None:
        invite = InviteMember(uow_factory, RecordingAuditSink())
        with pytest.raises(PermissionDeniedError):
            await invite.execute(
                _ctx(workspace_id, owner_id, role), user_id=other_user_id, role=Role.MEMBER
            )

    async def test_admin_can_invite(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        other_user_id: uuid.UUID,
    ) -> None:
        invite = InviteMember(uow_factory, RecordingAuditSink())
        membership = await invite.execute(
            _ctx(workspace_id, owner_id, Role.ADMIN), user_id=other_user_id, role=Role.MEMBER
        )
        assert membership.role is Role.MEMBER

    async def test_inviting_an_unknown_user_id_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        invite = InviteMember(uow_factory, RecordingAuditSink())
        with pytest.raises(NotFoundError):
            await invite.execute(
                _ctx(workspace_id, owner_id, Role.OWNER), user_id=uuid.uuid4(), role=Role.MEMBER
            )

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    async def test_member_and_viewer_cannot_change_roles(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        other_user_id: uuid.UUID,
        role: Role,
    ) -> None:
        await InviteMember(uow_factory, RecordingAuditSink()).execute(
            _ctx(workspace_id, owner_id, Role.OWNER), user_id=other_user_id, role=Role.VIEWER
        )
        change_role = ChangeMemberRole(uow_factory, RecordingAuditSink())
        with pytest.raises(PermissionDeniedError):
            await change_role.execute(
                _ctx(workspace_id, owner_id, role), user_id=other_user_id, role=Role.ADMIN
            )

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    async def test_member_and_viewer_cannot_remove_members(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        other_user_id: uuid.UUID,
        role: Role,
    ) -> None:
        await InviteMember(uow_factory, RecordingAuditSink()).execute(
            _ctx(workspace_id, owner_id, Role.OWNER), user_id=other_user_id, role=Role.VIEWER
        )
        remove = RemoveMember(uow_factory, RecordingAuditSink())
        with pytest.raises(PermissionDeniedError):
            await remove.execute(_ctx(workspace_id, owner_id, role), user_id=other_user_id)

    async def test_the_last_owner_cannot_be_demoted_even_by_another_owner(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        """A second, distinct guarantee layered on top of the permission
        check: even a caller who *does* hold MEMBER_UPDATE_ROLE cannot use it
        to strand the workspace without an owner."""
        change_role = ChangeMemberRole(uow_factory, RecordingAuditSink())
        with pytest.raises(ConflictError, match="without an owner"):
            await change_role.execute(
                _ctx(workspace_id, owner_id, Role.OWNER), user_id=owner_id, role=Role.ADMIN
            )

    async def test_viewer_can_still_list_members(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        other_user_id: uuid.UUID,
    ) -> None:
        """Reading the member list is a `MEMBER_READ` permission every role
        holds -- unlike inviting, changing roles, or removing."""
        await InviteMember(uow_factory, RecordingAuditSink()).execute(
            _ctx(workspace_id, owner_id, Role.OWNER), user_id=other_user_id, role=Role.VIEWER
        )
        members = await ListMembers(uow_factory).execute(_ctx(workspace_id, owner_id, Role.VIEWER))
        assert len(members) == 2


class TestCrossWorkspaceIsolationAtTheUseCaseLayer:
    """The actual gate is `ResolveAccessContext` (application/access.py):
    every workspace-scoped route resolves a context through it before
    calling any use case. Once a context exists, its `workspace_id` and
    `role` are trusted -- correctly, since producing one already proved
    membership. So the boundary to test is *there*, not by forging a
    context and handing it to a use case, which would only prove that
    repositories trust their caller (true by design; see ADR-0004 and
    `tests/integration/test_tenant_isolation.py`)."""

    @pytest.fixture
    async def other_workspace_id(
        self, uow_factory: FakeUnitOfWorkFactory, other_user_id: uuid.UUID
    ) -> uuid.UUID:
        create = CreateWorkspace(uow_factory)
        workspace = await create.execute(name="Someone Else's", created_by_user_id=other_user_id)
        return workspace.id

    async def test_resolving_a_context_for_a_workspace_you_do_not_belong_to_is_not_found(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        owner_id: uuid.UUID,
        other_workspace_id: uuid.UUID,
    ) -> None:
        """`owner_id` owns a *different* workspace and has no membership in
        `other_workspace_id` at all -- this is what actually stops them from
        ever obtaining a context that would let them rename it."""
        resolve = ResolveAccessContext(uow_factory)
        with pytest.raises(NotFoundError):
            await resolve.execute(user_id=owner_id, workspace_id=other_workspace_id)

    async def test_the_legitimate_owner_still_resolves_a_context_for_it(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        other_user_id: uuid.UUID,
        other_workspace_id: uuid.UUID,
    ) -> None:
        """Confirms the previous test is checking isolation, not a bug that
        would reject everyone."""
        resolve = ResolveAccessContext(uow_factory)
        ctx = await resolve.execute(user_id=other_user_id, workspace_id=other_workspace_id)
        assert ctx.role is Role.OWNER
