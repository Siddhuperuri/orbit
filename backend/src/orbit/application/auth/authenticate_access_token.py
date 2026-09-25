"""Resolve the current user from a presented access token.

The one place `InvalidTokenError` (a `core`-level exception, per the layering
in backend/.importlinter) is translated into the domain's
`AuthenticationRequiredError`. `core` cannot raise a domain error itself, so
this application-layer use case is where that boundary crossing belongs.
"""

from __future__ import annotations

from orbit.core.clock import Clock, SystemClock
from orbit.core.tokens import InvalidTokenError, decode_access_token
from orbit.domain.errors import AuthenticationRequiredError
from orbit.domain.models.entities import User
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

_SESSION_EXPIRED_MESSAGE = "Your session has expired. Please sign in again."


class AuthenticateAccessToken:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        secret_key: str,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._secret_key = secret_key
        self._clock = clock or SystemClock()

    async def execute(self, token: str) -> User:
        try:
            claims = decode_access_token(token, secret_key=self._secret_key, clock=self._clock)
        except InvalidTokenError as exc:
            raise AuthenticationRequiredError(_SESSION_EXPIRED_MESSAGE) from exc

        async with self._uow_factory() as uow:
            user = await uow.users.get(claims.user_id)

        if user is None or not user.can_authenticate:
            raise AuthenticationRequiredError(_SESSION_EXPIRED_MESSAGE)

        # Compared on every request, not only for "critical operations": this
        # is what makes a password change or forced logout take effect
        # immediately rather than after the token's own expiry (ADR-0003).
        if user.token_epoch != claims.token_epoch:
            raise AuthenticationRequiredError(_SESSION_EXPIRED_MESSAGE)

        return user
