"""Redeem a refresh token for a new access/refresh pair.

Rotation with reuse detection (ADR-0003): every refresh consumes the presented
token and issues a new one in the same family. Presenting a token a second
time is only possible if it was captured, so that event revokes the entire
family rather than being quietly ignored.
"""

from __future__ import annotations

from orbit.application.auth.session import IssuedSession, SessionRequest, issue_session
from orbit.core.clock import Clock, SystemClock
from orbit.core.config import Settings
from orbit.core.logging import get_logger
from orbit.core.tokens import hash_refresh_token
from orbit.domain.errors import AuthenticationRequiredError
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

logger = get_logger(__name__)


class RefreshSession:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        settings: Settings,
        audit: AuditSink,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._audit = audit
        self._clock = clock or SystemClock()

    async def execute(
        self,
        *,
        refresh_token: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        token_hash = hash_refresh_token(refresh_token)

        async with self._uow_factory() as uow:
            record = await uow.refresh_tokens.get_by_hash(token_hash)
            if record is None:
                msg = "Your session is no longer valid. Please sign in again."
                raise AuthenticationRequiredError(msg)

            if record.consumed_at is not None or record.revoked_at is not None:
                # Both states reject, but only one is evidence of theft,
                # and conflating them makes the audit trail lie.
                #
                # `consumed_at` set means this token was already spent and
                # is being presented again -- only possible if a copy
                # exists. `revoked_at` alone means the family was already
                # killed (by this very mechanism, or by a password reset)
                # and the legitimate client is retrying with a token it had
                # no way of knowing was dead. Recording that as a fresh
                # compromise inflates the incident count with the expected
                # aftermath of the last one.
                is_reuse = record.consumed_at is not None

                # Revoked unconditionally regardless: re-revoking an
                # already-revoked family is a harmless no-op, and doing it
                # here means the reuse path never depends on the family
                # having been revoked by some earlier request.
                await uow.refresh_tokens.revoke_family(record.family_id)
                await uow.commit()

                if is_reuse:
                    logger.warning(
                        "auth.refresh_token_reuse_detected",
                        user_id=str(record.user_id),
                        family_id=str(record.family_id),
                    )
                    # Audited as well as logged. This is the one auth event
                    # that is evidence of an actual compromise rather than a
                    # user mistake, so it has to outlive log retention.
                    await self._audit.record(
                        AuditEvent(
                            action=AuditAction.REFRESH_TOKEN_REUSE_DETECTED,
                            actor_user_id=record.user_id,
                            client_ip=client_ip,
                            user_agent=user_agent,
                            metadata={"family_id": str(record.family_id)},
                        )
                    )
                else:
                    logger.info(
                        "auth.revoked_refresh_token_presented",
                        user_id=str(record.user_id),
                        family_id=str(record.family_id),
                    )

                msg = "Your session is no longer valid. Please sign in again."
                raise AuthenticationRequiredError(msg)

            if record.expires_at <= self._clock.now():
                msg = "Your session has expired. Please sign in again."
                raise AuthenticationRequiredError(msg)

            user = await uow.users.get(record.user_id)
            if user is None or not user.can_authenticate:
                msg = "Your session is no longer valid. Please sign in again."
                raise AuthenticationRequiredError(msg)

            # Consumed before the replacement is issued, in the same
            # transaction: a crash between the two leaves the old token spent
            # and no new one issued, which fails safe (the user simply has to
            # log in again) rather than leaving two live tokens.
            await uow.refresh_tokens.consume(record.id)

            session = await issue_session(
                uow,
                SessionRequest(
                    user=user,
                    family_id=record.family_id,
                    settings=self._settings,
                    clock=self._clock,
                    client_ip=client_ip,
                    user_agent=user_agent,
                ),
            )
            await uow.commit()

        await self._audit.record(
            AuditEvent(
                action=AuditAction.SESSION_REFRESHED,
                actor_user_id=user.id,
                actor_email=user.email,
                client_ip=client_ip,
                user_agent=user_agent,
            )
        )
        return session
