"""Workspace and membership endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from orbit.api.deps import (
    AccessContextDep,
    ChangeMemberRoleDep,
    CreateWorkspaceDep,
    CurrentUserDep,
    DeleteWorkspaceDep,
    GetWorkspaceDep,
    InviteMemberDep,
    ListMembersDep,
    ListWorkspacesDep,
    RemoveMemberDep,
    RenameWorkspaceDep,
)
from orbit.api.v1.schemas.workspaces import (
    ChangeMemberRoleRequest,
    CreateWorkspaceRequest,
    InviteMemberRequest,
    MemberResponse,
    RenameWorkspaceRequest,
    WorkspaceResponse,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace",
    description="The caller becomes its owner in the same transaction.",
)
async def create_workspace(
    body: CreateWorkspaceRequest,
    current_user: CurrentUserDep,
    create: CreateWorkspaceDep,
) -> WorkspaceResponse:
    workspace = await create.execute(name=body.name, created_by_user_id=current_user.id)
    return WorkspaceResponse.from_entity(workspace, role=None)


@router.get(
    "",
    response_model=list[WorkspaceResponse],
    summary="List my workspaces",
    description="Every workspace the caller is a member of, with their role in each.",
)
async def list_workspaces(
    current_user: CurrentUserDep, list_ws: ListWorkspacesDep
) -> list[WorkspaceResponse]:
    workspaces = await list_ws.execute(user_id=current_user.id)
    return [WorkspaceResponse.from_entity(workspace, role=role) for workspace, role in workspaces]


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Get a workspace",
)
async def get_workspace(ctx: AccessContextDep, get: GetWorkspaceDep) -> WorkspaceResponse:
    workspace = await get.execute(ctx)
    return WorkspaceResponse.from_entity(workspace, role=ctx.role)


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Rename a workspace",
    description="Requires `expected_version` from the last read (optimistic concurrency).",
)
async def rename_workspace(
    body: RenameWorkspaceRequest, ctx: AccessContextDep, rename: RenameWorkspaceDep
) -> WorkspaceResponse:
    workspace = await rename.execute(ctx, name=body.name, expected_version=body.expected_version)
    return WorkspaceResponse.from_entity(workspace, role=ctx.role)


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a workspace",
    description="Soft delete. Contents are retained and recoverable.",
)
async def delete_workspace(ctx: AccessContextDep, delete: DeleteWorkspaceDep) -> None:
    await delete.execute(ctx)


@router.get(
    "/{workspace_id}/members",
    response_model=list[MemberResponse],
    summary="List members",
)
async def list_members(ctx: AccessContextDep, list_m: ListMembersDep) -> list[MemberResponse]:
    members = await list_m.execute(ctx)
    return [MemberResponse.from_entity(member) for member in members]


@router.post(
    "/{workspace_id}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an existing account to the workspace",
)
async def invite_member(
    body: InviteMemberRequest, ctx: AccessContextDep, invite: InviteMemberDep
) -> MemberResponse:
    member = await invite.execute(ctx, user_id=body.user_id, role=body.role)
    return MemberResponse.from_entity(member)


@router.patch(
    "/{workspace_id}/members/{user_id}",
    response_model=MemberResponse,
    summary="Change a member's role",
)
async def change_member_role(
    user_id: uuid.UUID,
    body: ChangeMemberRoleRequest,
    ctx: AccessContextDep,
    change_role: ChangeMemberRoleDep,
) -> MemberResponse:
    member = await change_role.execute(ctx, user_id=user_id, role=body.role)
    return MemberResponse.from_entity(member)


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member",
    description="Hard delete: access is revoked immediately, not merely hidden.",
)
async def remove_member(user_id: uuid.UUID, ctx: AccessContextDep, remove: RemoveMemberDep) -> None:
    await remove.execute(ctx, user_id=user_id)
