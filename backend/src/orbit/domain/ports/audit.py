"""Security audit port.

Distinct from logging, and the distinction matters. Logs are operational
telemetry: sampled in production, retained for weeks, and redacted
aggressively. An audit record is an assertion that a specific principal did a
specific thing at a specific time, and it has to survive log sampling, log
rotation, and the deletion of whatever it refers to.

The actions below are a closed set on purpose. Each value is part of the audit
contract -- dashboards, alerts, and compliance queries are written against
these strings, so they are never renamed or repurposed once released.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class AuditAction(StrEnum):
    """Security-relevant events worth reconstructing after the fact."""

    # -- Account lifecycle --
    USER_REGISTERED = "user.registered"
    EMAIL_VERIFIED = "user.email_verified"

    # -- Authentication --
    LOGIN_SUCCEEDED = "auth.login_succeeded"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    SESSION_REFRESHED = "auth.session_refreshed"

    # The one an incident review starts from: a refresh token was presented
    # twice, which is only possible if it was captured (ADR-0003).
    REFRESH_TOKEN_REUSE_DETECTED = "auth.refresh_token_reuse_detected"

    # -- Credentials --
    PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"
    PASSWORD_RESET_COMPLETED = "auth.password_reset_completed"
    # Reserved: there is no authenticated password-change endpoint yet, so
    # nothing emits this. Declared here rather than added later so the value is
    # fixed before any dashboard or alert is written against it (ADR-0018).
    PASSWORD_CHANGED = "auth.password_changed"

    # -- Abuse --
    RATE_LIMIT_EXCEEDED = "auth.rate_limit_exceeded"

    # -- Authorization --
    # Recorded on 403s, not 404s: a 403 means a *member* of the workspace
    # attempted something their role forbids, which is a far more interesting
    # signal than an outsider getting a 404.
    PERMISSION_DENIED = "authz.permission_denied"

    # -- Tenancy --
    MEMBER_ADDED = "workspace.member_added"
    MEMBER_ROLE_CHANGED = "workspace.member_role_changed"
    MEMBER_REMOVED = "workspace.member_removed"
    WORKSPACE_DELETED = "workspace.deleted"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One recorded action.

    `actor_email` is snapshotted alongside `actor_user_id` so the record still
    names someone after the account is purged -- the same reason `audit_logs`
    carries no foreign keys (docs/database/schema.md).
    """

    action: AuditAction
    actor_user_id: uuid.UUID | None = None
    actor_email: str | None = None
    workspace_id: uuid.UUID | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    #: Action-specific detail. Never credentials, never document content --
    #: the redaction rules that apply to logs apply here (ADR-0015).
    metadata: dict[str, object] = field(default_factory=dict)


class AuditSink(Protocol):
    async def record(self, event: AuditEvent) -> None:
        """Persist one audit event.

        Implementations must not raise into the caller. An audit write failing
        should never turn a successful login into a 500 -- the event is lost
        (and that loss is itself logged), but the user's action stands. The
        alternative makes the audit table a single point of failure for
        authentication.
        """
        ...
