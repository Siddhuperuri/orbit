"""`ObjectStorageClient` against a mocked S3 API (moto), not a fake.

Unlike `tests/unit/test_use_cases_upload.py`'s `FakeObjectStorage` -- which
exists to test *use-case* behaviour without caring how storage actually
works -- these tests exercise the real adapter's own mechanics: true
multipart upload and abort, presigned URL generation, `head_object`-based
existence checks, and how botocore errors get translated. moto intercepts
boto3 at the HTTP layer and implements the real S3 API in-process, so this
runs the genuine adapter code against genuine (if simulated) S3 semantics --
with no network call and no container.

This is what "where practical, add integration tests against local
S3-compatible storage" becomes when the practical option is a mock rather
than a live MinIO: `tests/integration/test_infrastructure.py` already covers
the live-MinIO case and is skipped without `ORBIT_S3_*` env vars pointing at
a running instance -- which, in this environment, has never once been
available (Docker has not started all milestone). Moto has no such
dependency, so it is what actually verifies this adapter today.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from urllib.parse import unquote

import boto3
import pytest
from moto import mock_aws

from orbit.core.config import Settings
from orbit.domain.errors import ConflictError, StorageUnavailableError
from orbit.infrastructure.storage import s3 as s3_module
from orbit.infrastructure.storage.s3 import ObjectStorageClient
from tests.conftest import build_settings

_BUCKET = "orbit-test-bucket"


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


@pytest.fixture
def settings() -> Settings:
    return build_settings(
        s3_bucket=_BUCKET,
        s3_region="us-east-1",
        s3_access_key_id="test-access-key",
        s3_secret_access_key="test-secret-key",
        # No endpoint override: moto intercepts real AWS S3 URLs.
        s3_endpoint_url=None,
        s3_force_path_style=False,
    )


@pytest.fixture
def storage(settings: Settings) -> Iterator[ObjectStorageClient]:
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
        yield ObjectStorageClient(settings)


# ---------------------------------------------------------------------------
# Single-shot writes (below the multipart threshold)
# ---------------------------------------------------------------------------


class TestSmallUploads:
    async def test_a_small_upload_is_written_and_readable(
        self, storage: ObjectStorageClient
    ) -> None:
        stored = await storage.put_stream(
            "workspaces/w/documents/d/v", _stream(b"hello world"), content_type="text/plain"
        )
        assert stored.byte_size == len(b"hello world")
        assert await storage.exists("workspaces/w/documents/d/v") is True

    async def test_a_single_shot_write_never_creates_a_multipart_upload(
        self, storage: ObjectStorageClient
    ) -> None:
        """Confirms the "never crossed one part" branch: a small write is a
        plain `put_object`, not a one-part multipart upload."""
        await storage.put_stream(
            "workspaces/w/documents/d/v", _stream(b"small"), content_type="text/plain"
        )
        response = storage.raw.list_multipart_uploads(Bucket=_BUCKET)
        assert response.get("Uploads", []) == []

    async def test_reading_back_returns_the_exact_bytes(self, storage: ObjectStorageClient) -> None:
        payload = b"the quick brown fox jumps over the lazy dog"
        await storage.put_stream(
            "workspaces/w/documents/d/v", _stream(payload), content_type="text/plain"
        )
        collected = bytearray()
        async for chunk in storage.open_stream("workspaces/w/documents/d/v"):
            collected += chunk
        assert bytes(collected) == payload


# ---------------------------------------------------------------------------
# True multipart upload
# ---------------------------------------------------------------------------


class TestMultipartUpload:
    """Exercised at the real part size (8 MiB), not a monkeypatched-down one.

    S3 enforces a genuine 5 MiB minimum on every non-final part, and moto
    enforces that rule too (`EntityTooSmall` on `CompleteMultipartUpload`
    otherwise) -- so shrinking `_PART_SIZE_BYTES` to make these tests cheap
    would exercise a size S3 itself would reject in production, proving
    nothing. The one full part these tests push through moto's in-memory
    backend costs a couple of seconds, not a network call.
    """

    async def test_an_upload_crossing_the_part_boundary_uses_multipart(
        self, storage: ObjectStorageClient
    ) -> None:
        first_part = b"a" * s3_module._PART_SIZE_BYTES
        remainder = b"tail bytes"

        stored = await storage.put_stream(
            "workspaces/w/documents/d/v",
            _stream(first_part, remainder),
            content_type="text/plain",
        )

        assert stored.byte_size == len(first_part) + len(remainder)
        collected = bytearray()
        async for chunk in storage.open_stream("workspaces/w/documents/d/v"):
            collected += chunk
        assert bytes(collected) == first_part + remainder

    async def test_no_incomplete_multipart_upload_is_left_behind_on_success(
        self, storage: ObjectStorageClient
    ) -> None:
        first_part = b"a" * s3_module._PART_SIZE_BYTES
        await storage.put_stream(
            "workspaces/w/documents/d/v",
            _stream(first_part, b"tail"),
            content_type="text/plain",
        )
        response = storage.raw.list_multipart_uploads(Bucket=_BUCKET)
        assert response.get("Uploads", []) == []

    async def test_a_stream_failure_mid_multipart_aborts_the_upload(
        self, storage: ObjectStorageClient
    ) -> None:
        """Partial upload failure, at the adapter level: the source stream
        breaks after one full part has already been sent to S3. No
        incomplete multipart upload -- which S3 bills for indefinitely --
        may be left behind, and no object may become readable."""
        first_part = b"a" * s3_module._PART_SIZE_BYTES

        async def _breaking_stream() -> AsyncIterator[bytes]:
            yield first_part  # completes one part
            msg = "connection reset"
            raise ConnectionError(msg)

        with pytest.raises(ConnectionError):
            await storage.put_stream(
                "workspaces/w/documents/d/v", _breaking_stream(), content_type="text/plain"
            )

        response = storage.raw.list_multipart_uploads(Bucket=_BUCKET)
        assert response.get("Uploads", []) == []
        assert await storage.exists("workspaces/w/documents/d/v") is False


# ---------------------------------------------------------------------------
# Refusing to overwrite
# ---------------------------------------------------------------------------


class TestRefusesOverwrite:
    async def test_writing_to_an_existing_key_is_a_conflict(
        self, storage: ObjectStorageClient
    ) -> None:
        await storage.put_stream(
            "workspaces/w/documents/d/v", _stream(b"original"), content_type="text/plain"
        )
        with pytest.raises(ConflictError):
            await storage.put_stream(
                "workspaces/w/documents/d/v",
                _stream(b"overwrite attempt"),
                content_type="text/plain",
            )

        # The original content survives the rejected overwrite attempt.
        collected = bytearray()
        async for chunk in storage.open_stream("workspaces/w/documents/d/v"):
            collected += chunk
        assert bytes(collected) == b"original"


# ---------------------------------------------------------------------------
# Existence and deletion
# ---------------------------------------------------------------------------


class TestExistsAndDelete:
    async def test_a_key_that_was_never_written_does_not_exist(
        self, storage: ObjectStorageClient
    ) -> None:
        assert await storage.exists("workspaces/w/documents/d/never-written") is False

    async def test_deleting_an_existing_object_removes_it(
        self, storage: ObjectStorageClient
    ) -> None:
        await storage.put_stream(
            "workspaces/w/documents/d/v", _stream(b"data"), content_type="text/plain"
        )
        await storage.delete("workspaces/w/documents/d/v")
        assert await storage.exists("workspaces/w/documents/d/v") is False

    async def test_deleting_a_key_that_never_existed_is_not_an_error(
        self, storage: ObjectStorageClient
    ) -> None:
        """Idempotent, matching the port's contract -- called from cleanup
        paths where "was it already gone" is not worth a check first."""
        await storage.delete("workspaces/w/documents/d/never-written")


# ---------------------------------------------------------------------------
# Presigned download URLs -- never expose credentials to the frontend
# ---------------------------------------------------------------------------


class TestPresignedDownload:
    async def test_a_presigned_url_is_generated_scoped_to_one_object(
        self, storage: ObjectStorageClient
    ) -> None:
        await storage.put_stream(
            "workspaces/w/documents/d/v", _stream(b"secret content"), content_type="text/plain"
        )
        url = await storage.presigned_get_url(
            "workspaces/w/documents/d/v",
            ttl=timedelta(seconds=60),
            download_filename="report.pdf",
        )
        assert "workspaces/w/documents/d/v" in url
        # A signature is present -- this is a capability url, not a bare path.
        assert "Signature=" in url or "X-Amz-Signature=" in url

    async def test_the_download_filename_is_carried_without_header_injection(
        self, storage: ObjectStorageClient
    ) -> None:
        """A filename containing quote or CRLF characters must not be able to
        break the Content-Disposition header structure -- RFC 6266's
        `filename*=UTF-8''<percent-encoded>` form makes this structurally
        impossible rather than merely escaped."""
        await storage.put_stream(
            "workspaces/w/documents/d/v", _stream(b"data"), content_type="text/plain"
        )
        dangerous_name = 'evil".pdf\r\nSet-Cookie: stolen=1'
        url = await storage.presigned_get_url(
            "workspaces/w/documents/d/v",
            ttl=timedelta(seconds=60),
            download_filename=dangerous_name,
        )
        assert "\r" not in url
        assert "\n" not in url
        # The name still round-trips through the encoding, just safely.
        assert "evil" in unquote(url)

    async def test_no_storage_credentials_appear_anywhere_in_the_url(
        self, storage: ObjectStorageClient, settings: Settings
    ) -> None:
        await storage.put_stream(
            "workspaces/w/documents/d/v", _stream(b"data"), content_type="text/plain"
        )
        url = await storage.presigned_get_url(
            "workspaces/w/documents/d/v", ttl=timedelta(seconds=60), download_filename="f.txt"
        )
        assert settings.s3_secret_access_key not in url


# ---------------------------------------------------------------------------
# Listing (the orphan sweep's dependency)
# ---------------------------------------------------------------------------


class TestListKeys:
    async def test_lists_only_keys_under_the_given_prefix(
        self, storage: ObjectStorageClient
    ) -> None:
        await storage.put_stream(
            "workspaces/a/documents/d1/v1", _stream(b"1"), content_type="text/plain"
        )
        await storage.put_stream(
            "workspaces/a/documents/d2/v2", _stream(b"22"), content_type="text/plain"
        )
        await storage.put_stream(
            "workspaces/b/documents/d3/v3", _stream(b"333"), content_type="text/plain"
        )

        keys = {summary.key async for summary in storage.list_keys("workspaces/a/documents/")}
        assert keys == {"workspaces/a/documents/d1/v1", "workspaces/a/documents/d2/v2"}

    async def test_an_empty_prefix_yields_nothing(self, storage: ObjectStorageClient) -> None:
        results = [summary async for summary in storage.list_keys("workspaces/nobody/")]
        assert results == []


# ---------------------------------------------------------------------------
# Error translation -- botocore internals never reach a caller
# ---------------------------------------------------------------------------


class TestErrorTranslation:
    async def test_a_missing_bucket_surfaces_as_storage_unavailable(
        self, settings: Settings
    ) -> None:
        with mock_aws():
            # No bucket created -- every call against it should fail.
            broken = ObjectStorageClient(settings)
            with pytest.raises(StorageUnavailableError) as excinfo:
                broken.head_bucket()
            # The domain error's message names nothing botocore-internal.
            assert "boto" not in str(excinfo.value).lower()
            assert "NoSuchBucket" not in str(excinfo.value)
