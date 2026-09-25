"""Audit log repository.

Append-only. There is no update and no delete: an audit record that can be
edited is not an audit record. Retention is enforced out of band by a
privileged role, not by application code (docs/database/schema.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func as sql_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.core.ids import new_uuid7
from orbit.domain.ports.audit import AuditAction, AuditEvent
from orbit.infrastructure.db.errors import flush_translating_conflicts
from orbit.infrastructure.db.models import AuditLog as AuditLogRow


class SqlAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: AuditEvent) -> uuid.UUID:
        row = AuditLogRow(
            id=new_uuid7(),
            workspace_id=event.workspace_id,
            actor_user_id=event.actor_user_id,
            actor_email=event.actor_email,
            action=event.action.value,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            request_id=event.request_id,
            client_ip=event.client_ip,
            user_agent=event.user_agent,
            metadata_json=dict(event.metadata),
        )
        self._session.add(row)
        await flush_translating_conflicts(self._session)
        return row.id

    async def list_for_workspace(
        self, workspace_id: uuid.UUID, *, limit: int = 100
    ) -> Sequence[AuditLogRow]:
        """Newest first, served by `ix_audit_logs_workspace_id_created_at`.

        Returns rows rather than entities: the audit view is a read-only
        projection with no behaviour, and inventing an entity for it would be
        ceremony (ADR-0010's read-path rule).
        """
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.workspace_id == workspace_id)
            .order_by(AuditLogRow.created_at.desc())
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def count_since(
        self, *, action: AuditAction, actor_user_id: uuid.UUID, since: datetime
    ) -> int:
        """How many times this actor did this thing recently.

        Supports after-the-fact investigation ("how many failed logins before
        the successful one?"). Deliberately *not* the rate limiter: that runs
        on every credential check and must not touch the database
        (domain/ports/rate_limiter.py).
        """
        stmt = (
            select(sql_func.count())
            .select_from(AuditLogRow)
            .where(
                AuditLogRow.action == action.value,
                AuditLogRow.actor_user_id == actor_user_id,
                AuditLogRow.created_at >= since,
            )
        )
        return (await self._session.scalar(stmt)) or 0
