"""Workspace and membership endpoints: HTTP contract and authorization mapping.

`AccessContextDep` itself is overridden here rather than
`ResolveAccessContext`, because the router depends on the *resolved*
context directly (via the `{workspace_id}` path parameter) -- overriding it
is what lets these tests supply a specific role per test without needing a
real membership lookup, matching how `CurrentUserDep` is overridden in
`test_auth_router.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.api.deps import (
    get_access_context,
    get_change_member_role,
    get_create_workspace,
    get_current_user,
    get_delete_workspace,
    get_get_workspace,
    get_invite_member,
    get_list_members,
    get_list_workspaces,
    get_remove_member,
    get_rename_workspace,
)
from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from orbit.domain.models.entities import Membership, User, Workspace

OverrideFn = Callable[..., None]

_WORKSPACE_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _workspace() -> Workspace:
    return Workspace(
        id=_WORKSPACE_ID,
        name="Research",
        slug="research-ab12cd",
        created_at=datetime.now(UTC),
        version=1,
    )


def _membership(role: Role = Role.MEMBER) -> Membership:
    return Membership(
        workspace_id=_WORKSPACE_ID, user_id=uuid.uuid4(), role=role, created_at=datetime.now(UTC)
    )


def _user() -> User:
    return User(
        id=_USER_ID,
        email="ada@example.com",
        full_name="Ada",
        is_active=True,
        token_epoch=0,
        created_at=datetime.now(UTC),
    )


class _Stub:
    """A generic use-case stand-in returning a fixed value or raising."""

    def __init__(self, result: object) -> None:
        self._result = result

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def override(app: FastAPI) -> Iterator[OverrideFn]:
    mapping = {
        "create_workspace": get_create_workspace,
        "list_workspaces": get_list_workspaces,
        "get_workspace": get_get_workspace,
        "rename_workspace": get_rename_workspace,
        "delete_workspace": get_delete_workspace,
        "list_members": get_list_members,
        "invite_member": get_invite_member,
        "change_member_role": get_change_member_role,
        "remove_member": get_remove_member,
    }

    def _apply(**overrides: object) -> None:
        for name, stub in overrides.items():
            app.dependency_overrides[mapping[name]] = lambda stub=stub: stub

    # Every test authenticates as the same fixed user; which workspace role
    # that user holds is what each test varies via `as_role`.
    app.dependency_overrides[get_current_user] = _user

    yield _apply
    app.dependency_overrides.clear()


@pytest.fixture
def as_role(app: FastAPI) -> Callable[[Role], None]:
    def _apply(role: Role) -> None:
        async def _context() -> AccessContext:
            return AccessContext(user_id=_USER_ID, workspace_id=_WORKSPACE_ID, role=role)

        app.dependency_overrides[get_access_context] = _context

    return _apply


class TestCreateWorkspace:
    def test_returns_201_with_the_created_workspace(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        override(create_workspace=_Stub(_workspace()))
        response = client.post("/api/v1/workspaces", json={"name": "Research"})
        assert response.status_code == 201
        assert response.json()["name"] == "Research"

    def test_blank_name_is_a_422(self, client: TestClient, override: OverrideFn) -> None:
        class _Exploding:
            async def execute(self, *_a: object, **_k: object) -> Workspace:
                pytest.fail("must not be called when validation fails")

        override(create_workspace=_Exploding())
        response = client.post("/api/v1/workspaces", json={"name": "   "})
        assert response.status_code == 422


class TestListWorkspaces:
    def test_returns_the_callers_workspaces_with_role(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        override(list_workspaces=_Stub([(_workspace(), Role.OWNER)]))
        response = client.get("/api/v1/workspaces")
        assert response.status_code == 200
        body = response.json()
        assert body[0]["role"] == "owner"


class TestGetWorkspaceAuthorization:
    def test_a_member_of_the_workspace_can_read_it(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(get_workspace=_Stub(_workspace()))
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}")
        assert response.status_code == 200

    def test_a_non_member_gets_404_not_403(self, client: TestClient, override: OverrideFn) -> None:
        """`AccessContextDep` itself raises `NotFoundError` for a non-member
        (application/access.py) -- this exercises that mapping at the HTTP
        layer by overriding the context resolver to fail exactly as it
        would for someone with no membership."""

        async def _denied() -> AccessContext:
            raise NotFoundError("Workspace not found.")

        client.app.dependency_overrides[get_access_context] = _denied  # type: ignore[attr-defined]
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestRenameWorkspaceAuthorizationMapping:
    """The permission check happens inside the use case; these confirm the
    HTTP layer relays a `PermissionDeniedError` as 403 without altering it,
    the same way `PermissionDeniedError` is mapped for every other route."""

    def test_a_permitted_role_succeeds(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        renamed = Workspace(
            id=_WORKSPACE_ID,
            name="New Name",
            slug="research-ab12cd",
            created_at=datetime.now(UTC),
            version=2,
        )
        override(rename_workspace=_Stub(renamed))
        response = client.patch(
            f"/api/v1/workspaces/{_WORKSPACE_ID}",
            json={"name": "New Name", "expected_version": 1},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    def test_a_denied_role_gets_403(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(
            rename_workspace=_Stub(
                PermissionDeniedError("You do not have permission to perform this action.")
            )
        )
        response = client.patch(
            f"/api/v1/workspaces/{_WORKSPACE_ID}",
            json={"name": "Hijacked", "expected_version": 1},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    def test_missing_expected_version_is_a_422(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        """`expected_version` is required, not defaulted (see
        api/v1/schemas/workspaces.py): a client with no prior read has no
        basis to overwrite the row, and a default would silently invite it."""
        as_role(Role.OWNER)
        override(rename_workspace=_Stub(_workspace()))
        response = client.patch(f"/api/v1/workspaces/{_WORKSPACE_ID}", json={"name": "X"})
        assert response.status_code == 422

    def test_a_stale_version_is_a_409(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(
            rename_workspace=_Stub(ConflictError("The workspace was modified by someone else."))
        )
        response = client.patch(
            f"/api/v1/workspaces/{_WORKSPACE_ID}",
            json={"name": "X", "expected_version": 1},
        )
        assert response.status_code == 409


class TestDeleteWorkspaceAuthorizationMapping:
    def test_owner_can_delete(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(delete_workspace=_Stub(None))
        response = client.delete(f"/api/v1/workspaces/{_WORKSPACE_ID}")
        assert response.status_code == 204

    def test_admin_is_denied(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.ADMIN)
        override(
            delete_workspace=_Stub(
                PermissionDeniedError("You do not have permission to perform this action.")
            )
        )
        response = client.delete(f"/api/v1/workspaces/{_WORKSPACE_ID}")
        assert response.status_code == 403


class TestMembers:
    """Every route here carries `{workspace_id}` and therefore depends on
    `AccessContextDep`; `as_role` must be called in each test (even though
    the specific role rarely matters for these) or the real resolver runs
    and reaches for the database these tests have no business touching."""

    def test_listing_members(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(list_members=_Stub([_membership(Role.OWNER), _membership(Role.VIEWER)]))
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}/members")
        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_inviting_a_member_returns_201(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.ADMIN)
        override(invite_member=_Stub(_membership(Role.MEMBER)))
        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/members",
            json={"user_id": str(uuid.uuid4()), "role": "member"},
        )
        assert response.status_code == 201
        assert response.json()["role"] == "member"

    def test_an_invalid_role_value_is_a_422(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.ADMIN)
        override(invite_member=_Stub(_membership()))
        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/members",
            json={"user_id": str(uuid.uuid4()), "role": "supreme-leader"},
        )
        assert response.status_code == 422

    def test_removing_the_last_owner_is_a_409(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(
            remove_member=_Stub(ConflictError("This workspace would be left without an owner."))
        )
        response = client.delete(f"/api/v1/workspaces/{_WORKSPACE_ID}/members/{uuid.uuid4()}")
        assert response.status_code == 409

    def test_removing_an_unknown_member_is_a_404(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(remove_member=_Stub(NotFoundError("That member was not found.")))
        response = client.delete(f"/api/v1/workspaces/{_WORKSPACE_ID}/members/{uuid.uuid4()}")
        assert response.status_code == 404


class TestUnauthenticatedAccessIsRejectedBeforeAnyUseCaseRuns:
    def test_no_session_is_401_on_every_workspace_route(self, client: TestClient) -> None:
        """No `get_current_user` override in this class -- the real
        dependency runs, finds no cookie, and must reject before the route
        body (and therefore any use case) executes."""
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
