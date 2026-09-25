"""Fetch a version's stored bytes for parsing, verifying them on the way.

The upload recorded a SHA-256 and a byte count while streaming the file in.
Verifying both here, over the *complete* object, closes the gap between "what
was uploaded" and "what is about to be parsed": a truncated read, a replaced
object, or a storage bug is caught before a parser ever sees the bytes
(ADR-0011's revalidation requirement).
"""

from __future__ import annotations

import hashlib
import tempfile
from typing import IO

from orbit.domain.errors import (
    DocumentIntegrityError,
    DocumentSourceMissingError,
    StoredObjectNotFoundError,
)
from orbit.domain.models.entities import DocumentVersion
from orbit.domain.ports.storage import ObjectStorage

_INTEGRITY_MESSAGE = (
    "The stored copy of this file could not be verified against the upload. Upload the file again."
)
_MISSING_MESSAGE = "The stored copy of this file is missing. Upload the file again."


async def fetch_verified_source(
    storage: ObjectStorage, version: DocumentVersion, *, spool_memory_bytes: int
) -> IO[bytes]:
    """Return a seekable file positioned at 0, holding exactly the uploaded bytes.

    Spooled: small documents stay in memory, large ones spill to a temporary
    file, so peak memory is bounded by `spool_memory_bytes` rather than by the
    upload limit. The caller owns closing it.

    Reading stops the moment more bytes arrive than were uploaded, so a
    replaced or corrupted object cannot be used to fill the worker's disk.
    """
    spool = tempfile.SpooledTemporaryFile(max_size=spool_memory_bytes)  # noqa: SIM115 -- returned
    try:
        await _copy_verified(storage, version, spool)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool


async def _copy_verified(storage: ObjectStorage, version: DocumentVersion, sink: IO[bytes]) -> None:
    hasher = hashlib.sha256()
    received = 0
    try:
        async for chunk in storage.open_stream(version.storage_key):
            received += len(chunk)
            if received > version.byte_size:
                raise DocumentIntegrityError(
                    _INTEGRITY_MESSAGE,
                    expected_bytes=version.byte_size,
                    received_at_least=received,
                )
            hasher.update(chunk)
            sink.write(chunk)
    except StoredObjectNotFoundError as exc:
        raise DocumentSourceMissingError(_MISSING_MESSAGE, storage_key=version.storage_key) from exc

    digest = hasher.hexdigest()
    if received != version.byte_size or digest != version.content_sha256:
        raise DocumentIntegrityError(
            _INTEGRITY_MESSAGE,
            expected_bytes=version.byte_size,
            received_bytes=received,
            expected_sha256=version.content_sha256,
            received_sha256=digest,
        )
