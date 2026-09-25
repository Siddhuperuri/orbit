"""Authenticate with a password and issue a session.

Three protections stack here, and each covers a gap the others leave:

* **Argon2id** makes one guess expensive.
* **Rate limiting** makes many guesses impossible, per account and per address.
* **Audit logging** makes a campaign of guesses *visible* after the fact, which
  is the only one of the three that helps once an attacker has succeeded.
"""

from __future__ import annotations

from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.auth.session import IssuedSession, SessionRequest, issue_session
from orbit.core.clock import Clock, SystemClock
from orbit.core.config import Settings
from orbit.core.ids import new_uuid7
from orbit.core.security import hash_password, needs_rehash, verify_password
from orbit.domain.errors import InvalidCredentialsError
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink
from orbit.domain.ports.rate_limiter import RateLimiter
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class LoginUser:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        settings: Settings,
        rate_limiter: RateLimiter,
        audit: AuditSink,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._audit = audit
        self._guard = AuthRateLimitGuard(rate_limiter, RateLimitPolicy.for_login(settings), audit)
        self._clock = clock or SystemClock()

    async def execute(
        self,
        *,
        email: str,
        password: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        # Checked *before* the password is verified, and before any database
        # read. Verifying first would make every rejected attempt cost an
        # Argon2id hash, turning the rate limiter itself into the amplifier
        # for a CPU-exhaustion attack it was added to prevent.
        await self._guard.check(identity=email, client_ip=client_ip)

        async with self._uow_factory() as uow:
            user = await uow.users.get_by_email(email)

            # Verification runs even for an unknown account, and the error is
            # identical either way. Short-circuiting on "no such user" would
            # make the login endpoint measurably faster for that case and turn
            # it into an account-enumeration timing oracle.
            password_hash = (
                await uow.users.get_password_hash(user.id)
                if user is not None
                else _DUMMY_HASH_FOR_TIMING_PARITY
            )

            if (
                user is None
                or not user.can_authenticate
                or password_hash is None
                or not verify_password(password, password_hash)
            ):
                # Audited with the submitted address, not a user id: a failure
                # against an address that has no account is exactly the signal
                # worth seeing, and it has no id to record.
                await self._audit.record(
                    AuditEvent(
                        action=AuditAction.LOGIN_FAILED,
                        actor_user_id=user.id if user else None,
                        actor_email=email,
                        client_ip=client_ip,
                        user_agent=user_agent,
                    )
                )
                msg = "Incorrect email or password."
                raise InvalidCredentialsError(msg)

            if needs_rehash(password_hash):
                # Transparent upgrade: parameters were raised since this hash
                # was created. Only reachable after a verified password, so it
                # cannot be used to probe parameter changes.
                #
                # `upgrade_password_hash`, never `set_password`: the latter
                # bumps the token epoch, which would invalidate the session
                # this very request is about to issue (the token is minted
                # from the already-read `user.token_epoch`).
                await uow.users.upgrade_password_hash(user.id, hash_password(password))

            await uow.users.record_login(user.id)

            session = await issue_session(
                uow,
                SessionRequest(
                    user=user,
                    family_id=new_uuid7(),
                    settings=self._settings,
                    clock=self._clock,
                    client_ip=client_ip,
                    user_agent=user_agent,
                ),
            )
            await uow.commit()

        # Cleared only after the transaction commits: a login that fails to
        # commit has not happened, and should not refund the attempt budget.
        await self._guard.clear(identity=email)
        await self._audit.record(
            AuditEvent(
                action=AuditAction.LOGIN_SUCCEEDED,
                actor_user_id=session.user.id,
                actor_email=session.user.email,
                client_ip=client_ip,
                user_agent=user_agent,
            )
        )
        return session


# A real Argon2id hash of an arbitrary password, computed once at import time.
# Verifying against this for an unknown email costs the same as verifying a
# real user's hash, which is what keeps the two cases indistinguishable by timing.
_DUMMY_HASH_FOR_TIMING_PARITY = hash_password(new_uuid7().hex)
