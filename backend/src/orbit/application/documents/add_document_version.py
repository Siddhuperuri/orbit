"""Upload a new revision of an existing document (ADR-0011).

Shares every validation and storage rule with `UploadDocument` -- the same
streaming size cap, the same magic-byte sniff, the same "storage first"
ordering -- and differs only in the database write: a new current version
replacing the old one, rather than a new document. See `upload_document.py`
for the reasoning behind each step; this module does not repeat it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from orbit.application.auth.rate_limits import AuthRateLimitGuard
from orbit.application.documents.upload_document import UploadResult
from orbit.application.processing.dispatch import enqueue_after_commit
from orbit.core.config import Settings
from orbit.core.ids import new_uuid7
from orbit.core.logging import current_request_id
from orbit.core.storage_keys import document_version_key
from orbit.core.uploads import (
    ContentTypeRejectedError,
    FilenameRejectedError,
    UploadSizeExceededError,
    prepare_upload,
)
from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.errors import UnsupportedContentTypeError as DomainUnsupportedContentTypeError
from orbit.domain.errors import UploadTooLargeError as DomainUploadTooLargeError
from orbit.domain.errors import ValidationError as DomainValidationError
from orbit.domain.models.entities import VersionContent
from orbit.domain.ports.processing import JobDispatch, ProcessingJobQueue
from orbit.domain.ports.storage import ObjectStorage
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class AddDocumentVersion:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: ObjectStorage,
        settings: Settings,
        guard: AuthRateLimitGuard,
        queue: ProcessingJobQueue,
    ) -> None:
        self._uow_factory = uow_factory
        self._storage = storage
        self._settings = settings
        self._guard = guard
        self._queue = queue

    async def execute(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        filename: str,
        content_stream: AsyncIterator[bytes],
        client_ip: str | None = None,
    ) -> UploadResult:
        # Adding a version is a change to a document's *content*, not the
        # creation of a new one, so it is gated by the update permission --
        # matching `RenameDocument`, the other content-preserving mutation.
        ctx.require(Permission.DOCUMENT_UPDATE)
        await self._guard.check(identity=str(ctx.user_id), client_ip=client_ip)

        try:
            prepared = await prepare_upload(filename, content_stream, settings=self._settings)
        except FilenameRejectedError as exc:
            raise DomainValidationError(exc.message, **exc.context) from exc
        except ContentTypeRejectedError as exc:
            raise DomainUnsupportedContentTypeError(exc.message, **exc.context) from exc

        assert prepared.stats.content_type is not None  # noqa: S101

        version_id = new_uuid7()
        key = document_version_key(ctx.workspace_id, document_id, version_id)

        try:
            stored = await self._storage.put_stream(
                key, prepared.stream, content_type=prepared.stats.content_type
            )
        except UploadSizeExceededError as exc:
            raise DomainUploadTooLargeError(exc.message, **exc.context) from exc

        assert prepared.stats.content_sha256 is not None  # noqa: S101

        content = VersionContent(
            storage_key=stored.key,
            content_sha256=prepared.stats.content_sha256,
            byte_size=stored.byte_size,
            content_type=prepared.stats.content_type,
            original_filename=prepared.sanitized_filename,
        )

        async with self._uow_factory() as uow:
            existing = await uow.documents.find_by_content_hash(ctx, content.content_sha256)
            # Excludes a match against *this* document: re-adding identical
            # content as a new revision of the same document is a legitimate
            # (if pointless) re-upload, not a duplicate of something else --
            # the repository's own `add_version` applies the same exclusion
            # when checking the database constraint, for the same reason.
            if existing is not None and existing.id != document_id:
                await self._storage.delete(key)
                return UploadResult(document=existing, deduplicated=True)

            try:
                await uow.documents.add_version(
                    ctx, document_id, content=content, version_id=version_id
                )
                job = await uow.processing.create_initial_job(
                    ctx, version_id, request_id=current_request_id()
                )
            except ConflictError:
                winner = await uow.documents.find_by_content_hash(ctx, content.content_sha256)
                await self._storage.delete(key)
                if winner is None:  # pragma: no cover -- defensive; see upload_document.py
                    raise
                return UploadResult(document=winner, deduplicated=True)
            except NotFoundError:
                # The version write failed, so nothing needs rolling back in
                # the database -- but the object is already in storage and
                # would otherwise be an immediate, permanent orphan rather
                # than one the sweep has to work to find.
                await self._storage.delete(key)
                raise

            # Re-read rather than hand-assembling a `Document` from the
            # `DocumentVersion` `add_version` returned: this is the same query
            # every other reader uses, so the response to an upload and the
            # response to a subsequent `GET` are guaranteed to agree. The new
            # version is already visible here -- same transaction, and
            # `add_version` already flushed it.
            document = await uow.documents.get(ctx, document_id)
            await uow.commit()

        await enqueue_after_commit(
            self._queue, JobDispatch(job_id=job.id, request_id=job.request_id)
        )

        if document is None:  # pragma: no cover -- defensive; add_version just proved it exists
            msg = "Document not found."
            raise NotFoundError(msg, document_id=str(document_id))
        return UploadResult(document=document, deduplicated=False)
