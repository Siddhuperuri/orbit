"""Upload, add-version, and download endpoints: HTTP contract only.

Use cases are stubbed here (`app.dependency_overrides`) -- the use cases'
own behaviour is `tests/unit/test_use_cases_upload.py`'s job, against the
fakes. This file is about what the router itself contributes: raw-body
request handling, the pre-flight `Content-Length` check, response shapes, and
error-to-status mapping.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.api.deps import (
    get_access_context,
    get_add_document_version,
    get_current_user,
    get_get_document_download,
    get_upload_document,
)
from orbit.application.documents.get_document_download import DownloadLink
from orbit.application.documents.upload_document import UploadResult
from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import (
    NotFoundError,
    UnsupportedContentTypeError,
    UploadTooLargeError,
    ValidationError,
)
from orbit.domain.models.entities import Document, DocumentVersion, ProcessingStatus, User

OverrideFn = Callable[..., None]

_WORKSPACE_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()
_DOCUMENT_ID = uuid.uuid4()


def _user() -> User:
    return User(
        id=_USER_ID,
        email="ada@example.com",
        full_name="Ada",
        is_active=True,
        token_epoch=0,
        created_at=datetime.now(UTC),
    )


def _version() -> DocumentVersion:
    now = datetime.now(UTC)
    return DocumentVersion(
        id=uuid.uuid4(),
        document_id=_DOCUMENT_ID,
        workspace_id=_WORKSPACE_ID,
        version_number=1,
        is_current=True,
        storage_key=f"workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}/{uuid.uuid4()}",
        content_sha256="0" * 64,
        byte_size=11,
        content_type="text/plain",
        original_filename="report.txt",
        status=ProcessingStatus.PENDING,
        chunk_count=0,
        created_at=now,
    )


def _document() -> Document:
    now = datetime.now(UTC)
    return Document(
        id=_DOCUMENT_ID,
        workspace_id=_WORKSPACE_ID,
        title="report.txt",
        created_at=now,
        updated_at=now,
        version=1,
        current_version=_version(),
    )


def _provider(value: object) -> Callable[[], object]:
    """Wrap a stub in a zero-argument provider.

    FastAPI introspects a dependency override the same way it introspects a
    route handler, so `lambda stub=stub: stub` declares a parameter FastAPI
    reads as a request field and resolves itself -- quietly handing the route
    something other than the stub the test bound. A closure has no parameters
    to misread.
    """
    return lambda: value


class _StubUpload:
    def __init__(self, result: UploadResult | Exception) -> None:
        self._result = result

    async def execute(self, *_args: object, **_kwargs: object) -> UploadResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _StubDownload:
    def __init__(self, result: DownloadLink | Exception) -> None:
        self._result = result

    async def execute(self, *_args: object, **_kwargs: object) -> DownloadLink:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _RecordingUpload:
    """Records whether it was ever called, for asserting a pre-flight
    rejection short-circuited before the use case ran."""

    def __init__(self) -> None:
        self.called = False

    async def execute(self, *_args: object, **_kwargs: object) -> UploadResult:
        self.called = True
        return UploadResult(document=_document(), deduplicated=False)


@pytest.fixture
def override(app: FastAPI) -> Iterator[OverrideFn]:
    mapping = {
        "upload_document": get_upload_document,
        "add_document_version": get_add_document_version,
        "get_document_download": get_get_document_download,
    }

    def _apply(**overrides: object) -> None:
        for name, stub in overrides.items():
            app.dependency_overrides[mapping[name]] = _provider(stub)

    app.dependency_overrides[get_current_user] = _user
    yield _apply
    app.dependency_overrides.clear()


@pytest.fixture
def as_role(app: FastAPI) -> Callable[[Role], None]:
    def _apply(role: Role) -> None:
        async def _context() -> AccessContext:
            return AccessContext(user_id=_USER_ID, workspace_id=_WORKSPACE_ID, role=role)

        app.dependency_overrides[get_access_context] = _context

    return _apply


class TestUploadDocument:
    def test_a_successful_upload_returns_201_with_the_document(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(
            upload_document=_StubUpload(UploadResult(document=_document(), deduplicated=False))
        )

        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents",
            params={"filename": "report.txt"},
            content=b"hello world",
        )

        assert response.status_code == 201
        body = response.json()
        assert body["deduplicated"] is False
        assert body["document"]["id"] == str(_DOCUMENT_ID)

    def test_a_deduplicated_upload_is_flagged_in_the_response(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(upload_document=_StubUpload(UploadResult(document=_document(), deduplicated=True)))

        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents",
            params={"filename": "report.txt"},
            content=b"hello world",
        )

        assert response.status_code == 201
        assert response.json()["deduplicated"] is True

    def test_filename_is_required(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(
            upload_document=_StubUpload(UploadResult(document=_document(), deduplicated=False))
        )

        response = client.post(f"/api/v1/workspaces/{_WORKSPACE_ID}/documents", content=b"data")
        assert response.status_code == 422

    def test_a_declared_oversized_content_length_is_rejected_before_the_use_case_runs(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        """The router's own pre-flight check, distinct from the use case's
        live streaming enforcement -- this test can only exercise the
        router's half."""
        as_role(Role.OWNER)
        recorder = _RecordingUpload()
        override(upload_document=recorder)

        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents",
            params={"filename": "big.pdf"},
            headers={"content-length": str(10 * 1024 * 1024 * 1024)},  # 10 GiB, declared
            content=b"small body -- the declared length is the point, not this",
        )

        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        assert recorder.called is False

    def test_an_unsupported_content_type_maps_to_415(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(
            upload_document=_StubUpload(
                UnsupportedContentTypeError("This file type is not supported.")
            )
        )
        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents",
            params={"filename": "malware.exe"},
            content=b"MZ",
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "UNSUPPORTED_CONTENT_TYPE"

    def test_an_oversized_stream_maps_to_413(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(upload_document=_StubUpload(UploadTooLargeError("File exceeds the limit.")))
        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents",
            params={"filename": "big.txt"},
            content=b"data",
        )
        assert response.status_code == 413

    def test_a_bad_filename_maps_to_422(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(upload_document=_StubUpload(ValidationError("Filename is missing or invalid.")))
        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents",
            params={"filename": "x"},
            content=b"data",
        )
        assert response.status_code == 422


class TestAddDocumentVersion:
    def test_a_successful_version_upload_returns_201(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(
            add_document_version=_StubUpload(UploadResult(document=_document(), deduplicated=False))
        )
        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}/versions",
            params={"filename": "v2.txt"},
            content=b"new content",
        )
        assert response.status_code == 201

    def test_a_nonexistent_document_is_a_404(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.OWNER)
        override(add_document_version=_StubUpload(NotFoundError("Document not found.")))
        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{uuid.uuid4()}/versions",
            params={"filename": "v2.txt"},
            content=b"data",
        )
        assert response.status_code == 404


class TestGetDocumentDownload:
    def test_returns_a_url_and_expiry(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        expires = datetime.now(UTC) + timedelta(seconds=60)
        override(
            get_document_download=_StubDownload(
                DownloadLink(url="https://storage.example/signed-url", expires_at=expires)
            )
        )
        response = client.get(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}/download"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["url"] == "https://storage.example/signed-url"

    def test_a_missing_document_is_a_404(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        as_role(Role.VIEWER)
        override(get_document_download=_StubDownload(NotFoundError("Document not found.")))
        response = client.get(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{uuid.uuid4()}/download"
        )
        assert response.status_code == 404

    def test_no_storage_credentials_appear_in_the_response(
        self, client: TestClient, override: OverrideFn, as_role: Callable[[Role], None]
    ) -> None:
        """The whole point of a presigned URL: the response carries a scoped
        capability, never anything that could mint another one."""
        as_role(Role.VIEWER)
        expires = datetime.now(UTC) + timedelta(seconds=60)
        override(
            get_document_download=_StubDownload(
                DownloadLink(
                    url="https://storage.example/signed-url?X-Amz-Signature=abc", expires_at=expires
                )
            )
        )
        response = client.get(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}/download"
        )
        assert "aws_secret" not in response.text.lower()
        assert "s3_secret_access_key" not in response.text.lower()


class TestUnauthenticated:
    def test_upload_requires_a_session(self, client: TestClient) -> None:
        response = client.post(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents",
            params={"filename": "report.txt"},
            content=b"data",
        )
        assert response.status_code == 401

    def test_download_requires_a_session(self, client: TestClient) -> None:
        response = client.get(
            f"/api/v1/workspaces/{_WORKSPACE_ID}/documents/{_DOCUMENT_ID}/download"
        )
        assert response.status_code == 401
