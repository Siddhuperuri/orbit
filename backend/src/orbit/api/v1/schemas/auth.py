"""Auth request/response contracts.

Tokens never appear in a response body -- they travel as `HttpOnly` cookies
(ADR-0009, `api/cookies.py`). These schemas describe only what a client needs
in JSON: the account it now holds a session for.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orbit.core.validation import normalize_email, require_non_blank, validate_password
from orbit.domain.models.entities import User


class RegisterRequest(BaseModel):
    email: str = Field(examples=["ada@example.com"])
    password: str = Field(examples=["a-long-passphrase-is-fine"])
    full_name: str = Field(examples=["Ada Lovelace"])

    _normalize_email = field_validator("email")(normalize_email)
    _validate_password = field_validator("password")(validate_password)

    @field_validator("full_name")
    @classmethod
    def _validate_full_name(cls, value: str) -> str:
        return require_non_blank(value, field_name="full_name", max_length=200)


class LoginRequest(BaseModel):
    email: str
    password: str

    _normalize_email = field_validator("email")(normalize_email)


class UserResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    email: str
    full_name: str
    created_at: datetime
    #: Exposed as a boolean, not a timestamp: the client needs to know whether
    #: to prompt, and the exact moment is audit detail, not UI state.
    email_verified: bool

    @classmethod
    def from_entity(cls, user: User) -> UserResponse:
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            created_at=user.created_at,
            email_verified=user.email_verified_at is not None,
        )


class SessionResponse(BaseModel):
    """Confirms who is now authenticated. The session itself lives in cookies."""

    model_config = ConfigDict(frozen=True)

    user: UserResponse


class PasswordResetRequest(BaseModel):
    """Start a reset. The response is identical whether or not the account
    exists, so this schema has no success/failure variants to describe."""

    email: str = Field(examples=["ada@example.com"])

    _normalize_email = field_validator("email")(normalize_email)


class PasswordResetConfirmRequest(BaseModel):
    # Bounded so an oversized body is rejected by the schema rather than
    # reaching the hash function. The tokens ORBIT issues are 43 characters;
    # the ceiling is generous only to avoid coupling the contract to that.
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(examples=["a-long-passphrase-is-fine"])

    _validate_password = field_validator("new_password")(validate_password)


class EmailVerificationConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
