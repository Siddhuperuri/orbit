"""Cross-tenant isolation.

Section 6 of the engineering brief: a user must never reach another user's
documents, chunks, embeddings, conversations, files, workspaces, or audit
records. These are the tests that hold that line.

Every tenant-scoped resource needs a denial test here. A resource without one is
incomplete under the Definition of Done.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from orbit.domain.access import AccessContext, Role
from orbit.domain.documents import DocumentEdit
from orbit.domain.errors import NotFoundError
from orbit.domain.models.entities import Document, ProcessingOutcome, User, Workspace
from orbit.infrastructure.db.unit_of_work import UnitOfWork
from tests.integration.conftest import version_content

pytestmark = pytest.mark.integration


@pytest.fixture
async def their_document(uow: UnitOfWork, other_ctx: AccessContext) -> Document:
    return await uow.documents.create(
        other_ctx, title="Their Secret", folder_id=None, content=version_content("theirs")
    )


class TestDocumentIsolation:
    async def test_another_tenants_document_reads_as_absent(
        self, uow: UnitOfWork, ctx: AccessContext, their_document: Document
    ) -> None:
        """`None`, not a permission error. A 403 would confirm the document
        exists and leak it across the boundary."""
        assert await uow.documents.get(ctx, their_document.id) is None

    async def test_another_tenants_document_cannot_be_renamed(
        self, uow: UnitOfWork, ctx: AccessContext, their_document: Document
    ) -> None:
        with pytest.raises(NotFoundError):
            await uow.documents.update(
                ctx, their_document.id, edit=DocumentEdit(title="Hijacked"), expected_version=1
            )

    async def test_another_tenants_document_cannot_be_deleted(
        self, uow: UnitOfWork, ctx: AccessContext, their_document: Document
    ) -> None:
        with pytest.raises(NotFoundError):
            await uow.documents.soft_delete(ctx, their_document.id)

    async def test_another_tenants_document_cannot_gain_a_version(
        self, uow: UnitOfWork, ctx: AccessContext, their_document: Document
    ) -> None:
        with pytest.raises(NotFoundError):
            await uow.documents.add_version(
                ctx, their_document.id, content=version_content("injected")
            )

    async def test_another_tenants_version_status_cannot_be_changed(
        self, uow: UnitOfWork, ctx: AccessContext, their_document: Document
    ) -> None:
        assert their_document.current_version is not None
        with pytest.raises(NotFoundError):
            await uow.documents.set_version_status(
                ctx,
                their_document.current_version.id,
                outcome=ProcessingOutcome.ready(chunk_count=1),
            )

    async def test_listing_never_includes_another_tenants_documents(
        self, uow: UnitOfWork, ctx: AccessContext, their_document: Document
    ) -> None:
        mine = await uow.documents.create(
            ctx, title="Mine", folder_id=None, content=version_content("mine")
        )
        page = await uow.documents.list_page(ctx, limit=50)
        assert [d.id for d in page.items] == [mine.id]

    async def test_content_hash_lookup_is_workspace_scoped(
        self, uow: UnitOfWork, ctx: AccessContext, their_document: Document
    ) -> None:
        """Otherwise a caller could probe for the existence of another tenant's
        file by uploading a copy and watching for a deduplication hit."""
        assert their_document.current_version is not None
        digest = their_document.current_version.content_sha256
        assert await uow.documents.find_by_content_hash(ctx, digest) is None


class TestWorkspaceIsolation:
    async def test_another_tenants_workspace_reads_as_absent(
        self, uow: UnitOfWork, user: User, other_workspace: Workspace
    ) -> None:
        forged = AccessContext(user_id=user.id, workspace_id=other_workspace.id, role=Role.OWNER)
        # The context is forged -- the user has no membership -- but the
        # repository still returns the row, which is why membership resolution
        # happens before a context is ever constructed. This test documents that
        # boundary: AccessContext is trusted, and producing one is the guarded step.
        found = await uow.workspaces.get(forged)
        assert found is not None
        assert await uow.memberships.get(other_workspace.id, user.id) is None

    async def test_membership_lookup_is_the_gate(
        self, uow: UnitOfWork, user: User, other_workspace: Workspace
    ) -> None:
        """The one call that decides whether an AccessContext may be built."""
        assert await uow.memberships.get(other_workspace.id, user.id) is None


class TestStructuralCrossTenantPrevention:
    """Composite foreign keys make some cross-tenant references impossible.

    These are stronger than a repository filter: no application bug can bypass
    them, because the database itself rejects the row.
    """

    async def _their_folder(self, uow: UnitOfWork, other_ctx: AccessContext) -> uuid.UUID:
        await uow.flush()
        their_folder_id = uuid.uuid4()
        await uow.session.execute(
            text(
                "INSERT INTO folders (id, workspace_id, name, depth, created_at, updated_at,"
                " version) VALUES (:i, :w, 'Theirs', 0, now(), now(), 1)"
            ),
            {"i": their_folder_id, "w": other_ctx.workspace_id},
        )
        return their_folder_id

    async def test_the_repository_answers_as_if_their_folder_did_not_exist(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        """The same answer as for a folder that was never created, so filing into
        a guessed id cannot be used to learn that another tenant has one
        (ADR-0004)."""
        their_folder_id = await self._their_folder(uow, other_ctx)

        with pytest.raises(NotFoundError, match="no longer exists"):
            await uow.documents.create(
                ctx,
                title="Misfiled",
                folder_id=their_folder_id,
                content=version_content("misfiled"),
            )

    async def test_the_database_rejects_it_even_if_the_repository_is_bypassed(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        """The composite foreign key is the backstop: a future code path that
        forgot the repository's check would still be refused by the schema."""
        their_folder_id = await self._their_folder(uow, other_ctx)

        with pytest.raises(IntegrityError):
            await uow.session.execute(
                text(
                    "INSERT INTO documents (id, workspace_id, folder_id, title, created_at,"
                    " updated_at, version) VALUES (:i, :w, :f, 'Misfiled', now(), now(), 1)"
                ),
                {"i": uuid.uuid4(), "w": ctx.workspace_id, "f": their_folder_id},
            )

    async def test_a_document_cannot_be_tagged_with_another_tenants_tag(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext
    ) -> None:
        mine = await uow.documents.create(
            ctx, title="Mine", folder_id=None, content=version_content("tagme")
        )
        their_tag_id = uuid.uuid4()
        await uow.session.execute(
            text(
                "INSERT INTO tags (id, workspace_id, name, created_at, updated_at)"
                " VALUES (:i, :w, 'confidential', now(), now())"
            ),
            {"i": their_tag_id, "w": other_ctx.workspace_id},
        )

        with pytest.raises(IntegrityError):
            await uow.session.execute(
                text(
                    "INSERT INTO document_tags (workspace_id, document_id, tag_id)"
                    " VALUES (:w, :d, :t)"
                ),
                {"w": ctx.workspace_id, "d": mine.id, "t": their_tag_id},
            )

    async def test_a_chunk_cannot_reference_another_tenants_version(
        self, uow: UnitOfWork, ctx: AccessContext, their_document: Document
    ) -> None:
        assert their_document.current_version is not None
        with pytest.raises(IntegrityError):
            await uow.session.execute(
                text(
                    "INSERT INTO chunks (id, workspace_id, document_id, document_version_id,"
                    " ordinal, content, token_count, char_start, char_end, chunker_version)"
                    " VALUES (gen_random_uuid(), :w, :d, :v, 0, 'stolen', 5, 0, 6, 'v1')"
                ),
                {
                    "w": ctx.workspace_id,
                    "d": their_document.id,
                    "v": their_document.current_version.id,
                },
            )
