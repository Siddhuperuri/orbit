"""S3-compatible object storage client (ADR-0011).

One boto3-backed client serves both MinIO (development) and AWS S3
(production); they differ only by endpoint URL and addressing style.

boto3 is synchronous. Every blocking call is dispatched to a worker thread via
`asyncio.to_thread` rather than adding `aioboto3`: the operations here are
I/O-bound, and a second AWS SDK tracking botocore's internals is not worth its
maintenance surface for what amounts to a handful of call sites.

**Uploads are true multipart, not buffer-the-whole-file-then-put.** An upload
larger than `_PART_SIZE_BYTES` is written to S3 as a sequence of parts, each
uploaded as soon as it is full, so peak memory for one upload is bounded by the
part size rather than by `ORBIT_MAX_UPLOAD_BYTES`. This is what makes N
concurrent uploads survive without becoming an out-of-memory incident -- the
exact failure mode a `bytes`-shaped interface would guarantee (ADR-0011). An
upload smaller than one part is written with a single `put_object`, since
multipart's coordination overhead buys nothing below that size.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar
from urllib.parse import quote

import anyio
import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from orbit.core.logging import get_logger
from orbit.core.metrics import STORAGE_DURATION, observe
from orbit.domain.errors import (
    ConflictError,
    StorageUnavailableError,
    StoredObjectNotFoundError,
)
from orbit.domain.ports.storage import ObjectSummary, StoredObject

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_s3.type_defs import (
        CompletedPartTypeDef,
        GetObjectOutputTypeDef,
        ListObjectsV2OutputTypeDef,
    )

from orbit.core.config import Settings

logger = get_logger(__name__)

# Below this, a part must be at least 5 MiB per the S3 API (except the last
# part of an upload, which may be smaller) -- 8 MiB gives headroom above that
# floor while keeping peak per-upload memory small relative to the configured
# ceiling.
_PART_SIZE_BYTES = 8 * 1024 * 1024

_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def build_s3_client(settings: Settings) -> S3Client:
    """Construct the S3 client for this process.

    Credentials come from configuration rather than the ambient AWS credential
    chain, so a developer's personal AWS profile can never be picked up by
    accident and used against a real bucket.
    """
    client: S3Client = boto3.client(
        "s3",
        endpoint_url=str(settings.s3_endpoint_url) if settings.s3_endpoint_url else None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(
            # MinIO requires path-style addressing; real S3 uses virtual-host style.
            s3={"addressing_style": "path" if settings.s3_force_path_style else "virtual"},
            signature_version="s3v4",
            # Bounded so a hung storage endpoint cannot occupy a request worker
            # indefinitely. Retries are handled by the caller's policy, not here.
            connect_timeout=settings.s3_connect_timeout_seconds,
            read_timeout=settings.s3_read_timeout_seconds,
            retries={"max_attempts": settings.s3_max_attempts, "mode": "standard"},
        ),
    )
    return client


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _classify(exc: BaseException) -> str:
    if isinstance(exc, ClientError) and _is_not_found(exc):
        return "not_found"
    return "error"


def _timed(operation: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Time one blocking S3 API call.

    Applied to the private single-call methods rather than to the public async
    ones, so each observation is exactly one request to the store. Timing a
    whole `put_stream` would include the time spent waiting on the *client's*
    upload, and a slow uploader would read as a slow bucket.
    """

    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(function)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with observe(STORAGE_DURATION, classify=_classify, operation=operation):
                return function(*args, **kwargs)

        return wrapper

    return decorate


def _is_not_found(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code", "")
    status = str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
    return code in _NOT_FOUND_CODES or status == "404"


class ObjectStorageClient:
    """Thin wrapper owning the boto3 client and translating its errors.

    Botocore exceptions carry endpoint hostnames and occasionally credential
    fragments. They are converted to domain errors here so that nothing above
    this module ever sees, logs, or returns them. Implements the
    `ObjectStorage` Protocol (`domain/ports/storage.py`) -- structurally, with
    no explicit inheritance, matching every other adapter in this codebase.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = build_s3_client(settings)
        self._bucket = settings.s3_bucket

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def raw(self) -> S3Client:
        """The underlying boto3 client, for adapters built in later milestones."""
        return self._client

    @_timed("head_bucket")
    def head_bucket(self) -> None:
        """Verify the bucket exists and is reachable with the configured credentials.

        Blocking. Callers on the event loop must dispatch this to a thread.
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except (ClientError, BotoCoreError) as exc:
            msg = "Object storage is unreachable or the bucket is inaccessible."
            raise StorageUnavailableError(msg, bucket=self._bucket) from exc

    def close(self) -> None:
        # botocore clients hold pooled HTTP connections; closing releases them
        # deterministically on shutdown instead of at garbage collection.
        underlying: Any = self._client
        underlying.close()

    # -- ObjectStorage ---------------------------------------------------

    async def put_stream(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        *,
        content_type: str,
    ) -> StoredObject:
        if await self.exists(key):
            # Structurally unreachable in normal operation -- keys are fresh
            # UUIDs minted per version (`core/storage_keys.py`) -- but checked
            # explicitly rather than relying only on that invariant, so a bug
            # that reused a key fails loudly instead of silently overwriting
            # someone's stored document.
            msg = "An object already exists at this storage key."
            raise ConflictError(msg, key=key)

        upload_id: str | None = None
        parts: list[CompletedPartTypeDef] = []
        buffer = bytearray()
        total = 0
        part_number = 1

        try:
            async for chunk in stream:
                buffer += chunk
                total += len(chunk)
                if len(buffer) >= _PART_SIZE_BYTES:
                    if upload_id is None:
                        upload_id = await anyio.to_thread.run_sync(
                            self._create_multipart, key, content_type
                        )
                    part = await anyio.to_thread.run_sync(
                        self._upload_part, key, upload_id, part_number, bytes(buffer)
                    )
                    parts.append(part)
                    part_number += 1
                    buffer.clear()

            if upload_id is None:
                # Never crossed one part: a single call is cheaper and
                # simpler than a one-part multipart upload.
                await anyio.to_thread.run_sync(self._put_object, key, bytes(buffer), content_type)
            else:
                if buffer:
                    part = await anyio.to_thread.run_sync(
                        self._upload_part, key, upload_id, part_number, bytes(buffer)
                    )
                    parts.append(part)
                await anyio.to_thread.run_sync(self._complete_multipart, key, upload_id, parts)
        except Exception:
            if upload_id is not None:
                # Best-effort: an incomplete multipart upload otherwise sits in
                # the bucket, invisible to any listing, billed forever. A
                # failure aborting it is logged, not raised -- the original
                # exception is the one the caller needs to see.
                try:
                    await anyio.to_thread.run_sync(self._abort_multipart, key, upload_id)
                except Exception:
                    logger.exception("storage.multipart_abort_failed", key=key)
            raise

        return StoredObject(key=key, byte_size=total)

    async def open_stream(self, key: str) -> AsyncIterator[bytes]:
        try:
            response = await anyio.to_thread.run_sync(self._get_object, key)
        except ClientError as exc:
            if _is_not_found(exc):
                # Not "unavailable": a missing object stays missing, and
                # retrying it only delays telling the user.
                msg = "No object exists at this storage key."
                raise StoredObjectNotFoundError(msg, key=key) from exc
            msg = "Object storage is unreachable."
            raise StorageUnavailableError(msg, key=key) from exc
        except BotoCoreError as exc:
            msg = "Object storage is unreachable."
            raise StorageUnavailableError(msg, key=key) from exc

        body = response["Body"]
        try:
            while True:
                chunk = await anyio.to_thread.run_sync(body.read, _PART_SIZE_BYTES)
                if not chunk:
                    return
                yield chunk
        finally:
            await anyio.to_thread.run_sync(body.close)

    async def presigned_get_url(self, key: str, *, ttl: timedelta, download_filename: str) -> str:
        try:
            return await anyio.to_thread.run_sync(
                lambda: self._client.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": self._bucket,
                        "Key": key,
                        # RFC 6266's `filename*` form: the value the browser
                        # actually shows the user is a UTF-8-encoded field the
                        # server controls, never the raw original filename
                        # spliced into a header -- a filename containing `"`
                        # or a newline cannot break this header's structure.
                        "ResponseContentDisposition": (
                            "attachment; filename*=UTF-8''" + quote(download_filename)
                        ),
                    },
                    ExpiresIn=int(ttl.total_seconds()),
                )
            )
        except (ClientError, BotoCoreError) as exc:
            msg = "Could not create a download link."
            raise StorageUnavailableError(msg, key=key) from exc

    async def delete(self, key: str) -> None:
        try:
            await anyio.to_thread.run_sync(self._delete_object, key)
        except (ClientError, BotoCoreError) as exc:
            msg = "Could not delete the stored object."
            raise StorageUnavailableError(msg, key=key) from exc

    async def exists(self, key: str) -> bool:
        try:
            await anyio.to_thread.run_sync(self._head_object, key)
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            msg = "Object storage is unreachable."
            raise StorageUnavailableError(msg, key=key) from exc
        except BotoCoreError as exc:
            msg = "Object storage is unreachable."
            raise StorageUnavailableError(msg, key=key) from exc
        return True

    async def list_keys(self, prefix: str) -> AsyncIterator[ObjectSummary]:
        continuation_token: str | None = None
        while True:
            page = await anyio.to_thread.run_sync(self._list_page, prefix, continuation_token)
            for entry in page.get("Contents", ()):
                yield ObjectSummary(key=entry["Key"], byte_size=entry["Size"])
            if not page.get("IsTruncated"):
                return
            continuation_token = page.get("NextContinuationToken")

    # -- blocking boto3 calls, always entered via `anyio.to_thread.run_sync` --

    @_timed("create_multipart")
    def _create_multipart(self, key: str, content_type: str) -> str:
        response = self._client.create_multipart_upload(
            Bucket=self._bucket, Key=key, ContentType=content_type
        )
        return response["UploadId"]

    @_timed("upload_part")
    def _upload_part(
        self, key: str, upload_id: str, part_number: int, data: bytes
    ) -> CompletedPartTypeDef:
        response = self._client.upload_part(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
            PartNumber=part_number,
            Body=data,
        )
        return {"ETag": response["ETag"], "PartNumber": part_number}

    @_timed("complete_multipart")
    def _complete_multipart(
        self, key: str, upload_id: str, parts: list[CompletedPartTypeDef]
    ) -> None:
        self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )

    @_timed("abort_multipart")
    def _abort_multipart(self, key: str, upload_id: str) -> None:
        self._client.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)

    @_timed("put_object")
    def _put_object(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    @_timed("get_object")
    def _get_object(self, key: str) -> GetObjectOutputTypeDef:
        return self._client.get_object(Bucket=self._bucket, Key=key)

    @_timed("delete_object")
    def _delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    @_timed("head_object")
    def _head_object(self, key: str) -> None:
        self._client.head_object(Bucket=self._bucket, Key=key)

    @_timed("list_objects")
    def _list_page(self, prefix: str, continuation_token: str | None) -> ListObjectsV2OutputTypeDef:
        if continuation_token:
            return self._client.list_objects_v2(
                Bucket=self._bucket, Prefix=prefix, ContinuationToken=continuation_token
            )
        return self._client.list_objects_v2(Bucket=self._bucket, Prefix=prefix)
