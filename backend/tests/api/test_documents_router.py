"""Document endpoints: HTTP contract, pagination envelope, and authorization.

Every route here carries `{workspace_id}` and depends on `AccessContextDep`,
so `as_role` must be called in every test -- see the equivalent note in
`test_workspaces_router.py` for why skipping it silently reaches for a real,
unreachable database instead of failing fast.
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
    get_current_user,
    get_delete_document,
    get_get_document,
    get_list_documents,
    get_update_document,
)
from orbit.domain.access import AccessContext, Role
from orbit.domain.documents import DocumentListQuery
from orbit.domain.errors import NotFoundError, PermissionDeniedError
from orbit.domain.models.entities import Document, ProcessingStatus, User
from orbit.domain.models.pagination import Page

OverrideFn = Callable[..., None]

_WORKSPACE_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()
_DOCUMENT_ID = uuid.uuid4()


def _document(*, title: str = "Quarterly Report", version: int = 1) -> Document:
    now = datetime.now(UTC)
    return Document(
        id=_DOCUMENT_ID,
        workspace_id=_WORKSPACE_ID,
        title=title,
        created_at=now,
        updated_at=now,
        version=version,
        current_version=None,
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
    def __init__(self, result: object) -> None:
        self._result = result

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def override(app: FastAPI) -> Iterator[OverrideFn]:
    mapping = {
        "list_documents": get_list_documents,
        "get_document": get_get_document,
        "update_document": get_update_document,
        "delete_document": get_delete_document,
    }

    def _apply(**overrides: object) -> None:
        for name, stub in overrides.items():
            app.dependency_overrides[mapping[name]] = lambda stub=stub: stub

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


class TestListDocuments:
    def test_returns_the_pagination_envelope(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(
            list_documents=_Stub(Page(items=(_document(),), next_cursor="opaque-cursor-value"))
        )
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents")
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["title"] == "Quarterly Report"
        assert body["next_cursor"] == "opaque-cursor-value"
        assert body["has_more"] is True

    def test_the_last_page_has_no_cursor(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(list_documents=_Stub(Page(items=(), next_cursor=None)))
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents")
        body = response.json()
        assert body["next_cursor"] is None
        assert body["has_more"] is False

    def test_every_role_can_list(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        override(list_documents=_Stub(Page(items=(), next_cursor=None)))
        for role in Role:
            as_role(role)
            response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents")
            assert response.status_code == 200

    def test_a_limit_over_the_ceiling_is_rejected_at_the_schema(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        """`PageLimit` (api/v1/schemas/pagination.py) bounds `limit` with
        `le=MAX_PAGE_SIZE`, so an oversized value is a 422 before the use
        case ever runs -- the stub is set to explode to prove it. Domain-
        layer `clamp_limit` (domain/models/pagination.py, covered in
        test_pagination.py) is the separate backstop for a caller that
        reaches a repository without going through this schema at all."""
        as_role(Role.VIEWER)

        class _Exploding:
            async def execute(self, *_a: object, **_k: object) -> Page:
                pytest.fail("must not be called when the limit fails schema validation")

        override(list_documents=_Exploding())
        response = client.get(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents", params={"limit": 100000}
        )
        assert response.status_code == 422

    def test_a_non_positive_limit_is_a_422(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(list_documents=_Stub(Page(items=(), next_cursor=None)))
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents", params={"limit": 0})
        assert response.status_code == 422

    def test_status_filter_is_passed_through(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        captured: dict[str, object] = {}

        class _Capturing:
            async def execute(self, _ctx: object, **kwargs: object) -> Page:
                captured.update(kwargs)
                return Page(items=(), next_cursor=None)

        override(list_documents=_Capturing())
        client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents", params={"status": "ready"})
        query = captured["query"]
        assert isinstance(query, DocumentListQuery)
        assert query.status is ProcessingStatus.READY

    def test_an_invalid_status_value_is_a_422(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(list_documents=_Stub(Page(items=(), next_cursor=None)))
        response = client.get(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents", params={"status": "on-fire"}
        )
        assert response.status_code == 422


class TestGetDocument:
    def test_returns_the_document(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(get_document=_Stub(_document()))
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}")
        assert response.status_code == 200
        assert response.json()["id"] == str(_DOCUMENT_ID)

    def test_another_tenants_document_is_a_404(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        """`GetDocument` returns `NotFoundError` for a cross-tenant miss, not
        a 403 -- confirming existence would leak it across the boundary
        (ADR-0004). This asserts the HTTP layer relays that unchanged."""
        as_role(Role.OWNER)
        override(get_document=_Stub(NotFoundError("Document not found.")))
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestUpdateDocument:
    def test_a_permitted_role_succeeds(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(update_document=_Stub(_document(title="Annual Report", version=2)))
        response = client.patch(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}",
            json={"title": "Annual Report", "expected_version": 1},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Annual Report"

    def test_viewer_is_denied(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(
            update_document=_Stub(
                PermissionDeniedError("You do not have permission to perform this action.")
            )
        )
        response = client.patch(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}",
            json={"title": "Hijacked", "expected_version": 1},
        )
        assert response.status_code == 403

    def test_blank_title_is_a_422(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)

        class _Exploding:
            async def execute(self, *_a: object, **_k: object) -> Document:
                pytest.fail("must not be called when validation fails")

        override(update_document=_Exploding())
        response = client.patch(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}",
            json={"title": "   ", "expected_version": 1},
        )
        assert response.status_code == 422


class TestDeleteDocument:
    def test_a_permitted_role_succeeds(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.MEMBER)
        override(delete_document=_Stub(None))
        response = client.delete(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}")
        assert response.status_code == 204

    def test_viewer_is_denied(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(
            delete_document=_Stub(
                PermissionDeniedError("You do not have permission to perform this action.")
            )
        )
        response = client.delete(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}")
        assert response.status_code == 403


class TestUnauthenticated:
    def test_every_route_requires_a_session(self, client: TestClient) -> None:
        """No overrides in this test at all: the real `get_current_user`
        must reject before any use case -- or the database -- is reached."""
        response = client.get(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
