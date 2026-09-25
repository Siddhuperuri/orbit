"""Identity and tenancy: users, workspaces, memberships, refresh tokens."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orbit.domain.access import Role
from orbit.infrastructure.db.models.base import (
    Base,
    OptimisticVersionMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# Native PostgreSQL enums for closed sets: an invalid value is rejected by the
# database rather than discovered later as an unhandled branch.
role_enum = SAEnum(
    Role,
    name="workspace_role",
    values_callable=lambda enum: [member.value for member in enum],
    native_enum=True,
)

MAX_EMAIL_LENGTH = 320  # RFC 5321: 64-character local part + @ + 255-character domain
MAX_NAME_LENGTH = 200


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """An account.

    Deletion is soft. A hard delete would orphan or destroy documents that belong
    to the *workspace* rather than to the person, and would erase the actor from
    audit history. The erasure path for a genuine "delete my data" request is
    anonymisation plus soft delete, documented in docs/database/schema.md.
    """

    __tablename__ = "users"

    # Stored as the user typed it, for display and for correspondence.
    # Uniqueness is enforced case-insensitively by a functional index below,
    # because "Ada@example.com" and "ada@example.com" are the same account.
    email: Mapped[str] = mapped_column(String(MAX_EMAIL_LENGTH), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)

    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    # Incremented to invalidate every outstanding access token for this user.
    # Access tokens are stateless and live up to 15 minutes (ADR-0003), so this
    # is what makes password change and forced logout take effect immediately
    # rather than after the token expires.
    token_epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    memberships: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="user",
        lazy="raise",
        cascade="all, delete-orphan",
        # Disambiguates: workspace_members has two foreign keys to users
        # (`user_id` and `invited_by_user_id`), so SQLAlchemy cannot infer which
        # one this collection follows.
        foreign_keys="WorkspaceMember.user_id",
    )

    __table_args__ = (
        # Functional unique index rather than a UNIQUE column: it is what makes
        # the constraint case-insensitive. Partial on live rows so that a
        # soft-deleted account does not permanently reserve its address.
        Index(
            "uq_users_email_lower",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("position('@' in email) > 1", name="email_has_local_part"),
        CheckConstraint("length(btrim(full_name)) > 0", name="full_name_not_blank"),
        CheckConstraint("token_epoch >= 0", name="token_epoch_non_negative"),
    )


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, OptimisticVersionMixin, Base):
    """A tenant boundary. Every document, folder, tag, and conversation lives in one.

    There is deliberately no `owner_user_id` column. Ownership is a membership
    role, so a single query answers "who owns this" and there is no second place
    for the answer to drift. `created_by_user_id` is kept as immutable
    provenance -- a historical fact rather than a live authority.

    The invariant "a workspace always has at least one owner" is not expressible
    as a table constraint. It is enforced in the application and covered by
    tests; see docs/database/schema.md.
    """

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        # Provenance survives the account being purged; it must not drag the
        # workspace, which belongs to its members, down with it.
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    members: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="workspace", lazy="raise", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Slugs appear in URLs, so uniqueness is global and case-insensitive.
        # Partial on live rows so a deleted workspace releases its slug.
        Index(
            "uq_workspaces_slug_lower",
            text("lower(slug)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        # Slug charset is constrained in the database as well as the application:
        # it ends up in a URL path, and a permissive value here would be a
        # routing bug at best.
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'", name="slug_format"),
    )


class WorkspaceMember(TimestampMixin, Base):
    """A user's role in one workspace.

    The composite primary key `(workspace_id, user_id)` is the natural key and
    makes "a user has at most one role per workspace" free rather than an extra
    constraint on a surrogate id.

    Membership is **hard-deleted**, never soft-deleted. Revoking access must
    actually revoke it; a soft-deleted membership row is one forgotten
    `WHERE deleted_at IS NULL` away from being a privilege-escalation bug.
    """

    __tablename__ = "workspace_members"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    role: Mapped[Role] = mapped_column(role_enum, nullable=False)

    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship(back_populates="members", lazy="raise")
    user: Mapped[User] = relationship(
        back_populates="memberships", lazy="raise", foreign_keys=[user_id]
    )

    __table_args__ = (
        # "Which workspaces can this user see?" -- issued on every request that
        # resolves an AccessContext, so it is the hottest lookup in the system.
        Index("ix_workspace_members_user_id_workspace_id", "user_id", "workspace_id"),
        # Partial index over owners, for enforcing the last-owner rule cheaply.
        Index(
            "ix_workspace_members_owners",
            "workspace_id",
            postgresql_where=text("role = 'owner'"),
        ),
    )


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """One issued refresh token.

    The token itself is never stored -- only its SHA-256 hash. A database leak
    therefore yields no usable credential, which is the entire point.

    Tokens belong to a *family* originating at login. Presenting an already
    consumed token is only possible if it was captured, so that event revokes
    the whole family (ADR-0003).
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Shared by every token descended from one login.
    family_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Recorded for incident review: a refresh from an unexpected address is the
    # signal that a family revocation was a real theft rather than a client race.
    client_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        # The refresh lookup. Unique because a hash collision would let one
        # token authenticate as another.
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        # Family revocation on reuse detection.
        Index("ix_refresh_tokens_family_id", "family_id"),
        # Pruning expired rows, and "log me out everywhere".
        Index("ix_refresh_tokens_user_id_expires_at", "user_id", "expires_at"),
        CheckConstraint("expires_at > issued_at", name="expires_after_issue"),
        CheckConstraint("length(token_hash) = 64", name="token_hash_is_sha256_hex"),
    )


class AccountTokenPurpose(StrEnum):
    """Mirrors `orbit.domain.models.entities.AccountTokenPurpose`.

    Declared again here rather than imported so that the ORM layer owns its
    own database enum definition -- the domain must not have to change shape
    to accommodate how PostgreSQL happens to store it.
    """

    PASSWORD_RESET = "password_reset"  # noqa: S105 -- names a purpose, not a credential
    EMAIL_VERIFICATION = "email_verification"


account_token_purpose_enum = SAEnum(
    AccountTokenPurpose,
    name="account_token_purpose",
    values_callable=lambda enum: [member.value for member in enum],
)


class AccountToken(UUIDPrimaryKeyMixin, Base):
    """A one-time secret mailed to a user: password reset or email verification.

    One table for both because they are the same mechanism (see the domain
    entity for why). The `purpose` column is checked at redemption, so a
    verification token cannot be spent as a password reset.

    The token itself is never stored -- only its SHA-256 hash, exactly as for
    refresh tokens. A read of this table yields nothing redeemable.
    """

    __tablename__ = "account_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[AccountTokenPurpose] = mapped_column(account_token_purpose_enum, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Recorded for incident review, as on refresh tokens: a reset redeemed
    # from an unfamiliar address is the signal worth investigating.
    client_ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    __table_args__ = (
        # The redemption lookup. Unique because a hash collision would let one
        # token redeem another's grant.
        UniqueConstraint("token_hash", name="uq_account_tokens_token_hash"),
        # Serves both "invalidate this user's outstanding resets" (issuing a
        # new one supersedes the old) and the scheduled prune of expired rows.
        Index("ix_account_tokens_user_id_purpose", "user_id", "purpose"),
        Index("ix_account_tokens_expires_at", "expires_at"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        CheckConstraint("length(token_hash) = 64", name="token_hash_is_sha256_hex"),
    )
