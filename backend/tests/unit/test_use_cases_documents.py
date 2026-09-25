"""Document use cases and their authorization boundaries."""

from __future__ import annotations

import hashlib
import uuid

import pytest

from orbit.application.documents.delete_document import DeleteDocument
from orbit.application.documents.get_document import GetDocument
from orbit.application.documents.list_documents import ListDocuments
from orbit.application.documents.update_document import UpdateDocument
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.domain.access import AccessContext, Role
from orbit.domain.documents import DocumentEdit
from orbit.domain.errors import NotFoundError, PermissionDeniedError
from orbit.domain.models.entities import VersionContent
from tests.unit.fakes.in_memory_unit_of_work import FakeUnitOfWorkFactory


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
async def workspace_id(uow_factory: FakeUnitOfWorkFactory, owner_id: uuid.UUID) -> uuid.UUID:
    workspace = await CreateWorkspace(uow_factory).execute(
        name="Research", created_by_user_id=owner_id
    )
    return workspace.id


def _ctx(workspace_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> AccessContext:
    return AccessContext(user_id=user_id, workspace_id=workspace_id, role=role)


def _content(marker: str) -> VersionContent:
    digest = hashlib.sha256(marker.encode()).hexdigest()
    return VersionContent(
        storage_key=f"workspaces/test/documents/test/{digest}",
        content_sha256=digest,
        byte_size=1024,
        content_type="application/pdf",
        original_filename=f"{marker}.pdf",
    )


@pytest.fixture
async def document_id(
    uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
) -> uuid.UUID:
    document = await uow_factory().documents.create(
        _ctx(workspace_id, owner_id, Role.OWNER),
        title="Quarterly Report",
        folder_id=None,
        content=_content("q1"),
    )
    return document.id


class TestListDocumentsAuthorizationBoundary:
    async def test_every_role_can_list(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        """`DOCUMENT_READ` is held by every role (domain/access.py) -- this
        pins that as a fact rather than an assumption."""
        list_docs = ListDocuments(uow_factory)
        for role in Role:
            page = await list_docs.execute(_ctx(workspace_id, owner_id, role), limit=25)
            assert page.items == ()  # no documents created yet in this test


class TestGetDocumentAuthorizationBoundary:
    async def test_every_role_can_read_a_document(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        get = GetDocument(uow_factory)
        for role in Role:
            document = await get.execute(_ctx(workspace_id, owner_id, role), document_id)
            assert document.id == document_id

    async def test_a_missing_document_is_not_found(
        self, uow_factory: FakeUnitOfWorkFactory, workspace_id: uuid.UUID, owner_id: uuid.UUID
    ) -> None:
        get = GetDocument(uow_factory)
        with pytest.raises(NotFoundError):
            await get.execute(_ctx(workspace_id, owner_id, Role.OWNER), uuid.uuid4())


class TestUpdateDocumentAuthorizationBoundary:
    @pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.MEMBER])
    async def test_owner_admin_and_member_can_rename(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
        role: Role,
    ) -> None:
        rename = UpdateDocument(uow_factory)
        renamed = await rename.execute(
            _ctx(workspace_id, owner_id, role),
            document_id,
            edit=DocumentEdit(title="Annual Report"),
            expected_version=1,
        )
        assert renamed.title == "Annual Report"

    async def test_viewer_cannot_rename(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        """The line drawn by `DOCUMENT_UPDATE`: a viewer can read and ask
        questions, but cannot touch the document's identity."""
        rename = UpdateDocument(uow_factory)
        with pytest.raises(PermissionDeniedError):
            await rename.execute(
                _ctx(workspace_id, owner_id, Role.VIEWER),
                document_id,
                edit=DocumentEdit(title="Hijacked"),
                expected_version=1,
            )

    async def test_a_denied_rename_leaves_the_title_unchanged(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        rename = UpdateDocument(uow_factory)
        with pytest.raises(PermissionDeniedError):
            await rename.execute(
                _ctx(workspace_id, owner_id, Role.VIEWER),
                document_id,
                edit=DocumentEdit(title="Hijacked"),
                expected_version=1,
            )
        assert uow_factory.state.documents[document_id].title == "Quarterly Report"


class TestDeleteDocumentAuthorizationBoundary:
    @pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.MEMBER])
    async def test_owner_admin_and_member_can_delete(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
        role: Role,
    ) -> None:
        delete = DeleteDocument(uow_factory)
        await delete.execute(_ctx(workspace_id, owner_id, role), document_id)
        assert uow_factory.state.documents[document_id].is_deleted

    async def test_viewer_cannot_delete(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        delete = DeleteDocument(uow_factory)
        with pytest.raises(PermissionDeniedError):
            await delete.execute(_ctx(workspace_id, owner_id, Role.VIEWER), document_id)
        assert not uow_factory.state.documents[document_id].is_deleted


class TestCrossTenantDocumentIsolation:
    @pytest.fixture
    async def other_user_id(self, uow_factory: FakeUnitOfWorkFactory) -> uuid.UUID:
        user = await uow_factory().users.create(
            email="other@example.com", password_hash="h", full_name="Other"
        )
        return user.id

    @pytest.fixture
    async def other_workspace_id(
        self, uow_factory: FakeUnitOfWorkFactory, other_user_id: uuid.UUID
    ) -> uuid.UUID:
        workspace = await CreateWorkspace(uow_factory).execute(
            name="Someone Else's", created_by_user_id=other_user_id
        )
        return workspace.id

    async def test_a_document_created_in_one_workspace_is_absent_from_another(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        other_user_id: uuid.UUID,
        other_workspace_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        """`document_id` belongs to the `workspace_id` fixture's workspace.
        Reading it through a context scoped to a *different* workspace must
        return "not found", never the document -- this is the fake's
        analogue of the composite-foreign-key isolation the real schema
        enforces structurally (docs/database/schema.md)."""
        get = GetDocument(uow_factory)
        with pytest.raises(NotFoundError):
            await get.execute(_ctx(other_workspace_id, other_user_id, Role.OWNER), document_id)

    async def test_listing_never_crosses_the_workspace_boundary(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        other_user_id: uuid.UUID,
        other_workspace_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        list_docs = ListDocuments(uow_factory)
        page = await list_docs.execute(
            _ctx(other_workspace_id, other_user_id, Role.OWNER), limit=25
        )
        assert page.items == ()
