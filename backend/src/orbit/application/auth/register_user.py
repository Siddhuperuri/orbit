"""Register a new account."""

from __future__ import annotations

from orbit.application.auth.rate_limits import AuthRateLimitGuard
from orbit.core.security import hash_password
from orbit.domain.models.entities import User
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class RegisterUser:
    """Creates an account with a hashed password.

    Deliberately does not log the user in: registration and authentication are
    different guarantees (an unverified address should not silently grant a
    session forever), and keeping them separate use cases means email
    verification can be inserted later without restructuring either one.
    """

    def __init__(
        self, uow_factory: UnitOfWorkFactory, audit: AuditSink, guard: AuthRateLimitGuard
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._guard = guard

    async def execute(
        self,
        *,
        email: str,
        password: str,
        full_name: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> User:
        # Limited before the hash, not after. Argon2id is deliberately slow
        # and memory-hungry, so an unmetered registration endpoint is a CPU
        # and memory exhaustion sink that costs the attacker nothing.
        #
        # It also meters the one place ORBIT does disclose whether an
        # address is already registered: a duplicate is a 409, which is an
        # enumeration oracle if it can be asked without limit.
        await self._guard.check(identity=email, client_ip=client_ip)

        # Hashed before the transaction opens: Argon2id is deliberately slow,
        # and there is no reason to hold a database transaction open for it.
        password_hash = hash_password(password)

        async with self._uow_factory() as uow:
            user = await uow.users.create(
                email=email, password_hash=password_hash, full_name=full_name
            )
            await uow.commit()

        await self._audit.record(
            AuditEvent(
                action=AuditAction.USER_REGISTERED,
                actor_user_id=user.id,
                actor_email=user.email,
                client_ip=client_ip,
                user_agent=user_agent,
            )
        )
        return user
