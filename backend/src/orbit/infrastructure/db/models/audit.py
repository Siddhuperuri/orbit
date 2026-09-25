"""Audit log.

Append-only, and deliberately the one table in the schema with **no foreign
keys**. That is not an oversight, so it is worth stating plainly:

An audit record is a historical assertion that something happened. A foreign key
would force one of two wrong behaviours when the referenced row is purged --
either block the deletion (so a workspace can never be fully removed) or destroy
the audit trail alongside it (so the record of a deletion disappears when the
deletion completes). Both defeat the purpose of auditing.

Identifiers are therefore stored as plain values with indexes, and the display
name is snapshotted at write time so a record still reads sensibly after the
subject is gone.

Immutability is enforced by permission, not by constraint: the application role
is granted INSERT and SELECT on this table and nothing else. See
docs/database/schema.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from orbit.infrastructure.db.models.base import Base, UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """One recorded action.

    Carries `created_at` but no `updated_at`: a record that could be updated is
    not an audit record.
    """

    __tablename__ = "audit_logs"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Nullable: account-level events (registration, login) belong to no workspace.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    # Nullable: scheduled reclamation and migrations act with no user behind them.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    # Snapshotted so the record still names someone after the account is purged.
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # Dotted, stable identifiers: "document.deleted", "member.role_changed".
    # Part of the audit contract, so they are not renamed once released.
    action: Mapped[str] = mapped_column(String(64), nullable=False)

    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    # Ties an audited action to the request that caused it, and therefore to
    # every log line that request produced (ADR-0015).
    request_id: Mapped[str | None] = mapped_column(String(26), nullable=True)

    client_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Structured, action-specific detail: the old and new role on a role change,
    # the filename on an upload. Never document content, never credentials --
    # the same redaction rules that apply to logs apply here.
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        # The workspace audit view, newest first.
        Index("ix_audit_logs_workspace_id_created_at", "workspace_id", text("created_at DESC")),
        # "What did this account do?" -- the question an incident starts from.
        Index("ix_audit_logs_actor_user_id_created_at", "actor_user_id", text("created_at DESC")),
        # "What happened to this document?"
        Index("ix_audit_logs_resource_type_resource_id", "resource_type", "resource_id"),
        # Correlating an audited action back to its request and its logs.
        Index(
            "ix_audit_logs_request_id",
            "request_id",
            postgresql_where=text("request_id IS NOT NULL"),
        ),
        CheckConstraint("length(btrim(action)) > 0", name="action_not_blank"),
        # A resource reference is either complete or absent; half of one cannot
        # be resolved and would quietly break the resource history view.
        CheckConstraint(
            "(resource_type IS NULL) = (resource_id IS NULL)", name="resource_reference_complete"
        ),
    )
