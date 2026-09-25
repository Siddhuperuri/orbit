"""Users, workspaces, membership, and ownership, against a real database.

The constraint tests here matter more than the happy paths: a uniqueness rule
that is checked in Python instead of enforced by the database is a race
condition, and these assert that the database is what rejects the duplicate.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.models.entities import User, Workspace
from orbit.infrastructure.db.unit_of_work import UnitOfWork
from tests.integration.conftest import unique_email, unique_slug

pytestmark = pytest.mark.integration


class TestUserCreation:
    async def test_creates_a_user(self, uow: UnitOfWork) -> None:
        created = await uow.users.create(
            email="Ada@Example.test", password_hash="$argon2id$hash", full_name="Ada Lovelace"
        )
        assert created.id is not None
        assert created.full_name == "Ada Lovelace"
        assert created.is_active is True
        assert created.token_epoch == 0
        assert created.deleted_at is None

    async def test_password_hash_is_not_on_the_entity(self, uow: UnitOfWork, user: User) -> None:
        """It would otherwise reach every log line and traceback that touches a
        user. It is fetched separately, at the one call site that needs it."""
        assert not hasattr(user, "password_hash")
        assert await uow.users.get_password_hash(user.id) == "$argon2id$fake$hash"

    async def test_email_lookup_is_case_insensitive(self, uow: UnitOfWork) -> None:
        await uow.users.create(
            email="Grace@Example.test", password_hash="h", full_name="Grace Hopper"
        )
        assert await uow.users.get_by_email("grace@example.test") is not None
        assert await uow.users.get_by_email("GRACE@EXAMPLE.TEST") is not None

    async def test_duplicate_email_is_rejected_by_the_database(self, uow: UnitOfWork) -> None:
        """Enforced by `uq_users_email_lower`, not by a check-then-insert --
        which would be a race between two concurrent registrations."""
        email = unique_email()
        await uow.users.create(email=email, password_hash="h", full_name="First")
        with pytest.raises(ConflictError):
            await uow.users.create(email=email.upper(), password_hash="h", full_name="Second")

    async def test_duplicate_email_surfaces_as_a_conflict_not_a_driver_error(
        self, uow: UnitOfWork
    ) -> None:
        """A raw `IntegrityError` escaping the repository reaches the error
        handler as an unrecognised exception and is answered as a **500**, so
        the one thing a person can actually fix -- "that address is taken" --
        would be reported as a fault on our side. The repository flushes through
        the translator for exactly this reason.
        """
        email = unique_email()
        await uow.users.create(email=email, password_hash="h", full_name="First")

        with pytest.raises(ConflictError) as raised:
            await uow.users.create(email=email, password_hash="h", full_name="Second")

        assert raised.value.code == "CONFLICT"
        assert raised.value.http_status == 409
        # The message is user-facing, so it names the constraint in plain words
        # and leaks neither the SQL nor the schema.
        assert "already exists" in str(raised.value)
        assert "uq_users_email_lower" not in str(raised.value)
        assert "INSERT" not in str(raised.value)

    async def test_blank_name_is_rejected(self, uow: UnitOfWork) -> None:
        # A CHECK violation has no mapped message, so it arrives as the generic
        # conflict rather than a 500. The API layer rejects this shape long
        # before the database does; this is the constraint behind that.
        with pytest.raises(ConflictError):
            await uow.users.create(email=unique_email(), password_hash="h", full_name="   ")

    async def test_email_without_a_local_part_is_rejected(self, uow: UnitOfWork) -> None:
        with pytest.raises(ConflictError):
            await uow.users.create(email="@example.test", password_hash="h", full_name="X")


class TestUserDeletion:
    async def test_soft_delete_hides_the_user(self, uow: UnitOfWork, user: User) -> None:
        await uow.users.soft_delete(user.id)
        assert await uow.users.get(user.id) is None
        assert await uow.users.get_by_email(user.email) is None

    async def test_soft_delete_invalidates_live_sessions(self, uow: UnitOfWork, user: User) -> None:
        """Bumping the token epoch is what makes deletion take effect before the
        15-minute access token expires (ADR-0003)."""
        epoch = await uow.session.scalar(
            text("SELECT token_epoch FROM users WHERE id = :i"), {"i": user.id}
        )
        await uow.users.soft_delete(user.id)
        after = await uow.session.scalar(
            text("SELECT token_epoch FROM users WHERE id = :i"), {"i": user.id}
        )
        assert after == epoch + 1

    async def test_the_email_becomes_available_again(self, uow: UnitOfWork) -> None:
        """The unique index is partial on live rows, so a deleted account does
        not permanently reserve its address."""
        email = unique_email()
        first = await uow.users.create(email=email, password_hash="h", full_name="First")
        await uow.users.soft_delete(first.id)
        second = await uow.users.create(email=email, password_hash="h", full_name="Second")
        assert second.id != first.id

    async def test_password_change_invalidates_live_sessions(
        self, uow: UnitOfWork, user: User
    ) -> None:
        await uow.users.set_password(user.id, "$argon2id$new")
        refreshed = await uow.users.get(user.id)
        assert refreshed is not None
        assert refreshed.token_epoch == user.token_epoch + 1


class TestWorkspaceCreation:
    async def test_creates_a_workspace(self, uow: UnitOfWork, user: User) -> None:
        created = await uow.workspaces.create(
            name="Research", slug=unique_slug(), created_by_user_id=user.id
        )
        assert created.name == "Research"
        assert created.created_by_user_id == user.id
        assert created.version == 1

    async def test_slug_is_globally_unique(self, uow: UnitOfWork, user: User) -> None:
        """A `ConflictError`, not the driver's `IntegrityError`: "that URL is
        taken" is something the person can act on, and an untranslated driver
        error would reach them as a 500 instead."""
        slug = unique_slug()
        await uow.workspaces.create(name="A", slug=slug, created_by_user_id=user.id)
        with pytest.raises(ConflictError, match="already taken"):
            await uow.workspaces.create(name="B", slug=slug.upper(), created_by_user_id=user.id)

    @pytest.mark.parametrize("slug", ["A", "has space", "-leading", "trailing-", "x", "üñî"])
    async def test_malformed_slugs_are_rejected(
        self, uow: UnitOfWork, user: User, slug: str
    ) -> None:
        """The slug ends up in a URL path, so its charset is constrained by the
        database as well as by the application.

        A CHECK violation has no mapped message, so it arrives as the generic
        conflict. Reaching here at all means the application-level validation
        was bypassed; the point is that the database still refuses, and refuses
        in a way the caller can read.
        """
        with pytest.raises(ConflictError):
            await uow.workspaces.create(name="X", slug=slug, created_by_user_id=user.id)

    async def test_creator_reference_survives_account_purge(
        self, uow: UnitOfWork, workspace: Workspace, user: User
    ) -> None:
        """A workspace belongs to its members, not to whoever created it, so a
        hard-deleted account must not take it down."""
        await uow.session.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
        remaining = await uow.session.scalar(
            text("SELECT created_by_user_id FROM workspaces WHERE id = :i"), {"i": workspace.id}
        )
        assert remaining is None


class TestMembership:
    async def test_adds_a_member(
        self, uow: UnitOfWork, ctx: AccessContext, other_user: User
    ) -> None:
        added = await uow.memberships.add(ctx, user_id=other_user.id, role=Role.MEMBER)
        assert added.role is Role.MEMBER
        assert added.workspace_id == ctx.workspace_id

    async def test_a_user_has_at_most_one_role_per_workspace(
        self, uow: UnitOfWork, ctx: AccessContext, other_user: User
    ) -> None:
        """Guaranteed by the composite primary key, not by an extra constraint."""
        await uow.memberships.add(ctx, user_id=other_user.id, role=Role.MEMBER)
        with pytest.raises(ConflictError):
            await uow.memberships.add(ctx, user_id=other_user.id, role=Role.VIEWER)

    async def test_role_change(self, uow: UnitOfWork, ctx: AccessContext, other_user: User) -> None:
        await uow.memberships.add(ctx, user_id=other_user.id, role=Role.VIEWER)
        changed = await uow.memberships.change_role(ctx, user_id=other_user.id, role=Role.ADMIN)
        assert changed.role is Role.ADMIN

    async def test_removal_is_a_hard_delete(
        self, uow: UnitOfWork, ctx: AccessContext, other_user: User
    ) -> None:
        """Revoking access must actually revoke it; a soft-deleted membership is
        one forgotten filter away from privilege escalation."""
        await uow.memberships.add(ctx, user_id=other_user.id, role=Role.MEMBER)
        await uow.memberships.remove(ctx, user_id=other_user.id)

        assert await uow.memberships.get(ctx.workspace_id, other_user.id) is None
        rows = await uow.session.scalar(
            text("SELECT count(*) FROM workspace_members WHERE workspace_id = :w AND user_id = :u"),
            {"w": ctx.workspace_id, "u": other_user.id},
        )
        assert rows == 0

    async def test_removing_an_absent_member_is_not_found(
        self, uow: UnitOfWork, ctx: AccessContext, other_user: User
    ) -> None:
        with pytest.raises(NotFoundError):
            await uow.memberships.remove(ctx, user_id=other_user.id)

    async def test_membership_cascades_when_the_workspace_is_purged(
        self, uow: UnitOfWork, ctx: AccessContext, workspace: Workspace, other_user: User
    ) -> None:
        await uow.memberships.add(ctx, user_id=other_user.id, role=Role.MEMBER)
        await uow.session.execute(text("DELETE FROM workspaces WHERE id = :i"), {"i": workspace.id})
        remaining = await uow.session.scalar(
            text("SELECT count(*) FROM workspace_members WHERE workspace_id = :w"),
            {"w": workspace.id},
        )
        assert remaining == 0


class TestOwnership:
    """The last-owner rule.

    No table constraint can express "at least one row with this role", so it is
    enforced in the repository -- which means it needs tests, because nothing
    else will catch its absence.
    """

    async def test_the_sole_owner_cannot_be_removed(
        self, uow: UnitOfWork, ctx: AccessContext, user: User
    ) -> None:
        with pytest.raises(ConflictError, match="without an owner"):
            await uow.memberships.remove(ctx, user_id=user.id)

    async def test_the_sole_owner_cannot_be_demoted(
        self, uow: UnitOfWork, ctx: AccessContext, user: User
    ) -> None:
        with pytest.raises(ConflictError, match="without an owner"):
            await uow.memberships.change_role(ctx, user_id=user.id, role=Role.ADMIN)

    async def test_an_owner_can_leave_once_another_exists(
        self, uow: UnitOfWork, ctx: AccessContext, user: User, other_user: User
    ) -> None:
        await uow.memberships.add(ctx, user_id=other_user.id, role=Role.OWNER)
        await uow.memberships.remove(ctx, user_id=user.id)
        assert await uow.memberships.count_owners(ctx.workspace_id) == 1

    async def test_a_non_owner_can_always_be_removed(
        self, uow: UnitOfWork, ctx: AccessContext, other_user: User
    ) -> None:
        await uow.memberships.add(ctx, user_id=other_user.id, role=Role.MEMBER)
        await uow.memberships.remove(ctx, user_id=other_user.id)
        assert await uow.memberships.count_owners(ctx.workspace_id) == 1

    async def test_promoting_to_owner_is_never_blocked(
        self, uow: UnitOfWork, ctx: AccessContext, other_user: User
    ) -> None:
        await uow.memberships.add(ctx, user_id=other_user.id, role=Role.MEMBER)
        promoted = await uow.memberships.change_role(ctx, user_id=other_user.id, role=Role.OWNER)
        assert promoted.role is Role.OWNER
        assert await uow.memberships.count_owners(ctx.workspace_id) == 2


class TestWorkspaceListing:
    async def test_lists_only_workspaces_the_user_belongs_to(
        self,
        uow: UnitOfWork,
        user: User,
        workspace: Workspace,
        other_workspace: Workspace,
    ) -> None:
        listed = await uow.workspaces.list_for_user(user.id)
        assert [w.id for w, _ in listed] == [workspace.id]

    async def test_returns_the_role_alongside_each_workspace(
        self, uow: UnitOfWork, user: User, workspace: Workspace
    ) -> None:
        listed = await uow.workspaces.list_for_user(user.id)
        assert listed[0][1] is Role.OWNER

    async def test_soft_deleted_workspaces_are_excluded(
        self, uow: UnitOfWork, ctx: AccessContext, user: User
    ) -> None:
        await uow.workspaces.soft_delete(ctx)
        assert await uow.workspaces.list_for_user(user.id) == []
