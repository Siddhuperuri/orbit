"""Documents, versioning, deduplication, constraints, and cascades."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from orbit.core.ids import new_uuid7
from orbit.domain.access import AccessContext
from orbit.domain.documents import DocumentEdit
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.models.entities import (
    Document,
    NewDocumentIds,
    ProcessingOutcome,
    ProcessingStatus,
    VersionContent,
)
from orbit.infrastructure.db.unit_of_work import UnitOfWork
from tests.integration.conftest import sha256_of, version_content

pytestmark = pytest.mark.integration


@pytest.fixture
async def document(uow: UnitOfWork, ctx: AccessContext) -> Document:
    return await uow.documents.create(
        ctx, title="Quarterly Report", folder_id=None, content=version_content("q1")
    )


class TestDocumentCreation:
    async def test_creates_a_document_with_its_first_version(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        created = await uow.documents.create(
            ctx, title="Contract", folder_id=None, content=version_content("contract")
        )
        assert created.title == "Contract"
        assert created.current_version is not None
        assert created.current_version.version_number == 1
        assert created.current_version.is_current is True
        assert created.current_version.status is ProcessingStatus.PENDING

    async def test_a_document_never_exists_without_a_version(
        self, uow: UnitOfWork, document: Document
    ) -> None:
        """Both rows are written in one transaction, so no reader can observe a
        document with no content."""
        count = await uow.session.scalar(
            text("SELECT count(*) FROM document_versions WHERE document_id = :d"),
            {"d": document.id},
        )
        assert count == 1

    async def test_records_the_creator(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        assert document.created_by_user_id == ctx.user_id

    async def test_blank_title_is_rejected(self, uow: UnitOfWork, ctx: AccessContext) -> None:
        """A `ConflictError`, not a raw `IntegrityError`: `create`'s own flush
        is wrapped so that a constraint violation surfacing here -- which the
        upload pipeline's concurrent-duplicate race can legitimately trigger
        -- always reaches the caller as a domain error, never leaks the
        driver's."""
        with pytest.raises(ConflictError):
            await uow.documents.create(
                ctx, title="   ", folder_id=None, content=version_content("blank")
            )

    async def test_zero_byte_upload_is_rejected_by_the_value_object(self) -> None:
        """Caught where the value is built, before any database round trip."""
        with pytest.raises(ValueError, match="byte_size must be positive"):
            VersionContent(
                storage_key="k",
                content_sha256=sha256_of("x"),
                byte_size=0,
                content_type="application/pdf",
                original_filename="x.pdf",
            )

    async def test_malformed_hash_is_rejected_by_the_value_object(self) -> None:
        with pytest.raises(ValueError, match="64-character hex"):
            VersionContent(
                storage_key="k",
                content_sha256="short",
                byte_size=1,
                content_type="application/pdf",
                original_filename="x.pdf",
            )


class TestDeduplication:
    async def test_identical_content_in_one_workspace_is_rejected(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        """Enforced by a partial unique index over *current* versions, so an
        identical re-upload cannot silently create a second document. Raised
        as `ConflictError`, not `IntegrityError`: this is precisely the
        constraint the upload pipeline's concurrent-duplicate race can
        legitimately hit, and `UploadDocument` catches `ConflictError`
        specifically to resolve it as a deduplication rather than an error."""
        with pytest.raises(ConflictError):
            await uow.documents.create(
                ctx, title="Duplicate", folder_id=None, content=version_content("q1")
            )

    async def test_lookup_finds_the_existing_document(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        found = await uow.documents.find_by_content_hash(ctx, sha256_of("q1"))
        assert found is not None
        assert found.id == document.id

    async def test_deduplication_does_not_cross_workspaces(
        self, uow: UnitOfWork, ctx: AccessContext, other_ctx: AccessContext, document: Document
    ) -> None:
        """Scoped to the workspace: a global namespace would leak the existence
        of another tenant's document through a hash collision check."""
        theirs = await uow.documents.create(
            other_ctx, title="Same bytes", folder_id=None, content=version_content("q1")
        )
        assert theirs.id != document.id

    async def test_superseded_content_can_be_uploaded_again(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        """The uniqueness index is partial on `is_current`, so reverting a
        document to earlier content is not mistaken for a duplicate."""
        await uow.documents.add_version(ctx, document.id, content=version_content("q2"))
        # "q1" is now a superseded version, so its hash is free again.
        restored = await uow.documents.add_version(ctx, document.id, content=version_content("q1"))
        assert restored.version_number == 3


class TestVersioning:
    async def test_adding_a_version_increments_the_number(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        second = await uow.documents.add_version(ctx, document.id, content=version_content("q2"))
        assert second.version_number == 2

    async def test_exactly_one_version_is_current(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        await uow.documents.add_version(ctx, document.id, content=version_content("q2"))
        await uow.documents.add_version(ctx, document.id, content=version_content("q3"))

        current = await uow.session.scalar(
            text("SELECT count(*) FROM document_versions WHERE document_id = :d AND is_current"),
            {"d": document.id},
        )
        assert current == 1

    async def test_the_newest_version_is_the_current_one(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        await uow.documents.add_version(ctx, document.id, content=version_content("q2"))
        reloaded = await uow.documents.get(ctx, document.id)
        assert reloaded is not None
        assert reloaded.current_version is not None
        assert reloaded.current_version.version_number == 2

    async def test_history_is_preserved(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        await uow.documents.add_version(ctx, document.id, content=version_content("q2"))
        versions = await uow.documents.list_versions(ctx, document.id)
        assert [v.version_number for v in versions] == [2, 1]
        assert [v.is_current for v in versions] == [True, False]

    async def test_versioning_preserves_document_identity(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        """Replacing the file must not disturb the title or the folder -- that is
        the entire reason identity and content are separate tables."""
        await uow.documents.add_version(ctx, document.id, content=version_content("q2"))
        reloaded = await uow.documents.get(ctx, document.id)
        assert reloaded is not None
        assert reloaded.title == "Quarterly Report"
        assert reloaded.id == document.id

    async def test_duplicate_version_numbers_are_impossible(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        await uow.flush()
        with pytest.raises(IntegrityError):
            await uow.session.execute(
                text(
                    "INSERT INTO document_versions "
                    "(id, document_id, workspace_id, version_number, is_current, storage_key, "
                    " content_sha256, byte_size, content_type, original_filename, status, "
                    " chunk_count, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :d, :w, 1, false, 'k', :h, 10, 'application/pdf',"
                    " 'x.pdf', 'pending', 0, now(), now())"
                ),
                {"d": document.id, "w": ctx.workspace_id, "h": sha256_of("dupe")},
            )

    async def test_adding_a_version_to_a_missing_document_is_not_found(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        with pytest.raises(NotFoundError):
            await uow.documents.add_version(ctx, uuid.uuid4(), content=version_content("ghost"))


class TestProcessingLifecycle:
    async def test_moves_to_ready_with_chunks(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        assert document.current_version is not None
        updated = await uow.documents.set_version_status(
            ctx, document.current_version.id, outcome=ProcessingOutcome.ready(chunk_count=12)
        )
        assert updated.status is ProcessingStatus.READY
        assert updated.chunk_count == 12
        assert updated.processed_at is not None
        assert updated.is_searchable

    async def test_a_ready_version_must_have_chunks(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        """A READY version with no chunks answers no questions while looking
        healthy in a list. ADR-0012 makes that case FAILED; the database
        enforces it."""
        assert document.current_version is not None
        # Raw SQL, deliberately: `ProcessingOutcome` refuses to *construct* a
        # chunkless READY outcome, so going through the repository would test
        # the entity's guard rather than the database's.
        with pytest.raises(IntegrityError):
            await uow.session.execute(
                text(
                    "UPDATE document_versions SET status = 'ready', chunk_count = 0,"
                    " processed_at = now() WHERE id = :id"
                ),
                {"id": document.current_version.id},
            )

    async def test_ready_with_no_chunks_is_rejected_before_the_database(self) -> None:
        with pytest.raises(ValueError, match="at least one chunk"):
            ProcessingOutcome(status=ProcessingStatus.READY, chunk_count=0)

    async def test_failure_must_carry_a_code(self) -> None:
        with pytest.raises(ValueError, match="failure_code"):
            ProcessingOutcome(status=ProcessingStatus.FAILED)

    async def test_records_a_failure_with_its_reason(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        assert document.current_version is not None
        failed = await uow.documents.set_version_status(
            ctx,
            document.current_version.id,
            outcome=ProcessingOutcome.failed(
                code="NO_EXTRACTABLE_TEXT",
                reason="This looks like a scanned document; OCR is not yet supported.",
            ),
        )
        assert failed.status is ProcessingStatus.FAILED
        assert failed.failure_code == "NO_EXTRACTABLE_TEXT"
        assert not failed.is_searchable

    async def test_a_transient_failure_returns_the_version_to_pending(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        """`FAILED` means the document is the problem; `PENDING` means we are.
        A provider outage must not tell the user their file is broken."""
        assert document.current_version is not None
        version_id = document.current_version.id

        await uow.documents.set_version_status(
            ctx, version_id, outcome=ProcessingOutcome(status=ProcessingStatus.PROCESSING)
        )
        back = await uow.documents.set_version_status(
            ctx, version_id, outcome=ProcessingOutcome(status=ProcessingStatus.PENDING)
        )
        assert back.status is ProcessingStatus.PENDING
        assert back.processed_at is None, "a non-terminal state must clear processed_at"


class TestDeletion:
    async def test_soft_delete_hides_the_document(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        await uow.documents.soft_delete(ctx, document.id)
        assert await uow.documents.get(ctx, document.id) is None

    async def test_soft_delete_retains_the_row_for_recovery(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        await uow.documents.soft_delete(ctx, document.id)
        deleted_at = await uow.session.scalar(
            text("SELECT deleted_at FROM documents WHERE id = :i"), {"i": document.id}
        )
        assert deleted_at is not None

    async def test_deleting_twice_is_not_found(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        await uow.documents.soft_delete(ctx, document.id)
        with pytest.raises(NotFoundError):
            await uow.documents.soft_delete(ctx, document.id)

    async def test_purging_a_document_cascades_to_its_versions(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        """Versions are derived content: worthless without their document."""
        await uow.documents.add_version(ctx, document.id, content=version_content("q2"))
        await uow.session.execute(text("DELETE FROM documents WHERE id = :i"), {"i": document.id})
        remaining = await uow.session.scalar(
            text("SELECT count(*) FROM document_versions WHERE document_id = :d"),
            {"d": document.id},
        )
        assert remaining == 0

    async def test_purging_a_workspace_cascades_to_its_documents(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        await uow.session.execute(
            text("DELETE FROM workspaces WHERE id = :i"), {"i": ctx.workspace_id}
        )
        remaining = await uow.session.scalar(
            text("SELECT count(*) FROM documents WHERE workspace_id = :w"),
            {"w": ctx.workspace_id},
        )
        assert remaining == 0


class TestRenameAndConcurrency:
    async def test_renames_a_document(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        renamed = await uow.documents.update(
            ctx,
            document.id,
            edit=DocumentEdit(title="Annual Report"),
            expected_version=document.version,
        )
        assert renamed.title == "Annual Report"
        assert renamed.version == document.version + 1

    async def test_a_stale_version_is_a_conflict_not_a_silent_overwrite(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        """Without optimistic concurrency this would be last-writer-wins, and the
        first user's rename would vanish with no error anywhere."""
        await uow.documents.update(
            ctx, document.id, edit=DocumentEdit(title="First"), expected_version=document.version
        )
        with pytest.raises(ConflictError, match="modified by someone else"):
            await uow.documents.update(
                ctx,
                document.id,
                edit=DocumentEdit(title="Second"),
                expected_version=document.version,
            )

    async def test_renaming_a_missing_document_is_not_found(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        with pytest.raises(NotFoundError):
            await uow.documents.update(
                ctx, uuid.uuid4(), edit=DocumentEdit(title="X"), expected_version=1
            )


class TestUploadPipelineSupport:
    """The two repository capabilities the upload pipeline (ADR-0011) needs
    that no other caller does: pre-generated ids, and a reverse lookup from a
    storage key back to whether any row still references it."""

    async def test_create_uses_the_pre_generated_ids_not_its_own(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        """The storage key must be known before the row is written -- which
        is only possible if the id in the key matches the id the row actually
        gets, not one the repository quietly generated instead."""
        ids = NewDocumentIds(document_id=new_uuid7(), version_id=new_uuid7())
        created = await uow.documents.create(
            ctx, title="Pre-generated", folder_id=None, content=version_content("pregen"), ids=ids
        )
        assert created.id == ids.document_id
        assert created.current_version is not None
        assert created.current_version.id == ids.version_id

    async def test_create_without_ids_still_generates_its_own(
        self, uow: UnitOfWork, ctx: AccessContext
    ) -> None:
        """Every caller except the upload pipeline: unchanged from before
        `ids` existed."""
        created = await uow.documents.create(
            ctx, title="Auto ids", folder_id=None, content=version_content("autoids")
        )
        assert created.id is not None
        assert created.current_version is not None

    async def test_exists_by_storage_key_finds_a_referenced_key(
        self, uow: UnitOfWork, ctx: AccessContext, document: Document
    ) -> None:
        assert document.current_version is not None
        found = await uow.documents.exists_by_storage_key(document.current_version.storage_key)
        assert found is True

    async def test_exists_by_storage_key_is_false_for_an_orphaned_key(
        self, uow: UnitOfWork
    ) -> None:
        """The exact question the orphan sweep asks for every key it lists in
        storage: does anything still reference this."""
        never_written = f"workspaces/nowhere/documents/{new_uuid7()}/{new_uuid7()}"
        found = await uow.documents.exists_by_storage_key(never_written)
        assert found is False

    async def test_exists_by_storage_key_is_not_workspace_scoped(
        self, uow: UnitOfWork, other_ctx: AccessContext, document: Document
    ) -> None:
        """Deliberately: the sweep walks storage key-by-key, and the key
        already names its own workspace (`core/storage_keys.py`) -- scoping
        the lookup again would just repeat what the key already says, for a
        method whose only caller already knows which workspace it is asking
        about."""
        assert document.current_version is not None
        # Asked via a *different* tenant's context; still found, because the
        # method answers a global question ("does any row reference this
        # key"), not a tenant-scoped one.
        found = await uow.documents.exists_by_storage_key(document.current_version.storage_key)
        assert found is True
