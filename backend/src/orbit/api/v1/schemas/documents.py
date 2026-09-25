"""Document request/response contracts.

Upload has no JSON request schema, and that is deliberate, not an omission:
the endpoint accepts a raw request body (ADR-0011's streaming validation
pipeline needs the true byte stream, not whatever Starlette's multipart parser
already buffered into a `SpooledTemporaryFile` before a route handler ever
runs -- see the router module for the full reasoning). A caller that could
specify `storage_key` or `content_sha256` directly in a JSON body would be
exactly the vulnerability ADR-0011 exists to prevent, so neither ever appears
in a request schema here -- only in responses, where they are ORBIT's own
output.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orbit.api.v1.schemas.organization import TagColor
from orbit.core.validation import require_non_blank
from orbit.domain.documents import MAX_DOCUMENT_TITLE_LENGTH, DocumentEdit, Passage
from orbit.domain.models.entities import (
    Document,
    DocumentVersion,
    ProcessingStatus,
    TagRef,
    VersionPage,
)
from orbit.domain.processing.failures import FailureKind
from orbit.domain.processing.jobs import JobStatus, PipelineStage, ProcessingJob


class UpdateDocumentRequest(BaseModel):
    """A partial edit of a document's identity: its title, its folder, or both.

    `folder_id` distinguishes three intents by *presence*, which JSON Merge Patch
    semantics require and a plain optional field cannot express: absent leaves
    the folder alone, `null` takes the document out of its folder, and an id
    files it there. `title` is simply optional.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    folder_id: uuid.UUID | None = None
    expected_version: int = Field(ge=1)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_non_blank(value, field_name="title", max_length=MAX_DOCUMENT_TITLE_LENGTH)

    def to_edit(self) -> DocumentEdit:
        return DocumentEdit(
            title=self.title,
            move_to_folder="folder_id" in self.model_fields_set,
            folder_id=self.folder_id,
        )


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    version_number: int
    is_current: bool
    status: ProcessingStatus
    original_filename: str
    byte_size: int
    content_type: str
    chunk_count: int
    page_count: int | None
    failure_code: str | None
    failure_reason: str | None = Field(
        description=(
            "Why processing failed, written for the person who uploaded the file. "
            "Present exactly when `status` is `failed`."
        )
    )
    processing_stage: PipelineStage | None = Field(
        description=(
            "Where the pipeline is right now, as last reported by the worker. Present only "
            "while `status` is `pending` or `processing`; there is no finer progress than "
            "this, so a client must not draw a percentage from it."
        )
    )
    processed_at: datetime | None
    created_at: datetime
    content_sha256: str = Field(
        description=(
            "SHA-256 of the stored file, lowercase hex. "
            "Lets a person confirm which bytes a version holds."
        )
    )
    created_by_user_id: uuid.UUID | None = Field(
        description="Who uploaded this version; `null` if that account has since been deleted."
    )

    @classmethod
    def from_entity(cls, version: DocumentVersion) -> DocumentVersionResponse:
        return cls(
            id=version.id,
            version_number=version.version_number,
            is_current=version.is_current,
            status=version.status,
            original_filename=version.original_filename,
            byte_size=version.byte_size,
            content_type=version.content_type,
            chunk_count=version.chunk_count,
            page_count=version.page_count,
            failure_code=version.failure_code,
            failure_reason=version.failure_reason,
            processing_stage=(
                PipelineStage(version.processing_stage) if version.processing_stage else None
            ),
            processed_at=version.processed_at,
            created_at=version.created_at,
            content_sha256=version.content_sha256,
            created_by_user_id=version.created_by_user_id,
        )


class TagRefResponse(BaseModel):
    """A tag as it appears on a document: enough to draw its chip."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    color: TagColor

    @classmethod
    def from_entity(cls, tag: TagRef) -> TagRefResponse:
        # Validated rather than cast: see `TagResponse.from_entity`.
        return cls.model_validate({"id": tag.id, "name": tag.name, "color": tag.color})


class DocumentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    title: str
    folder_id: uuid.UUID | None
    version: int = Field(
        description=(
            "Optimistic-concurrency counter for this document's own row. Send it back as "
            "`expected_version` when editing. Not the revision number of the file -- that is "
            "`current_version.version_number`."
        )
    )
    created_at: datetime
    updated_at: datetime
    created_by_user_id: uuid.UUID | None
    archived_at: datetime | None = Field(
        description=(
            "Set while the document is archived: out of the default list and out of answers."
        )
    )
    tags: list[TagRefResponse]
    current_version: DocumentVersionResponse | None

    @classmethod
    def from_entity(cls, document: Document) -> DocumentResponse:
        return cls(
            id=document.id,
            title=document.title,
            folder_id=document.folder_id,
            version=document.version,
            created_at=document.created_at,
            updated_at=document.updated_at,
            created_by_user_id=document.created_by_user_id,
            archived_at=document.archived_at,
            tags=[TagRefResponse.from_entity(tag) for tag in document.tags],
            current_version=(
                DocumentVersionResponse.from_entity(document.current_version)
                if document.current_version
                else None
            ),
        )


class UploadResponse(BaseModel):
    """The outcome of an upload: which document it landed on, and whether that
    document already existed (ADR-0011's deduplication)."""

    model_config = ConfigDict(frozen=True)

    document: DocumentResponse
    deduplicated: bool = Field(
        description=(
            "True if this content already existed as another document's current "
            "version. The upload succeeded, but no new document or version was "
            "created -- `document` is the pre-existing one."
        )
    )


class ProcessingAttemptResponse(BaseModel):
    """One processing attempt. Operator detail (`error_message`, the worker
    id) is deliberately absent: it can name internals, and the user-facing
    explanation lives on the version."""

    model_config = ConfigDict(frozen=True)

    attempt: int
    status: JobStatus
    stage: PipelineStage | None
    error_code: str | None
    failure_kind: FailureKind | None
    scheduled_for: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_entity(cls, job: ProcessingJob) -> ProcessingAttemptResponse:
        return cls(
            attempt=job.attempt,
            status=job.status,
            stage=job.stage,
            error_code=job.error_code,
            failure_kind=job.failure_kind,
            scheduled_for=job.scheduled_for,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


class ProcessingStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: uuid.UUID
    version: DocumentVersionResponse
    attempts: list[ProcessingAttemptResponse] = Field(description="Newest attempt first.")


class DownloadLinkResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    expires_at: datetime


class DocumentVersionListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DocumentVersionResponse] = Field(description="Newest first.")
    next_before: int | None = Field(
        default=None,
        description="Pass as `before` for the next, older page. Absent on the last page.",
    )

    @classmethod
    def from_page(cls, page: VersionPage) -> DocumentVersionListResponse:
        return cls(
            items=[DocumentVersionResponse.from_entity(version) for version in page.items],
            next_before=page.next_before,
        )


class PassageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ordinal: int
    text: str = Field(
        description="The passage with text repeated from the previous one already removed."
    )
    heading_path: str | None
    page_from: int | None
    page_to: int | None
    char_start: int
    char_end: int

    @classmethod
    def from_entity(cls, passage: Passage) -> PassageResponse:
        return cls(
            ordinal=passage.ordinal,
            text=passage.text,
            heading_path=passage.heading_path,
            page_from=passage.page_from,
            page_to=passage.page_to,
            char_start=passage.char_start,
            char_end=passage.char_end,
        )


class DocumentContentResponse(BaseModel):
    """The indexed text of the current version, in reading order.

    This is what search and answers can see -- not a rendering of the original
    file, whose layout and images are not retained.
    """

    model_config = ConfigDict(frozen=True)

    version_id: uuid.UUID
    version_number: int
    total_passages: int
    items: list[PassageResponse]
    next_after: int | None = Field(
        default=None,
        description="Pass as `after` for the next page. Absent on the last page.",
    )
