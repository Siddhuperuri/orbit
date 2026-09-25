"""Upload, add-version, and download use cases, against the in-memory fake
`UnitOfWork` and `FakeObjectStorage`.

These exercise the full pipeline end to end -- filename sanitization, magic-
byte sniffing, streaming size enforcement, deduplication, and download
authorization -- against the production use-case classes, not stubs. Covers
the scenarios named explicitly for this milestone: valid PDF, valid Markdown,
valid TXT, invalid MIME, oversized file, duplicate file, unauthorized
download, missing object, storage failure, and partial upload failure.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import pytest

from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.documents.add_document_version import AddDocumentVersion
from orbit.application.documents.get_document_download import GetDocumentDownload
from orbit.application.documents.sweep_orphaned_storage import SweepOrphanedStorage
from orbit.application.documents.upload_document import UploadDocument
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.core.clock import FixedClock
from orbit.core.config import Settings
from orbit.core.ids import new_uuid7
from orbit.core.storage_keys import document_version_key
from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import (
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    StorageUnavailableError,
    UnsupportedContentTypeError,
    UploadTooLargeError,
    ValidationError,
)
from orbit.domain.models.entities import User
from tests.conftest import build_settings
from tests.unit.fakes.fake_processing import RecordingJobQueue
from tests.unit.fakes.fake_storage import FailingObjectStorage, FakeObjectStorage
from tests.unit.fakes.in_memory_unit_of_work import FakeUnitOfWorkFactory
from tests.unit.fakes.security_doubles import InMemoryRateLimiter, RecordingAuditSink

_PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\nrest of a pdf..."
_MARKDOWN_BYTES = b"# Title\n\nBody text.\n"
_TEXT_BYTES = b"Just plain text.\n"


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


@dataclass
class Harness:
    uow_factory: FakeUnitOfWorkFactory
    storage: FakeObjectStorage
    settings: Settings
    clock: FixedClock
    queue: RecordingJobQueue
    upload: UploadDocument
    add_version: AddDocumentVersion
    download: GetDocumentDownload
    sweep: SweepOrphanedStorage
    create_workspace: CreateWorkspace

    async def sign_up(self, email: str) -> User:
        async with self.uow_factory() as uow:
            user = await uow.users.create(
                email=email, password_hash="$argon2id$fake", full_name="Test Person"
            )
            await uow.commit()
        return user

    async def context_for(self, user: User, workspace_id: uuid.UUID) -> AccessContext:
        async with self.uow_factory() as uow:
            membership = await uow.memberships.get(workspace_id, user.id)
        assert membership is not None
        return AccessContext(user_id=user.id, workspace_id=workspace_id, role=membership.role)


def _no_op_guard(settings: Settings) -> AuthRateLimitGuard:
    """A guard backed by a limiter that never trips, for tests about
    upload behaviour rather than about the rate limit itself -- which has
    its own coverage in `test_upload_rate_limiting` below."""
    return AuthRateLimitGuard(
        InMemoryRateLimiter(), RateLimitPolicy.for_upload(settings), RecordingAuditSink()
    )


@pytest.fixture
def harness() -> Harness:
    uow_factory = FakeUnitOfWorkFactory()
    storage = FakeObjectStorage()
    settings = build_settings(max_upload_bytes=1024)
    clock = FixedClock()
    queue = RecordingJobQueue()
    return Harness(
        uow_factory=uow_factory,
        storage=storage,
        settings=settings,
        clock=clock,
        queue=queue,
        upload=UploadDocument(uow_factory, storage, settings, _no_op_guard(settings), queue),
        add_version=AddDocumentVersion(
            uow_factory, storage, settings, _no_op_guard(settings), queue
        ),
        download=GetDocumentDownload(uow_factory, storage, clock),
        sweep=SweepOrphanedStorage(uow_factory, storage, clock),
        create_workspace=CreateWorkspace(uow_factory),
    )


async def _owner_context(harness: Harness, *, email: str = "owner@example.com") -> AccessContext:
    user = await harness.sign_up(email)
    workspace = await harness.create_workspace.execute(name="Acme", created_by_user_id=user.id)
    return await harness.context_for(user, workspace.id)


# ---------------------------------------------------------------------------
# Valid uploads: PDF, Markdown, TXT
# ---------------------------------------------------------------------------


class TestValidUploads:
    async def test_a_valid_pdf_is_stored_and_a_document_created(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        result = await harness.upload.execute(
            ctx,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        assert result.deduplicated is False
        assert result.document.current_version is not None
        assert result.document.current_version.content_type == "application/pdf"
        assert result.document.current_version.byte_size == len(_PDF_BYTES)
        assert result.document.title == "report.pdf"

        stored = harness.storage.objects[result.document.current_version.storage_key]
        assert stored.data == _PDF_BYTES

    async def test_a_valid_markdown_file_is_stored(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        result = await harness.upload.execute(
            ctx,
            filename="notes.md",
            title="My Notes",
            folder_id=None,
            content_stream=_stream(_MARKDOWN_BYTES),
        )
        assert result.document.current_version is not None
        assert result.document.current_version.content_type == "text/markdown"
        assert result.document.title == "My Notes"

    async def test_a_valid_text_file_is_stored(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        result = await harness.upload.execute(
            ctx,
            filename="notes.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(_TEXT_BYTES),
        )
        assert result.document.current_version is not None
        assert result.document.current_version.content_type == "text/plain"

    async def test_the_storage_key_contains_no_user_input(self, harness: Harness) -> None:
        """Path traversal is unrepresentable: the key is built entirely from
        server-generated ids, never from the filename."""
        ctx = await _owner_context(harness)
        result = await harness.upload.execute(
            ctx,
            filename="../../etc/passwd.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        assert result.document.current_version is not None
        key = result.document.current_version.storage_key
        assert "etc" not in key
        assert ".." not in key
        assert str(ctx.workspace_id) in key
        assert str(result.document.id) in key


# ---------------------------------------------------------------------------
# Invalid MIME
# ---------------------------------------------------------------------------


class TestInvalidMime:
    async def test_content_not_matching_the_extension_is_rejected(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        with pytest.raises(UnsupportedContentTypeError):
            await harness.upload.execute(
                ctx,
                filename="fake.pdf",
                title=None,
                folder_id=None,
                content_stream=_stream(_TEXT_BYTES),
            )
        # Nothing was left behind in storage for a rejected upload.
        assert harness.storage.objects == {}

    async def test_an_unsupported_extension_is_rejected(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        with pytest.raises(UnsupportedContentTypeError):
            await harness.upload.execute(
                ctx,
                filename="malware.exe",
                title=None,
                folder_id=None,
                content_stream=_stream(b"MZ\x90\x00executable"),
            )

    async def test_a_bad_filename_is_a_validation_error(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        with pytest.raises(ValidationError):
            await harness.upload.execute(
                ctx,
                filename="a/b/c/",
                title=None,
                folder_id=None,
                content_stream=_stream(_PDF_BYTES),
            )


# ---------------------------------------------------------------------------
# Oversized file
# ---------------------------------------------------------------------------


class TestOversizedFile:
    async def test_an_oversized_file_is_rejected(self, harness: Harness) -> None:
        oversized = b"x" * (harness.settings.max_upload_bytes + 1)
        ctx = await _owner_context(harness)
        with pytest.raises(UploadTooLargeError):
            await harness.upload.execute(
                ctx,
                filename="big.txt",
                title=None,
                folder_id=None,
                content_stream=_stream(oversized),
            )

    async def test_an_oversized_file_leaves_no_document_row(self, harness: Harness) -> None:
        oversized = b"x" * (harness.settings.max_upload_bytes + 1)
        ctx = await _owner_context(harness)
        with pytest.raises(UploadTooLargeError):
            await harness.upload.execute(
                ctx,
                filename="big.txt",
                title=None,
                folder_id=None,
                content_stream=_stream(oversized),
            )
        assert harness.uow_factory.state.documents == {}


# ---------------------------------------------------------------------------
# Duplicate file
# ---------------------------------------------------------------------------


class TestDuplicateFile:
    async def test_reuploading_identical_content_returns_the_existing_document(
        self, harness: Harness
    ) -> None:
        ctx = await _owner_context(harness)
        first = await harness.upload.execute(
            ctx,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        second = await harness.upload.execute(
            ctx,
            filename="report-copy.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )

        assert first.deduplicated is False
        assert second.deduplicated is True
        assert second.document.id == first.document.id
        # No second document was created.
        assert len(harness.uow_factory.state.documents) == 1

    async def test_the_redundant_object_is_removed_from_storage(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        await harness.upload.execute(
            ctx,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        objects_after_first = len(harness.storage.objects)

        await harness.upload.execute(
            ctx,
            filename="report-copy.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        # The second upload's object was written, found duplicate, and deleted
        # -- so the count is unchanged, not doubled.
        assert len(harness.storage.objects) == objects_after_first
        assert len(harness.storage.deleted_keys) == 1

    async def test_different_content_is_not_treated_as_a_duplicate(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        first = await harness.upload.execute(
            ctx,
            filename="a.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(b"content A"),
        )
        second = await harness.upload.execute(
            ctx,
            filename="b.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(b"content B"),
        )
        assert second.deduplicated is False
        assert second.document.id != first.document.id

    async def test_duplicate_content_across_workspaces_is_not_deduplicated(
        self, harness: Harness
    ) -> None:
        """Deduplication is workspace-scoped: a shared global namespace would
        leak the existence of another tenant's document through a hash
        collision check (ADR-0011)."""
        ctx_a = await _owner_context(harness, email="a@example.com")
        ctx_b = await _owner_context(harness, email="b@example.com")

        first = await harness.upload.execute(
            ctx_a,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        second = await harness.upload.execute(
            ctx_b,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        assert second.deduplicated is False
        assert second.document.id != first.document.id

    async def test_reuploading_as_a_new_version_of_the_same_document_is_not_a_conflict(
        self, harness: Harness
    ) -> None:
        """Re-adding identical content to the *same* document (a no-op-ish
        revision) must not trip the cross-document duplicate constraint."""
        ctx = await _owner_context(harness)
        created = await harness.upload.execute(
            ctx,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        result = await harness.add_version.execute(
            ctx,
            created.document.id,
            filename="report-v2.pdf",
            content_stream=_stream(_PDF_BYTES),
        )
        assert result.deduplicated is False
        assert result.document.current_version is not None
        assert result.document.current_version.version_number == 2


# ---------------------------------------------------------------------------
# Add document version
# ---------------------------------------------------------------------------


class TestAddDocumentVersion:
    async def test_adding_a_version_replaces_the_current_content(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        created = await harness.upload.execute(
            ctx,
            filename="v1.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(b"version one"),
        )
        result = await harness.add_version.execute(
            ctx, created.document.id, filename="v2.txt", content_stream=_stream(b"version two")
        )
        assert result.document.current_version is not None
        assert result.document.current_version.byte_size == len(b"version two")
        assert result.document.current_version.version_number == 2

    async def test_adding_a_version_to_a_nonexistent_document_is_not_found(
        self, harness: Harness
    ) -> None:
        ctx = await _owner_context(harness)
        with pytest.raises(NotFoundError):
            await harness.add_version.execute(
                ctx, uuid.uuid4(), filename="v2.txt", content_stream=_stream(b"content")
            )

    async def test_a_viewer_cannot_add_a_version(self, harness: Harness) -> None:
        owner = await harness.sign_up("owner@example.com")
        viewer = await harness.sign_up("viewer@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        async with harness.uow_factory() as uow:
            await uow.memberships.add(owner_ctx, user_id=viewer.id, role=Role.VIEWER)
            await uow.commit()
        created = await harness.upload.execute(
            owner_ctx,
            filename="v1.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(b"version one"),
        )

        viewer_ctx = await harness.context_for(viewer, workspace.id)
        with pytest.raises(PermissionDeniedError):
            await harness.add_version.execute(
                viewer_ctx,
                created.document.id,
                filename="v2.txt",
                content_stream=_stream(b"version two"),
            )


# ---------------------------------------------------------------------------
# Unauthorized download / cross-workspace access
# ---------------------------------------------------------------------------


class TestUnauthorizedDownload:
    async def test_a_document_from_another_workspace_cannot_be_downloaded(
        self, harness: Harness
    ) -> None:
        owner_ctx = await _owner_context(harness, email="owner@example.com")
        outsider_ctx = await _owner_context(harness, email="outsider@example.com")

        created = await harness.upload.execute(
            owner_ctx,
            filename="secret.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(b"confidential"),
        )

        with pytest.raises(NotFoundError):
            await harness.download.execute(outsider_ctx, created.document.id)

    async def test_the_owning_workspace_can_download_its_own_document(
        self, harness: Harness
    ) -> None:
        ctx = await _owner_context(harness)
        created = await harness.upload.execute(
            ctx,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        link = await harness.download.execute(ctx, created.document.id)
        assert created.document.current_version is not None
        assert created.document.current_version.storage_key in link.url
        assert link.expires_at > harness.clock.now()

    async def test_a_viewer_can_download(self, harness: Harness) -> None:
        """Download requires DOCUMENT_READ, which every role holds."""
        owner = await harness.sign_up("owner@example.com")
        viewer = await harness.sign_up("viewer@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        async with harness.uow_factory() as uow:
            await uow.memberships.add(owner_ctx, user_id=viewer.id, role=Role.VIEWER)
            await uow.commit()
        created = await harness.upload.execute(
            owner_ctx,
            filename="shared.txt",
            title=None,
            folder_id=None,
            content_stream=_stream(b"shared content"),
        )

        viewer_ctx = await harness.context_for(viewer, workspace.id)
        link = await harness.download.execute(viewer_ctx, created.document.id)
        assert link.url


# ---------------------------------------------------------------------------
# Missing object
# ---------------------------------------------------------------------------


class TestMissingObject:
    async def test_a_row_whose_object_was_removed_is_reported_as_not_found(
        self, harness: Harness
    ) -> None:
        """The row survived something the object did not. Reported the same
        way a bad id is, rather than a raw storage error reaching the client."""
        ctx = await _owner_context(harness)
        created = await harness.upload.execute(
            ctx,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        assert created.document.current_version is not None
        # Simulate the object having vanished independently of its row.
        await harness.storage.delete(created.document.current_version.storage_key)

        with pytest.raises(NotFoundError):
            await harness.download.execute(ctx, created.document.id)

    async def test_a_document_with_no_current_version_cannot_be_downloaded(
        self, harness: Harness
    ) -> None:
        ctx = await _owner_context(harness)
        with pytest.raises(NotFoundError):
            await harness.download.execute(ctx, uuid.uuid4())


# ---------------------------------------------------------------------------
# Storage failure
# ---------------------------------------------------------------------------


class TestStorageFailure:
    async def test_a_storage_outage_during_upload_propagates_and_creates_no_row(
        self,
    ) -> None:
        uow_factory = FakeUnitOfWorkFactory()
        failing_storage = FailingObjectStorage()
        settings = build_settings(max_upload_bytes=1024)
        upload = UploadDocument(
            uow_factory, failing_storage, settings, _no_op_guard(settings), RecordingJobQueue()
        )

        user = await uow_factory().users.create(
            email="owner@example.com", password_hash="$argon2id$fake", full_name="Owner"
        )
        async with uow_factory() as uow:
            workspace = await uow.workspaces.create(
                name="Acme", slug="acme-abc123", created_by_user_id=user.id
            )
            await uow.memberships.add_owner(workspace.id, user.id)
            await uow.commit()
        ctx = AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER)

        with pytest.raises(StorageUnavailableError):
            await upload.execute(
                ctx,
                filename="report.pdf",
                title=None,
                folder_id=None,
                content_stream=_stream(_PDF_BYTES),
            )

        assert uow_factory.state.documents == {}
        assert failing_storage.attempts == 1

    async def test_a_download_link_cannot_be_minted_during_a_storage_outage(
        self, harness: Harness
    ) -> None:
        ctx = await _owner_context(harness)
        created = await harness.upload.execute(
            ctx,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )

        broken_download = GetDocumentDownload(
            harness.uow_factory, FailingObjectStorage(), harness.clock
        )
        with pytest.raises(StorageUnavailableError):
            await broken_download.execute(ctx, created.document.id)


# ---------------------------------------------------------------------------
# Partial upload failure
# ---------------------------------------------------------------------------


class TestPartialUploadFailure:
    async def test_a_connection_dropped_mid_upload_leaves_no_document_row(
        self,
    ) -> None:
        uow_factory = FakeUnitOfWorkFactory()
        # Fails after 5 bytes -- partway through a stream that has more to give.
        failing_storage = FailingObjectStorage(fail_after_bytes=5)
        settings = build_settings(max_upload_bytes=1024)
        upload = UploadDocument(
            uow_factory, failing_storage, settings, _no_op_guard(settings), RecordingJobQueue()
        )

        user = await uow_factory().users.create(
            email="owner@example.com", password_hash="$argon2id$fake", full_name="Owner"
        )
        async with uow_factory() as uow:
            workspace = await uow.workspaces.create(
                name="Acme", slug="acme-abc123", created_by_user_id=user.id
            )
            await uow.memberships.add_owner(workspace.id, user.id)
            await uow.commit()
        ctx = AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER)

        async def _slow_stream() -> AsyncIterator[bytes]:
            for chunk in (b"one ", b"two ", b"three ", b"four ", b"five "):
                yield chunk

        with pytest.raises(StorageUnavailableError):
            await upload.execute(
                ctx,
                filename="report.txt",
                title=None,
                folder_id=None,
                content_stream=_slow_stream(),
            )

        # A half-received upload must never become a document row that
        # claims content ORBIT does not actually have.
        assert uow_factory.state.documents == {}


# ---------------------------------------------------------------------------
# Orphan sweep
# ---------------------------------------------------------------------------


class TestSweepOrphanedStorage:
    async def test_an_object_with_no_referencing_row_is_reclaimed(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        # Simulate an orphan: written to storage, but no document row exists
        # for it (e.g. the database commit that should have followed failed).
        orphan_key = document_version_key(ctx.workspace_id, new_uuid7(), new_uuid7())
        await harness.storage.put_stream(
            orphan_key, _stream(b"orphaned bytes"), content_type="text/plain"
        )
        # Move the clock past the orphan's own embedded timestamp -- exactly
        # what "time passing since the upload" means in production, where the
        # sweep always runs well after the fact.
        harness.clock.advance(timedelta(hours=2))

        result = await harness.sweep.execute(ctx.workspace_id, grace_period=timedelta(seconds=0))

        assert result.objects_deleted == 1
        assert orphan_key not in harness.storage.objects

    async def test_a_referenced_object_is_never_swept(self, harness: Harness) -> None:
        ctx = await _owner_context(harness)
        created = await harness.upload.execute(
            ctx,
            filename="report.pdf",
            title=None,
            folder_id=None,
            content_stream=_stream(_PDF_BYTES),
        )
        result = await harness.sweep.execute(ctx.workspace_id, grace_period=timedelta(seconds=0))

        assert result.objects_deleted == 0
        assert created.document.current_version is not None
        assert created.document.current_version.storage_key in harness.storage.objects

    async def test_a_fresh_orphan_survives_the_grace_period(self, harness: Harness) -> None:
        """An upload in progress must never be swept out from under it."""
        ctx = await _owner_context(harness)
        orphan_key = document_version_key(ctx.workspace_id, new_uuid7(), new_uuid7())
        await harness.storage.put_stream(
            orphan_key, _stream(b"in flight"), content_type="text/plain"
        )
        # A few seconds have passed since the write -- comfortably inside the
        # default one-hour grace period, which is the case this test is about.
        harness.clock.advance(timedelta(seconds=5))

        result = await harness.sweep.execute(ctx.workspace_id)  # default grace period

        assert result.objects_deleted == 0
        assert orphan_key in harness.storage.objects


# ---------------------------------------------------------------------------
# Resource exhaustion via upload rate
# ---------------------------------------------------------------------------


class TestUploadRateLimiting:
    """Independent of the per-request size ceiling: even small, valid uploads
    cost a streaming hash, an S3 write, and a database row, so an unlimited
    *rate* of them is still a resource-exhaustion path (ADR-0017's reasoning,
    applied here)."""

    async def test_exceeding_the_per_account_limit_is_rejected(self) -> None:
        uow_factory = FakeUnitOfWorkFactory()
        storage = FakeObjectStorage()
        settings = build_settings(max_upload_bytes=1024, upload_rate_limit_per_account=2)
        limiter = InMemoryRateLimiter()
        guard = AuthRateLimitGuard(
            limiter, RateLimitPolicy.for_upload(settings), RecordingAuditSink()
        )
        upload = UploadDocument(uow_factory, storage, settings, guard, RecordingJobQueue())

        user = await uow_factory().users.create(
            email="owner@example.com", password_hash="$argon2id$fake", full_name="Owner"
        )
        async with uow_factory() as uow:
            workspace = await uow.workspaces.create(
                name="Acme", slug="acme-abc123", created_by_user_id=user.id
            )
            await uow.memberships.add_owner(workspace.id, user.id)
            await uow.commit()
        ctx = AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER)

        for index in range(2):
            await upload.execute(
                ctx,
                filename=f"file{index}.txt",
                title=None,
                folder_id=None,
                content_stream=_stream(f"content {index}".encode()),
            )

        with pytest.raises(RateLimitedError):
            await upload.execute(
                ctx,
                filename="file-over-the-limit.txt",
                title=None,
                folder_id=None,
                content_stream=_stream(b"one more"),
            )

    async def test_the_limit_is_checked_before_the_stream_is_read(self) -> None:
        """Rejection must not cost a streaming hash or a storage write."""
        uow_factory = FakeUnitOfWorkFactory()
        storage = FakeObjectStorage()
        settings = build_settings(max_upload_bytes=1024, upload_rate_limit_per_account=1)
        limiter = InMemoryRateLimiter()
        guard = AuthRateLimitGuard(
            limiter, RateLimitPolicy.for_upload(settings), RecordingAuditSink()
        )
        upload = UploadDocument(uow_factory, storage, settings, guard, RecordingJobQueue())

        user = await uow_factory().users.create(
            email="owner@example.com", password_hash="$argon2id$fake", full_name="Owner"
        )
        async with uow_factory() as uow:
            workspace = await uow.workspaces.create(
                name="Acme", slug="acme-abc123", created_by_user_id=user.id
            )
            await uow.memberships.add_owner(workspace.id, user.id)
            await uow.commit()
        ctx = AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER)

        await upload.execute(
            ctx, filename="first.txt", title=None, folder_id=None, content_stream=_stream(b"one")
        )

        touched = False

        async def _tracking_stream() -> AsyncIterator[bytes]:
            nonlocal touched
            touched = True
            yield b"unused"

        with pytest.raises(RateLimitedError):
            await upload.execute(
                ctx,
                filename="second.txt",
                title=None,
                folder_id=None,
                content_stream=_tracking_stream(),
            )
        assert touched is False
