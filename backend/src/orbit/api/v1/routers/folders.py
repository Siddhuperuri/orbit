"""Folder endpoints.

A workspace's folders are returned as one flat list, parents before children, and
the client builds the tree. Paging a tree is not meaningful, so the list is
bounded instead: the number of folders per workspace is capped when they are
created (`MAX_FOLDERS_PER_WORKSPACE`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from orbit.api.deps import (
    AccessContextDep,
    CreateFolderDep,
    DeleteFolderDep,
    ListFoldersDep,
    RenameFolderDep,
)
from orbit.api.v1.schemas.organization import (
    CreateFolderRequest,
    FolderListResponse,
    FolderNodeResponse,
    FolderResponse,
    RenameFolderRequest,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/folders", tags=["folders"])


@router.get(
    "",
    response_model=FolderListResponse,
    summary="List folders",
    description=(
        "Every folder in the workspace with what it holds, parents before children. "
        "Not paginated: the count per workspace is capped."
    ),
)
async def list_folders(ctx: AccessContextDep, list_all: ListFoldersDep) -> FolderListResponse:
    listings = await list_all.execute(ctx)
    return FolderListResponse(items=[FolderNodeResponse.from_listing(item) for item in listings])


@router.post(
    "",
    response_model=FolderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a folder",
    description="`409` if a sibling already has that name (compared case-insensitively).",
)
async def create_folder(
    body: CreateFolderRequest, ctx: AccessContextDep, create: CreateFolderDep
) -> FolderResponse:
    folder = await create.execute(ctx, name=body.name, parent_id=body.parent_id)
    return FolderResponse.from_entity(folder)


@router.patch(
    "/{folder_id}",
    response_model=FolderResponse,
    summary="Rename a folder",
    description="Requires `expected_version` from the last read (optimistic concurrency).",
)
async def rename_folder(
    folder_id: uuid.UUID,
    body: RenameFolderRequest,
    ctx: AccessContextDep,
    rename: RenameFolderDep,
) -> FolderResponse:
    folder = await rename.execute(
        ctx, folder_id, name=body.name, expected_version=body.expected_version
    )
    return FolderResponse.from_entity(folder)


@router.delete(
    "/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a folder",
    description=(
        "Only an empty folder can be deleted: `409 FOLDER_NOT_EMPTY` if it still holds "
        "documents (archived ones included) or subfolders. Nothing is moved or removed "
        "on the caller's behalf."
    ),
)
async def delete_folder(
    folder_id: uuid.UUID, ctx: AccessContextDep, delete: DeleteFolderDep
) -> None:
    await delete.execute(ctx, folder_id)
