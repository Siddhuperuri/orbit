"""Folder and tag contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orbit.core.validation import require_non_blank
from orbit.domain.models.entities import Folder, FolderListing, Tag, TagListing
from orbit.domain.organization import MAX_FOLDER_NAME_LENGTH, MAX_TAG_NAME_LENGTH

#: The tag palette, as theme tones. Kept equal to `orbit.domain.organization.TAG_COLORS`
#: by `tests/unit/test_organization.py`; spelled out here because OpenAPI needs the literal
#: values to emit an enum the frontend can type against.
TagColor = Literal["neutral", "accent", "success", "warning", "danger"]


class FolderResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    depth: int
    version: int = Field(description="Send back as `expected_version` when renaming.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, folder: Folder) -> FolderResponse:
        return cls(
            id=folder.id,
            name=folder.name,
            parent_id=folder.parent_folder_id,
            depth=folder.depth,
            version=folder.version,
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )


class FolderNodeResponse(FolderResponse):
    """A folder with what is inside it, as listed."""

    document_count: int = Field(description="Live, unarchived documents directly inside.")
    archived_document_count: int = Field(
        description="Archived documents directly inside. They keep the folder from being deleted."
    )
    child_count: int = Field(description="Subfolders directly inside.")

    @classmethod
    def from_listing(cls, listing: FolderListing) -> FolderNodeResponse:
        base = FolderResponse.from_entity(listing.folder)
        return cls(
            **base.model_dump(),
            document_count=listing.document_count,
            archived_document_count=listing.archived_document_count,
            child_count=listing.child_count,
        )


class FolderListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[FolderNodeResponse] = Field(
        description="Every folder, parents before children. Bounded per workspace, so unpaginated."
    )


class CreateFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    parent_id: uuid.UUID | None = Field(
        default=None, description="Omit or `null` to create a top-level folder."
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return require_non_blank(value, field_name="name", max_length=MAX_FOLDER_NAME_LENGTH)


class RenameFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    expected_version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return require_non_blank(value, field_name="name", max_length=MAX_FOLDER_NAME_LENGTH)


class TagResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    color: TagColor
    version: int = Field(description="Send back as `expected_version` when editing.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, tag: Tag) -> TagResponse:
        # Validated rather than cast: a colour in the database that is not in
        # the palette should fail here, loudly, not reach a client that has no
        # style for it.
        return cls.model_validate(
            {
                "id": tag.id,
                "name": tag.name,
                "color": tag.color,
                "version": tag.version,
                "created_at": tag.created_at,
                "updated_at": tag.updated_at,
            }
        )


class TagUsageResponse(TagResponse):
    document_count: int = Field(
        description="Live documents carrying the tag, archived ones included."
    )

    @classmethod
    def from_listing(cls, listing: TagListing) -> TagUsageResponse:
        base = TagResponse.from_entity(listing.tag)
        return cls(**base.model_dump(), document_count=listing.document_count)


class TagListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[TagUsageResponse] = Field(
        description="Every tag, by name. Bounded per workspace, so unpaginated."
    )


class CreateTagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    color: TagColor = "neutral"

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return require_non_blank(value, field_name="name", max_length=MAX_TAG_NAME_LENGTH)


class UpdateTagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    color: TagColor | None = None
    expected_version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_non_blank(value, field_name="name", max_length=MAX_TAG_NAME_LENGTH)
