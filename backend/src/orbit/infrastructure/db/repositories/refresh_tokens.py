"""Refresh token repository.

Not workspace-scoped: a session belongs to a user, not to a workspace they
happen to be a member of.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.core.ids import new_uuid7
from orbit.domain.models.entities import NewRefreshToken, RefreshTokenSession
from orbit.infrastructure.db.errors import flush_translating_conflicts
from orbit.infrastructure.db.models import RefreshToken as RefreshTokenRow


def to_entity(row: RefreshTokenRow) -> RefreshTokenSession:
    return RefreshTokenSession(
        id=row.id,
        user_id=row.user_id,
        family_id=row.family_id,
        token_hash=row.token_hash,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
        revoked_at=row.revoked_at,
    )


class SqlRefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, issuance: NewRefreshToken) -> RefreshTokenSession:
        row = RefreshTokenRow(
            id=new_uuid7(),
            user_id=issuance.user_id,
            family_id=issuance.family_id,
            token_hash=issuance.token_hash,
            expires_at=issuance.expires_at,
            client_ip=issuance.client_ip,
            user_agent=issuance.user_agent,
        )
        self._session.add(row)
        await flush_translating_conflicts(self._session)
        await self._session.refresh(row)
        return to_entity(row)

    async def get_by_hash(self, token_hash: str) -> RefreshTokenSession | None:
        stmt = select(RefreshTokenRow).where(RefreshTokenRow.token_hash == token_hash)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return to_entity(row) if row else None

    async def consume(self, token_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshTokenRow)
            .where(RefreshTokenRow.id == token_id)
            .values(consumed_at=func.now())
        )

    async def revoke_family(self, family_id: uuid.UUID) -> None:
        """Invalidate every token descended from one login.

        Only tokens not already revoked are touched, so `revoked_at` keeps the
        timestamp of the *first* revocation rather than being overwritten by a
        second call -- which matters when reconstructing an incident timeline.
        """
        await self._session.execute(
            update(RefreshTokenRow)
            .where(RefreshTokenRow.family_id == family_id, RefreshTokenRow.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        """Kill every session this user has, across all families.

        Necessary because a refresh token carries no token epoch: bumping
        `users.token_epoch` invalidates outstanding *access* tokens, but a
        stolen refresh token would otherwise keep minting new ones
        indefinitely -- straight through the password reset meant to stop it.

        Called on password reset and password change.
        """
        await self._session.execute(
            update(RefreshTokenRow)
            .where(RefreshTokenRow.user_id == user_id, RefreshTokenRow.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )

    async def prune_expired(self, *, before: datetime) -> int:
        """Delete rows expired before `before`. Called by a scheduled task,
        never by request handling."""
        stmt = (
            delete(RefreshTokenRow)
            .where(RefreshTokenRow.expires_at < before)
            .returning(RefreshTokenRow.id)
        )
        deleted = (await self._session.execute(stmt)).scalars().all()
        return len(deleted)
