"""Email verification: request and confirmation.

Verification answers one question -- does the person holding this account
actually control this address? -- and that answer matters for exactly one
thing here: whether password reset can be trusted. An unverified address is an
address someone may have typed by mistake or claimed deliberately, and mailing
a reset token to it hands over the account.

**Verification is deliberately not enforced at login.** Gating sign-in on it
would lock a user out of an account they legitimately created because a
message went to spam, and would make mail deliverability a hard dependency of
authentication. The gate belongs on the actions where an unverified address is
genuinely dangerous; `User.email_verified_at` carries the fact so those
decisions can be made per-feature rather than globally, and that choice is
recorded in ADR-0018.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from orbit.application.auth.account_mail import AccountLinkMailer
from orbit.application.auth.account_tokens import (
    TokenRequest,
    hash_account_token,
    issue_account_token,
)
from orbit.application.auth.rate_limits import AuthRateLimitGuard
from orbit.core.clock import Clock, SystemClock
from orbit.domain.errors import BadRequestError
from orbit.domain.models.entities import AccountTokenPurpose
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

#: Longer than a password reset: this link is not a credential that grants
#: access to anything, only a claim of address ownership, so the cost of a
#: longer window is low and the cost of expiring before someone checks a
#: rarely-read inbox is high.
VERIFICATION_TOKEN_TTL = timedelta(days=3)

_VERIFY_SUBJECT = "Confirm your ORBIT email address"


class RequestEmailVerification:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        mailer: AccountLinkMailer,
        audit: AuditSink,
        guard: AuthRateLimitGuard,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._mailer = mailer
        self._audit = audit
        self._guard = guard
        self._clock = clock or SystemClock()

    async def execute(self, *, user_id: uuid.UUID, client_ip: str | None = None) -> None:
        """Send (or resend) a verification link.

        Unlike password reset this is called with an authenticated user id
        rather than a submitted address, so there is no enumeration surface to
        protect -- the caller already proved who they are.
        """
        # Still rate limited despite being authenticated: a valid session is
        # not a licence to have ORBIT mail an address repeatedly, and the
        # address on the account is one the holder can change.
        await self._guard.check(identity=str(user_id), client_ip=client_ip)

        async with self._uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None or not user.can_authenticate:
                return
            if user.email_verified_at is not None:
                # Already verified. Silently doing nothing is right: resending
                # would issue a live token for an address that needs no proof.
                return

            issued = await issue_account_token(
                uow,
                TokenRequest(
                    user_id=user.id,
                    purpose=AccountTokenPurpose.EMAIL_VERIFICATION,
                    ttl=VERIFICATION_TOKEN_TTL,
                    clock=self._clock,
                    client_ip=client_ip,
                ),
            )
            await uow.commit()

        # Delivery failure is non-fatal: the user can request another, and
        # failing the request would make an unconfigured or degraded mail
        # provider look like a bug in registration.
        await self._mailer.send(
            to=user.email,
            subject=_VERIFY_SUBJECT,
            body=self._body(issued.plaintext),
            flow="email_verification",
        )

    def _body(self, token: str) -> str:
        link = self._mailer.link_for(token)
        days = VERIFICATION_TOKEN_TTL.days
        return (
            f"Confirm this address to finish setting up your ORBIT account:\n\n{link}\n\n"
            f"The link expires in {days} days.\n\n"
            "If you did not create an ORBIT account, you can ignore this message."
        )


class VerifyEmail:
    """Redeems a verification token."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        audit: AuditSink,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._clock = clock or SystemClock()

    async def execute(self, *, token: str, client_ip: str | None = None) -> None:
        token_hash = hash_account_token(token)

        async with self._uow_factory() as uow:
            record = await uow.account_tokens.get_by_hash(
                token_hash, purpose=AccountTokenPurpose.EMAIL_VERIFICATION
            )
            if record is None or not record.is_redeemable_at(self._clock.now()):
                msg = "This verification link is invalid or has expired."
                raise BadRequestError(msg)

            if not await uow.account_tokens.consume(record.id):
                msg = "This verification link is invalid or has expired."
                raise BadRequestError(msg)

            user = await uow.users.get(record.user_id)
            if user is None:
                msg = "This verification link is invalid or has expired."
                raise BadRequestError(msg)

            await uow.users.mark_email_verified(user.id)
            await uow.commit()

        await self._audit.record(
            AuditEvent(
                action=AuditAction.EMAIL_VERIFIED,
                actor_user_id=user.id,
                actor_email=user.email,
                client_ip=client_ip,
            )
        )
