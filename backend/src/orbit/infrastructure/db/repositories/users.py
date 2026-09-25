"""User repository.

Account-level, so it takes no `AccessContext` -- an account exists before any
workspace membership does. Every method here is therefore a deliberate exception
to tenant scoping, and the module is kept small for that reason.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.core.ids import new_uuid7
from orbit.domain.models.entities import User
from orbit.infrastructure.db.errors import flush_translating_conflicts
from orbit.infrastructure.db.models import User as UserRow


def to_entity(row: UserRow) -> User:
    """Map the ORM row to a domain entity.

    The password hash is deliberately absent. Carrying it on the entity would
    put it into every log line, traceback, and debugger frame that touches a
    user; it is fetched separately, at the one call site that needs it.
    """
    return User(
        id=row.id,
        email=row.email,
        full_name=row.full_name,
        is_active=row.is_active,
        token_epoch=row.token_epoch,
        created_at=row.created_at,
        email_verified_at=row.email_verified_at,
        last_login_at=row.last_login_at,
        deleted_at=row.deleted_at,
    )


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        row = await self._session.get(UserRow, user_id)
        if row is None or row.deleted_at is not None:
            return None
        return to_entity(row)

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive, matching the functional unique index on the table.

        Comparing `lower(email)` on both sides is what lets PostgreSQL use
        `uq_users_email_lower`; comparing the raw column would force a
        sequential scan on every login.
        """
        stmt = select(UserRow).where(
            func.lower(UserRow.email) == email.strip().lower(),
            UserRow.deleted_at.is_(None),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return to_entity(row) if row else None

    async def get_password_hash(self, user_id: uuid.UUID) -> str | None:
        stmt = select(UserRow.password_hash).where(
            UserRow.id == user_id, UserRow.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(self, *, email: str, password_hash: str, full_name: str) -> User:
        row = UserRow(
            id=new_uuid7(),
            email=email.strip(),
            password_hash=password_hash,
            full_name=full_name.strip(),
        )
        self._session.add(row)
        # Flush rather than commit: the caller owns the transaction, but the
        # unique-email violation must surface here, attributable to this
        # statement, rather than at an unrelated commit later.
        await flush_translating_conflicts(self._session)
        await self._session.refresh(row)
        return to_entity(row)

    async def set_password(self, user_id: uuid.UUID, password_hash: str) -> None:
        """Replace the hash and invalidate every live session.

        Bumping `token_epoch` in the same statement is what makes a password
        change take effect immediately. Without it, an attacker holding a stolen
        access token keeps access for up to its full 15-minute lifetime -- during
        the exact incident the user is responding to (ADR-0003).
        """
        stmt = (
            update(UserRow)
            .where(UserRow.id == user_id, UserRow.deleted_at.is_(None))
            .values(password_hash=password_hash, token_epoch=UserRow.token_epoch + 1)
        )
        await self._session.execute(stmt)

    async def upgrade_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        """Re-encode the *same* password under stronger parameters.

        Deliberately does NOT bump `token_epoch`, and that distinction is the
        whole reason this is separate from `set_password`. The credential has
        not changed -- only its encoding -- so no live session should be
        invalidated.

        Conflating the two is a live bug, not a style question: the caller
        (login) has already read the user and is about to mint an access token
        carrying the epoch it read. Bumping the epoch mid-login mints a token
        whose epoch is instantly stale, so the user "logs in" and is rejected
        on their very next request.
        """
        stmt = (
            update(UserRow)
            .where(UserRow.id == user_id, UserRow.deleted_at.is_(None))
            .values(password_hash=password_hash)
        )
        await self._session.execute(stmt)

    async def mark_email_verified(self, user_id: uuid.UUID) -> None:
        """Stamp the address as proven.

        The `email_verified_at IS NULL` predicate makes this idempotent at the
        database rather than in the caller: a replayed request updates zero
        rows instead of moving the timestamp forward.
        """
        stmt = (
            update(UserRow)
            .where(
                UserRow.id == user_id,
                UserRow.deleted_at.is_(None),
                UserRow.email_verified_at.is_(None),
            )
            .values(email_verified_at=func.now())
        )
        await self._session.execute(stmt)

    async def record_login(self, user_id: uuid.UUID) -> None:
        stmt = update(UserRow).where(UserRow.id == user_id).values(last_login_at=func.now())
        await self._session.execute(stmt)

    async def soft_delete(self, user_id: uuid.UUID) -> None:
        """Mark the account deleted and cut every session.

        Soft, not hard: a hard delete would erase the actor from audit history
        and orphan documents that belong to the workspace rather than to the
        person. The workspace's copy of their work survives; their access does
        not.
        """
        stmt = (
            update(UserRow)
            .where(UserRow.id == user_id, UserRow.deleted_at.is_(None))
            .values(
                deleted_at=func.now(),
                is_active=False,
                token_epoch=UserRow.token_epoch + 1,
            )
        )
        await self._session.execute(stmt)
