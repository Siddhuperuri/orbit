"""Tag endpoints.

Attaching a tag to a document lives with the document (`PUT
/documents/{id}/tags/{tag_id}`), because that is a change to the document. These
routes manage the tags themselves.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from orbit.api.deps import (
    AccessContextDep,
    CreateTagDep,
    DeleteTagDep,
    ListTagsDep,
    UpdateTagDep,
)
from orbit.api.v1.schemas.organization import (
    CreateTagRequest,
    TagListResponse,
    TagResponse,
    TagUsageResponse,
    UpdateTagRequest,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/tags", tags=["tags"])


@router.get(
    "",
    response_model=TagListResponse,
    summary="List tags",
    description="Every tag in the workspace by name, with how many documents carry it.",
)
async def list_tags(ctx: AccessContextDep, list_all: ListTagsDep) -> TagListResponse:
    listings = await list_all.execute(ctx)
    return TagListResponse(items=[TagUsageResponse.from_listing(item) for item in listings])


@router.post(
    "",
    response_model=TagResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tag",
    description="`409` if a tag with that name exists (compared case-insensitively).",
)
async def create_tag(
    body: CreateTagRequest, ctx: AccessContextDep, create: CreateTagDep
) -> TagResponse:
    tag = await create.execute(ctx, name=body.name, color=body.color)
    return TagResponse.from_entity(tag)


@router.patch(
    "/{tag_id}",
    response_model=TagResponse,
    summary="Rename or recolour a tag",
    description="Requires `expected_version` from the last read (optimistic concurrency).",
)
async def update_tag(
    tag_id: uuid.UUID, body: UpdateTagRequest, ctx: AccessContextDep, update: UpdateTagDep
) -> TagResponse:
    tag = await update.execute(
        ctx,
        tag_id,
        name=body.name,
        color=body.color,
        expected_version=body.expected_version,
    )
    return TagResponse.from_entity(tag)


@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tag",
    description="Removes the tag from every document that carries it. The documents are untouched.",
)
async def delete_tag(tag_id: uuid.UUID, ctx: AccessContextDep, delete: DeleteTagDep) -> None:
    await delete.execute(ctx, tag_id)
