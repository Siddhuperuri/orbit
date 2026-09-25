"""Shared session-issuance logic for login and refresh.

Both use cases mint the same pair of tokens by the same rules; the only
difference is which `family_id` the new refresh token belongs to (a fresh one
at login, the presented token's own family on refresh). Factoring that out is
what keeps the two use cases from silently drifting apart on TTL handling or
claim shape.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from orbit.core.clock import Clock
from orbit.core.config import Settings
from orbit.core.tokens import issue_access_token, issue_refresh_token
from orbit.domain.models.entities import NewRefreshToken, User
from orbit.domain.ports.unit_of_work import UnitOfWork


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Everything the API layer needs to respond to a successful login or
    refresh: the user, for the response body, and both tokens, for cookies."""

    user: User
    access_token: str
    access_token_expires_at: datetime
    refresh_token: str
    refresh_token_expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionRequest:
    """What login and refresh each already know and hand to `issue_session`."""

    user: User
    family_id: uuid.UUID
    settings: Settings
    clock: Clock
    client_ip: str | None = None
    user_agent: str | None = None


async def issue_session(uow: UnitOfWork, request: SessionRequest) -> IssuedSession:
    """Mint and persist one access/refresh pair within the caller's transaction.

    The refresh token is written to the database before it is returned, so a
    token that reaches a client always has a corresponding row -- there is no
    window where a client holds a credential the database does not yet know
    about.
    """
    settings, clock, user = request.settings, request.clock, request.user
    now = clock.now()

    access_token = issue_access_token(
        user_id=user.id,
        token_epoch=user.token_epoch,
        secret_key=settings.secret_key,
        ttl=timedelta(seconds=settings.access_token_ttl_seconds),
        clock=clock,
    )
    access_expires_at = now + timedelta(seconds=settings.access_token_ttl_seconds)

    refresh = issue_refresh_token()
    refresh_expires_at = now + timedelta(seconds=settings.refresh_token_ttl_seconds)

    await uow.refresh_tokens.create(
        NewRefreshToken(
            user_id=user.id,
            family_id=request.family_id,
            token_hash=refresh.sha256_hex,
            expires_at=refresh_expires_at,
            client_ip=request.client_ip,
            user_agent=request.user_agent,
        )
    )

    return IssuedSession(
        user=user,
        access_token=access_token,
        access_token_expires_at=access_expires_at,
        refresh_token=refresh.plaintext,
        refresh_token_expires_at=refresh_expires_at,
    )
