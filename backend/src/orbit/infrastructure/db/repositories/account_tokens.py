"""One-time account token repository (password reset, email verification)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.core.ids import new_uuid7
from orbit.domain.models.entities import AccountToken, AccountTokenPurpose
from orbit.infrastructure.db.errors import flush_translating_conflicts
from orbit.infrastructure.db.models import AccountToken as AccountTokenRow


def to_entity(row: AccountTokenRow) -> AccountToken:
    return AccountToken(
        id=row.id,
        user_id=row.user_id,
        purpose=AccountTokenPurpose(row.purpose.value),
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        created_at=row.created_at,
        consumed_at=row.consumed_at,
    )


class SqlAccountTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue(
        self,
        *,
        user_id: uuid.UUID,
        purpose: AccountTokenPurpose,
        token_hash: str,
        expires_at: datetime,
        client_ip: str | None = None,
    ) -> AccountToken:
        row = AccountTokenRow(
            id=new_uuid7(),
            user_id=user_id,
            purpose=purpose.value,
            token_hash=token_hash,
            expires_at=expires_at,
            client_ip=client_ip,
        )
        self._session.add(row)
        await flush_translating_conflicts(self._session)
        await self._session.refresh(row)
        return to_entity(row)

    async def get_by_hash(
        self, token_hash: str, *, purpose: AccountTokenPurpose
    ) -> AccountToken | None:
        """Look up by hash **and** purpose.

        Filtering on purpose here rather than checking it after the fact is
        what makes cross-purpose redemption structurally impossible: an
        email-verification token simply does not exist as far as the password
        reset flow's query is concerned.
        """
        stmt = select(AccountTokenRow).where(
            AccountTokenRow.token_hash == token_hash,
            AccountTokenRow.purpose == purpose.value,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return to_entity(row) if row else None

    async def consume(self, token_id: uuid.UUID) -> bool:
        """Mark a token spent, and report whether this call is what spent it.

        The `consumed_at IS NULL` predicate makes this a compare-and-set: two
        concurrent redemptions of the same token both read it as unconsumed,
        but only one `UPDATE` matches a row. The loser gets `False` and must
        reject -- without this, a race would let one token be spent twice.
        """
        stmt = (
            update(AccountTokenRow)
            .where(AccountTokenRow.id == token_id, AccountTokenRow.consumed_at.is_(None))
            .values(consumed_at=func.now())
            .returning(AccountTokenRow.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def invalidate_outstanding(
        self, *, user_id: uuid.UUID, purpose: AccountTokenPurpose
    ) -> None:
        """Spend every unredeemed token of this purpose for this user.

        Called when issuing a new one, so that requesting a second password
        reset invalidates the first. Without it, every reset email ever sent
        stays live until its own expiry -- so an old message recovered from a
        compromised mailbox still takes over the account.
        """
        stmt = (
            update(AccountTokenRow)
            .where(
                AccountTokenRow.user_id == user_id,
                AccountTokenRow.purpose == purpose.value,
                AccountTokenRow.consumed_at.is_(None),
            )
            .values(consumed_at=func.now())
        )
        await self._session.execute(stmt)

    async def prune_expired(self, *, before: datetime) -> int:
        """Delete rows expired before `before`. A scheduled task, never a request."""
        stmt = (
            delete(AccountTokenRow)
            .where(AccountTokenRow.expires_at < before)
            .returning(AccountTokenRow.id)
        )
        return len((await self._session.execute(stmt)).scalars().all())
