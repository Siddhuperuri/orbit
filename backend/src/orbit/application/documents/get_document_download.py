"""Authorize and mint a download link for a document's current version.

The API is not in the download data path (ADR-0011): this use case checks
authorization and hands back a short-lived, single-object presigned URL, and
the browser fetches the bytes directly from object storage from then on. That
is the entire answer to "never expose private storage credentials to the
frontend" -- the caller receives a capability scoped to one object and one
expiry, never anything that could mint another one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from orbit.core.clock import Clock, SystemClock
from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import NotFoundError
from orbit.domain.ports.storage import ObjectStorage
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

#: Short by design: a presigned URL is a bearer capability for as long as it is
#: valid. A user who does not click "download" within a minute of asking for
#: one has lost nothing -- the endpoint that issued it is cheap to call again.
DOWNLOAD_URL_TTL = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class DownloadLink:
    url: str
    expires_at: datetime


class GetDocumentDownload:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, storage: ObjectStorage, clock: Clock | None = None
    ) -> None:
        self._uow_factory = uow_factory
        self._storage = storage
        self._clock = clock or SystemClock()

    async def execute(
        self,
        ctx: AccessContext,
        document_id: uuid.UUID,
        *,
        version_id: uuid.UUID | None = None,
    ) -> DownloadLink:
        """A link to the current version, or -- with `version_id` -- to any
        revision. Superseded versions keep their stored object, so an earlier
        file stays retrievable even though its text is no longer indexed."""
        ctx.require(Permission.DOCUMENT_READ)

        async with self._uow_factory() as uow:
            document = await uow.documents.get(ctx, document_id)
            version = document.current_version if document else None
            if document is not None and version_id is not None:
                version = await uow.documents.get_version(ctx, document_id, version_id)

        # A cross-workspace id and a document with no content both look like
        # "not found" to the caller -- the former for the usual tenant-
        # isolation reason (a 403 would confirm existence across the
        # boundary), the latter because there is nothing a download link
        # could point at. So does a version id that belongs to another document.
        if document is None or version is None:
            msg = "Document not found."
            raise NotFoundError(msg, document_id=str(document_id))

        if not await self._storage.exists(version.storage_key):
            # The row survived something that the object did not -- a prior
            # failed delete, a hand-edited database, a bug. Reported as the
            # same "not found" a caller cannot distinguish from a bad id,
            # rather than a 500 the moment a browser follows a URL that was
            # never going to resolve.
            msg = "Document not found."
            raise NotFoundError(msg, document_id=str(document_id))

        url = await self._storage.presigned_get_url(
            version.storage_key,
            ttl=DOWNLOAD_URL_TTL,
            download_filename=version.original_filename,
        )
        return DownloadLink(url=url, expires_at=self._clock.now() + DOWNLOAD_URL_TTL)
