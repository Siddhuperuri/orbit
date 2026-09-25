"""Test doubles for `ObjectStorage`.

`FakeObjectStorage` drains an upload stream into an in-memory dict rather than
implementing anything resembling multipart -- test payloads are a few bytes,
and the *use case's* behaviour is what these tests exercise, not S3's own
mechanics (that is `tests/unit/test_s3_adapter.py`'s job, against moto).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta

from orbit.domain.errors import (
    ConflictError,
    StorageUnavailableError,
    StoredObjectNotFoundError,
)
from orbit.domain.ports.storage import ObjectSummary, StoredObject


@dataclass
class _StoredEntry:
    data: bytes
    content_type: str


class FakeObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, _StoredEntry] = {}
        #: Every key ever deleted, for asserting cleanup happened -- a key
        #: can only be deleted once from `objects`, but a test asserting "the
        #: use case cleaned up after itself" needs to see that the attempt
        #: was made even for a key it also asserts is absent.
        self.deleted_keys: list[str] = []

    async def put_stream(
        self, key: str, stream: AsyncIterator[bytes], *, content_type: str
    ) -> StoredObject:
        if key in self.objects:
            msg = "An object already exists at this storage key."
            raise ConflictError(msg, key=key)
        chunks = bytearray()
        async for chunk in stream:
            chunks += chunk
        self.objects[key] = _StoredEntry(data=bytes(chunks), content_type=content_type)
        return StoredObject(key=key, byte_size=len(chunks))

    def open_stream(self, key: str) -> AsyncIterator[bytes]:
        entry = self.objects.get(key)
        if entry is None:
            msg = "No object exists at this storage key."
            raise StoredObjectNotFoundError(msg, key=key)
        return _one_chunk(entry.data)

    async def presigned_get_url(self, key: str, *, ttl: timedelta, download_filename: str) -> str:
        # Deterministic and inspectable, not a real signed URL -- tests assert
        # against the key and filename it carries, never fetch it.
        return f"https://fake-storage.test/{key}?filename={download_filename}&ttl={int(ttl.total_seconds())}"

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.deleted_keys.append(key)

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def list_keys(self, prefix: str) -> AsyncIterator[ObjectSummary]:
        for key, entry in list(self.objects.items()):
            if key.startswith(prefix):
                yield ObjectSummary(key=key, byte_size=len(entry.data))


async def _one_chunk(data: bytes) -> AsyncIterator[bytes]:
    yield data


@dataclass
class FailingObjectStorage:
    """Raises on every `put_stream`, for asserting storage-failure handling.

    `fail_after_bytes`, when set, raises partway through consuming the stream
    instead of immediately -- simulating a connection dropped mid-upload
    rather than a connection that was never established.
    """

    fail_after_bytes: int | None = None
    attempts: int = field(default=0, init=False)

    async def put_stream(
        self, key: str, stream: AsyncIterator[bytes], *, content_type: str
    ) -> StoredObject:
        self.attempts += 1
        total = 0
        async for chunk in stream:
            total += len(chunk)
            if self.fail_after_bytes is not None and total >= self.fail_after_bytes:
                msg = "Simulated storage outage."
                raise StorageUnavailableError(msg, key=key)
        msg = "Simulated storage outage."
        raise StorageUnavailableError(msg, key=key)

    def open_stream(self, key: str) -> AsyncIterator[bytes]:
        msg = "Simulated storage outage."
        raise StorageUnavailableError(msg, key=key)

    async def presigned_get_url(self, key: str, *, ttl: timedelta, download_filename: str) -> str:
        msg = "Simulated storage outage."
        raise StorageUnavailableError(msg, key=key)

    async def delete(self, key: str) -> None:
        msg = "Simulated storage outage."
        raise StorageUnavailableError(msg, key=key)

    async def exists(self, key: str) -> bool:
        msg = "Simulated storage outage."
        raise StorageUnavailableError(msg, key=key)

    def list_keys(self, prefix: str) -> AsyncIterator[ObjectSummary]:
        msg = "Simulated storage outage."
        raise StorageUnavailableError(msg, key=prefix)
