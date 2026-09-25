"""Upload a file as a brand-new document (ADR-0011).

This is the one use case where "storage first, database second" ordering
actually matters, so it is worth restating plainly: the object is written to
storage, *then* the document and its first version are committed to
PostgreSQL. The two systems cannot share a transaction, so one of two
inconsistencies is always possible, and the ordering decides which:

* storage succeeds, the database write fails -> an **orphaned object**,
  invisible to every user, reclaimed later by `SweepOrphanedStorage`.
* the database write succeeds, storage never happened -> a **document row
  pointing at nothing**, visible and broken, and the pipeline fails on it
  forever.

An orphan costs storage. A dangling row costs correctness. This ordering only
ever risks the cheap failure.

Processing is never done here. The version and the job that will process it
are committed in one transaction, and only then is the worker notified
(ADR-0019): the request returns as soon as the bytes are safely stored, however
large the document.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from orbit.application.auth.rate_limits import AuthRateLimitGuard
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
from orbit.core.validation import require_non_blank
from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import ConflictError, NotFoundError
from orbit.domain.errors import UnsupportedContentTypeError as DomainUnsupportedContentTypeError
from orbit.domain.errors import UploadTooLargeError as DomainUploadTooLargeError
from orbit.domain.errors import ValidationError as DomainValidationError
from orbit.domain.models.entities import Document, NewDocumentIds, VersionContent
from orbit.domain.ports.processing import JobDispatch, ProcessingJobQueue
from orbit.domain.ports.storage import ObjectStorage
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class UploadResult:
    """What the caller learns, distinguishing a genuinely new document from a
    re-upload of content that was already there.

    The distinction is surfaced rather than hidden because a client showing
    "uploaded" for a file that silently changed nothing would be confusing --
    ADR-0011 says re-uploading identical content should feel successful, not
    that it should feel identical to creating something.
    """

    document: Document
    deduplicated: bool


class UploadDocument:
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

    # Five genuinely independent facts about one upload -- what it's called,
    # what to title it, where it goes, its bytes, and who's asking -- rather
    # than a bundle that would just move the count into a dataclass no call
    # site would find clearer.
    async def execute(  # noqa: PLR0913
        self,
        ctx: AccessContext,
        *,
        filename: str,
        title: str | None,
        folder_id: uuid.UUID | None,
        content_stream: AsyncIterator[bytes],
        client_ip: str | None = None,
    ) -> UploadResult:
        ctx.require(Permission.DOCUMENT_CREATE)
        # Checked before anything is read from the stream: each accepted
        # upload costs a streaming hash, an S3 write, and a database row, so
        # the limit has to gate entry, not just the expensive part of it.
        await self._guard.check(identity=str(ctx.user_id), client_ip=client_ip)

        if folder_id is not None:
            # Refuse a missing or deleted destination *before* a byte is read:
            # discovering it after streaming 50 MB to storage wastes the upload
            # and leaves an object for the sweep. The insert re-checks under a
            # lock, so this is a courtesy, not the control.
            async with self._uow_factory() as uow:
                if await uow.folders.get(ctx, folder_id) is None:
                    msg = "That folder no longer exists."
                    raise NotFoundError(msg, folder_id=str(folder_id))

        try:
            prepared = await prepare_upload(filename, content_stream, settings=self._settings)
        except FilenameRejectedError as exc:
            raise DomainValidationError(exc.message, **exc.context) from exc
        except ContentTypeRejectedError as exc:
            raise DomainUnsupportedContentTypeError(exc.message, **exc.context) from exc

        # `prepared.stats.content_type` is populated already: `prepare_upload`
        # sniffs the first chunk before returning, specifically so this real
        # value -- not a placeholder -- is what storage records, including for
        # the multipart case where the object write begins before the stream
        # is fully drained.
        assert prepared.stats.content_type is not None  # noqa: S101

        document_id = new_uuid7()
        version_id = new_uuid7()
        key = document_version_key(ctx.workspace_id, document_id, version_id)

        try:
            stored = await self._storage.put_stream(
                key, prepared.stream, content_type=prepared.stats.content_type
            )
        except UploadSizeExceededError as exc:
            raise DomainUploadTooLargeError(exc.message, **exc.context) from exc

        # Populated as a side effect of `storage.put_stream` having fully
        # drained `prepared.stream` (see `core/uploads.py`).
        assert prepared.stats.content_sha256 is not None  # noqa: S101

        content = VersionContent(
            storage_key=stored.key,
            content_sha256=prepared.stats.content_sha256,
            byte_size=stored.byte_size,
            content_type=prepared.stats.content_type,
            original_filename=prepared.sanitized_filename,
        )

        resolved_title = (
            require_non_blank(title, field_name="title", max_length=512)
            if title
            else prepared.sanitized_filename
        )

        async with self._uow_factory() as uow:
            existing = await uow.documents.find_by_content_hash(ctx, content.content_sha256)
            if existing is not None:
                # Someone already has this exact content current in this
                # workspace. Reprocessing it would waste embedding cost and
                # confuse a user who uploaded the same file twice; returning
                # their existing document is what ADR-0011 calls for. The
                # object just written is now redundant.
                await self._storage.delete(key)
                return UploadResult(document=existing, deduplicated=True)

            try:
                document = await uow.documents.create(
                    ctx,
                    title=resolved_title,
                    folder_id=folder_id,
                    content=content,
                    ids=NewDocumentIds(document_id=document_id, version_id=version_id),
                )
                # Same transaction: a version never exists without the job
                # that will process it, so there is no window in which a crash
                # leaves a PENDING document that nothing will ever pick up.
                job = await uow.processing.create_initial_job(
                    ctx, version_id, request_id=current_request_id()
                )
            except ConflictError:
                # Lost a race: another upload of identical content committed
                # between our check above and this insert. Resolve it exactly
                # the same way a sequential duplicate would, rather than
                # surfacing the race as a client-visible error.
                winner = await uow.documents.find_by_content_hash(ctx, content.content_sha256)
                await self._storage.delete(key)
                if winner is None:  # pragma: no cover -- defensive; see below
                    # The constraint fired, so a matching current version must
                    # exist. Not finding it would mean it was deleted in the
                    # instant between the conflict and this read -- prefer
                    # failing loudly over returning a result that lies.
                    raise
                return UploadResult(document=winner, deduplicated=True)
            except NotFoundError:
                # The destination folder was deleted after the pre-check. The
                # object is already stored and nothing will reference it.
                await self._storage.delete(key)
                raise

            await uow.commit()

        await enqueue_after_commit(
            self._queue, JobDispatch(job_id=job.id, request_id=job.request_id)
        )
        return UploadResult(document=document, deduplicated=False)
