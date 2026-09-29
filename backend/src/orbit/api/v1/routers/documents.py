"""Document endpoints.

Upload and add-version are **raw-body** POSTs, not `multipart/form-data`, and
that is a deliberate departure from the conventional file-upload shape rather
than an oversight.

FastAPI's `UploadFile` (the `File(...)` dependency) is backed by Starlette's
own multipart parser, which consumes the entire request body and spools it
into a `SpooledTemporaryFile` **before a route handler ever runs**. By the
time a handler saw an `UploadFile`, ADR-0011's "abort the instant the
configured limit is exceeded" would already be moot -- the oversized body
would already be fully received and sitting on disk or in memory.

Reading the body directly via `Request.stream()` instead means every chunk
that arrives off the wire reaches `core/uploads.py`'s validating wrapper
before anything else touches it, which is what makes the live byte-counter in
that module an actual control rather than a check performed after the fact.
Upload metadata (`filename`, `title`, `folder_id`) travels as query parameters
because there is no form body left to carry them.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from orbit.api.deps import (
    AccessContextDep,
    AddDocumentTagDep,
    AddDocumentVersionDep,
    ArchiveDocumentDep,
    ClientIpDep,
    DeleteDocumentDep,
    GetDocumentDep,
    GetDocumentDownloadDep,
    GetProcessingStatusDep,
    ListDocumentsDep,
    ListDocumentVersionsDep,
    ReadDocumentContentDep,
    RemoveDocumentTagDep,
    ReprocessDocumentDep,
    SettingsDep,
    UpdateDocumentDep,
    UploadDocumentDep,
)
from orbit.api.v1.schemas.documents import (
    DocumentContentResponse,
    DocumentResponse,
    DocumentVersionListResponse,
    DocumentVersionResponse,
    DownloadLinkResponse,
    PassageResponse,
    ProcessingAttemptResponse,
    ProcessingStatusResponse,
    UpdateDocumentRequest,
    UploadResponse,
)
from orbit.api.v1.schemas.pagination import PageLimit, PageResponse
from orbit.application.documents.list_document_versions import MAX_VERSION_PAGE
from orbit.application.documents.read_document_content import MAX_PASSAGE_PAGE
from orbit.domain.documents import ArchiveFilter, DocumentListQuery, DocumentSort
from orbit.domain.errors import UploadTooLargeError
from orbit.domain.models.entities import ProcessingStatus
from orbit.domain.models.pagination import clamp_limit

router = APIRouter(prefix="/workspaces/{workspace_id}/documents", tags=["documents"])


def _reject_declared_oversize(request: Request, *, max_upload_bytes: int) -> None:
    """Refuse an upload whose declared `Content-Length` already exceeds the
    limit, without reading a single byte of the body.

    This is an optimization layered on top of the mandatory live-streaming
    check in `core/uploads.py`, never a substitute for it: a client can omit
    `Content-Length`, lie about it, or use chunked transfer-encoding, so the
    declared value is a hint that saves an expensive rejection, not a control
    (ADR-0011). The live counter catches every case this cannot.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_upload_bytes:
        msg = f"Declared upload size exceeds the {max_upload_bytes}-byte limit."
        raise UploadTooLargeError(msg, max_bytes=max_upload_bytes, declared_bytes=int(declared))


@router.get(
    "",
    response_model=PageResponse[DocumentResponse],
    summary="List documents",
    description=(
        "Keyset-paginated. Pass `next_cursor` back as `cursor`; a cursor is bound to the "
        "`sort` it was issued for and is refused under any other. Archived documents are "
        "excluded unless `archive=archived`. `q` is a case-insensitive substring of the "
        "*title* -- it is not content search (use search for that). `tag_id` may repeat; "
        "a document must carry every tag given."
    ),
)
async def list_documents(
    ctx: AccessContextDep,
    list_docs: ListDocumentsDep,
    limit: PageLimit = 25,
    cursor: str | None = None,
    folder_id: uuid.UUID | None = None,
    unfiled: Annotated[
        bool, Query(description="Only documents in no folder. Not combinable with `folder_id`.")
    ] = False,
    status_filter: Annotated[ProcessingStatus | None, Query(alias="status")] = None,
    tag_id: Annotated[list[uuid.UUID] | None, Query(max_length=5)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: DocumentSort = DocumentSort.CREATED_DESC,
    archive: ArchiveFilter = ArchiveFilter.ACTIVE,
) -> PageResponse[DocumentResponse]:
    page = await list_docs.execute(
        ctx,
        limit=clamp_limit(limit),
        cursor=cursor,
        query=DocumentListQuery(
            status=status_filter,
            folder_id=folder_id,
            unfiled=unfiled,
            tag_ids=tuple(tag_id or ()),
            text=q,
            sort=sort,
            archive=archive,
        ),
    )
    return PageResponse[DocumentResponse](
        items=[DocumentResponse.from_entity(document) for document in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get a document",
)
async def get_document(
    document_id: uuid.UUID, ctx: AccessContextDep, get: GetDocumentDep
) -> DocumentResponse:
    document = await get.execute(ctx, document_id)
    return DocumentResponse.from_entity(document)


@router.patch(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Rename and/or move a document",
    description=(
        "Send `title`, `folder_id`, or both. `folder_id: null` takes the document out of its "
        "folder; omitting it leaves the folder alone. Requires `expected_version` from the "
        "last read (optimistic concurrency): `409` if the document changed since."
    ),
)
async def update_document(
    document_id: uuid.UUID,
    body: UpdateDocumentRequest,
    ctx: AccessContextDep,
    update: UpdateDocumentDep,
) -> DocumentResponse:
    document = await update.execute(
        ctx, document_id, edit=body.to_edit(), expected_version=body.expected_version
    )
    return DocumentResponse.from_entity(document)


@router.post(
    "/{document_id}/archive",
    response_model=DocumentResponse,
    summary="Archive a document",
    description=(
        "Takes the document out of the default list and out of search and answers. Nothing "
        "is removed, and it can be restored. Idempotent."
    ),
)
async def archive_document(
    document_id: uuid.UUID, ctx: AccessContextDep, archive: ArchiveDocumentDep
) -> DocumentResponse:
    document = await archive.execute(ctx, document_id, archived=True)
    return DocumentResponse.from_entity(document)


@router.post(
    "/{document_id}/restore",
    response_model=DocumentResponse,
    summary="Restore an archived document",
    description="Idempotent: restoring a document that is not archived succeeds.",
)
async def restore_document(
    document_id: uuid.UUID, ctx: AccessContextDep, archive: ArchiveDocumentDep
) -> DocumentResponse:
    document = await archive.execute(ctx, document_id, archived=False)
    return DocumentResponse.from_entity(document)


@router.put(
    "/{document_id}/tags/{tag_id}",
    response_model=DocumentResponse,
    summary="Tag a document",
    description="Idempotent: adding a tag the document already has succeeds.",
)
async def add_document_tag(
    document_id: uuid.UUID, tag_id: uuid.UUID, ctx: AccessContextDep, add: AddDocumentTagDep
) -> DocumentResponse:
    document = await add.execute(ctx, document_id, tag_id)
    return DocumentResponse.from_entity(document)


@router.delete(
    "/{document_id}/tags/{tag_id}",
    response_model=DocumentResponse,
    summary="Remove a tag from a document",
    description="Idempotent: removing a tag the document does not have succeeds.",
)
async def remove_document_tag(
    document_id: uuid.UUID, tag_id: uuid.UUID, ctx: AccessContextDep, remove: RemoveDocumentTagDep
) -> DocumentResponse:
    document = await remove.execute(ctx, document_id, tag_id)
    return DocumentResponse.from_entity(document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
    description=(
        "Soft delete, effective immediately. Reclamation of chunks and the "
        "stored object is asynchronous."
    ),
)
async def delete_document(
    document_id: uuid.UUID, ctx: AccessContextDep, delete: DeleteDocumentDep
) -> None:
    await delete.execute(ctx, document_id)


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
    description=(
        "The request body is the raw file content -- not `multipart/form-data`. "
        "`filename` is required; `title` defaults to one made from the filename "
        "(extension dropped, underscores as spaces); `folder_id` is optional. "
        "Supported formats: PDF, Markdown, plain text. "
        "Re-uploading content that already exists in this workspace returns the "
        "existing document with `deduplicated: true`, rather than creating a "
        "second copy."
    ),
)
async def upload_document(
    request: Request,
    ctx: AccessContextDep,
    upload: UploadDocumentDep,
    settings: SettingsDep,
    client_ip: ClientIpDep,
    filename: Annotated[str, Query(min_length=1, max_length=1024)],
    title: Annotated[str | None, Query(max_length=512)] = None,
    folder_id: uuid.UUID | None = None,
) -> UploadResponse:
    _reject_declared_oversize(request, max_upload_bytes=settings.max_upload_bytes)
    result = await upload.execute(
        ctx,
        filename=filename,
        title=title,
        folder_id=folder_id,
        content_stream=request.stream(),
        client_ip=client_ip,
    )
    return UploadResponse(
        document=DocumentResponse.from_entity(result.document), deduplicated=result.deduplicated
    )


@router.post(
    "/{document_id}/versions",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new version of a document",
    description="Same validation as creating a document; replaces the current version.",
)
async def add_document_version(
    document_id: uuid.UUID,
    request: Request,
    ctx: AccessContextDep,
    add_version: AddDocumentVersionDep,
    settings: SettingsDep,
    client_ip: ClientIpDep,
    filename: Annotated[str, Query(min_length=1, max_length=1024)],
) -> UploadResponse:
    _reject_declared_oversize(request, max_upload_bytes=settings.max_upload_bytes)
    result = await add_version.execute(
        ctx, document_id, filename=filename, content_stream=request.stream(), client_ip=client_ip
    )
    return UploadResponse(
        document=DocumentResponse.from_entity(result.document), deduplicated=result.deduplicated
    )


@router.get(
    "/{document_id}/versions",
    response_model=DocumentVersionListResponse,
    summary="List a document's versions",
    description=(
        "Every revision, newest first, current and superseded. Pass `next_before` back as "
        "`before` for older ones."
    ),
)
async def list_document_versions(
    document_id: uuid.UUID,
    ctx: AccessContextDep,
    list_versions: ListDocumentVersionsDep,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_VERSION_PAGE)] = MAX_VERSION_PAGE,
) -> DocumentVersionListResponse:
    page = await list_versions.execute(ctx, document_id, before=before, limit=limit)
    return DocumentVersionListResponse.from_page(page)


@router.get(
    "/{document_id}/content",
    response_model=DocumentContentResponse,
    summary="Read a document's indexed text",
    description=(
        "The current version's passages in reading order, with the overlap the chunker adds "
        "between neighbours removed. This is the text search and answers can see, not a "
        "rendering of the original file. Empty until the version is `ready`. Earlier versions "
        "keep their file but not their text."
    ),
)
async def get_document_content(
    document_id: uuid.UUID,
    ctx: AccessContextDep,
    read_content: ReadDocumentContentDep,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PASSAGE_PAGE)] = MAX_PASSAGE_PAGE,
) -> DocumentContentResponse:
    content = await read_content.execute(ctx, document_id, after=after, limit=limit)
    return DocumentContentResponse(
        version_id=content.version.id,
        version_number=content.version.version_number,
        total_passages=content.version.chunk_count,
        items=[PassageResponse.from_entity(passage) for passage in content.items],
        next_after=content.next_after,
    )


@router.get(
    "/{document_id}/processing",
    response_model=ProcessingStatusResponse,
    summary="Get processing status",
    description=(
        "Where the current version is in the asynchronous pipeline -- `pending`, "
        "`processing`, `ready`, or `failed` with a reason -- and every attempt made. "
        "Poll this after an upload; processing never happens inside the upload request."
    ),
)
async def get_processing_status(
    document_id: uuid.UUID, ctx: AccessContextDep, get_status: GetProcessingStatusDep
) -> ProcessingStatusResponse:
    view = await get_status.execute(ctx, document_id)
    return ProcessingStatusResponse(
        document_id=view.document.id,
        version=DocumentVersionResponse.from_entity(view.version),
        attempts=[ProcessingAttemptResponse.from_entity(job) for job in view.jobs],
    )


@router.post(
    "/{document_id}/reprocess",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reprocess a failed document",
    description=(
        "Starts a fresh processing run for a document whose current version is "
        "`failed`, with a fresh retry budget. `409` if it is not failed."
    ),
)
async def reprocess_document(
    document_id: uuid.UUID, ctx: AccessContextDep, reprocess: ReprocessDocumentDep
) -> DocumentResponse:
    document = await reprocess.execute(ctx, document_id)
    return DocumentResponse.from_entity(document)


@router.get(
    "/{document_id}/download",
    response_model=DownloadLinkResponse,
    summary="Get a download link",
    description=(
        "A short-lived, single-object presigned URL for the current version's "
        "content. The API is not in the download data path: fetch `url` "
        "directly. It expires at `expires_at` and is not reusable after that."
    ),
)
async def get_document_download(
    document_id: uuid.UUID, ctx: AccessContextDep, get_download: GetDocumentDownloadDep
) -> DownloadLinkResponse:
    link = await get_download.execute(ctx, document_id)
    return DownloadLinkResponse(url=link.url, expires_at=link.expires_at)


@router.get(
    "/{document_id}/versions/{version_id}/download",
    response_model=DownloadLinkResponse,
    summary="Get a download link for one version",
    description=(
        "As the document download, but for any revision. Superseded versions keep their "
        "stored file, so an earlier upload can still be retrieved."
    ),
)
async def get_document_version_download(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    ctx: AccessContextDep,
    get_download: GetDocumentDownloadDep,
) -> DownloadLinkResponse:
    link = await get_download.execute(ctx, document_id, version_id=version_id)
    return DownloadLinkResponse(url=link.url, expires_at=link.expires_at)
