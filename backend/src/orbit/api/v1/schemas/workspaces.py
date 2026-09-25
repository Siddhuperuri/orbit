"""Workspace and membership request/response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orbit.core.validation import require_non_blank
from orbit.domain.access import Role
from orbit.domain.models.entities import Membership, Workspace


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(examples=["Research"])

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return require_non_blank(value, field_name="name", max_length=200)


class RenameWorkspaceRequest(BaseModel):
    name: str
    # The client's last-seen version, for optimistic concurrency (ADR-0010).
    # Required, not defaulted: a client that has never fetched the workspace
    # has no basis to overwrite it, and a default would silently invite that.
    expected_version: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return require_non_blank(value, field_name="name", max_length=200)


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    slug: str
    version: int
    created_at: datetime
    role: Role | None = Field(
        default=None, description="The caller's role, when known from a membership listing."
    )

    @classmethod
    def from_entity(cls, workspace: Workspace, *, role: Role | None = None) -> WorkspaceResponse:
        return cls(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            version=workspace.version,
            created_at=workspace.created_at,
            role=role,
        )


class InviteMemberRequest(BaseModel):
    user_id: uuid.UUID
    role: Role = Role.MEMBER


class ChangeMemberRoleRequest(BaseModel):
    role: Role


class MemberResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    role: Role
    created_at: datetime

    @classmethod
    def from_entity(cls, membership: Membership) -> MemberResponse:
        return cls(
            user_id=membership.user_id, role=membership.role, created_at=membership.created_at
        )
