"""End a session by revoking its token family."""

from __future__ import annotations

from orbit.core.tokens import hash_refresh_token
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class LogoutSession:
    """Idempotent by design: logging out twice, or logging out a token that
    was already revoked by reuse detection, both succeed silently. A logout
    endpoint that can fail is a logout a client has to retry-and-hope on."""

    def __init__(self, uow_factory: UnitOfWorkFactory, audit: AuditSink) -> None:
        self._uow_factory = uow_factory
        self._audit = audit

    async def execute(self, *, refresh_token: str, client_ip: str | None = None) -> None:
        token_hash = hash_refresh_token(refresh_token)

        async with self._uow_factory() as uow:
            record = await uow.refresh_tokens.get_by_hash(token_hash)
            if record is not None:
                await uow.refresh_tokens.revoke_family(record.family_id)
            await uow.commit()

        if record is not None:
            await self._audit.record(
                AuditEvent(
                    action=AuditAction.LOGOUT,
                    actor_user_id=record.user_id,
                    client_ip=client_ip,
                    metadata={"family_id": str(record.family_id)},
                )
            )
