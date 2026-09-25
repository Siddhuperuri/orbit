"""Password reset: request and completion.

The defining constraint of the request half is that **the response must not
depend on whether the account exists**. A reset form that says "no account
with that email" is an account-enumeration oracle available to anyone, with no
authentication and no rate limit that helps -- the attacker only needs one
request per address. So the endpoint returns the same body, the same status,
and does the same amount of visible work either way; only the internal branch
differs.

The completion half is where account takeover lives if it is wrong, so it does
four things without exception: verifies the token's hash, checks it is
unexpired and unspent, spends it atomically, and bumps `token_epoch` so every
session that existed before the reset dies with it. That last point is the one
most often missed -- resetting a password because someone stole your session
is worthless if their session survives the reset.
"""

from __future__ import annotations

from datetime import timedelta

from orbit.application.auth.account_mail import AccountLinkMailer
from orbit.application.auth.account_tokens import (
    TokenRequest,
    hash_account_token,
    issue_account_token,
)
from orbit.application.auth.rate_limits import AuthRateLimitGuard
from orbit.core.clock import Clock, SystemClock
from orbit.core.security import hash_password
from orbit.domain.errors import BadRequestError
from orbit.domain.models.entities import AccountTokenPurpose
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

#: Short by design. A reset link is a bearer credential sitting in an inbox;
#: the window in which a compromised mailbox yields an account takeover should
#: be measured in minutes, not days.
RESET_TOKEN_TTL = timedelta(minutes=30)

_RESET_SUBJECT = "Reset your ORBIT password"


class RequestPasswordReset:
    """Issues a reset token and emails it, revealing nothing either way."""

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

    async def execute(self, *, email: str, client_ip: str | None = None) -> None:
        """Always returns `None`, for every input.

        Returning nothing is not laziness -- it is the contract. Any return
        value that varied with whether the address was known would reintroduce
        the oracle this whole flow is shaped to avoid.
        """
        # Limited hard, and before any lookup. Each accepted request costs an
        # outbound email to an address the caller chose, so an unlimited
        # endpoint is a free mail-bombing service pointed at a third party --
        # and one that arrives with ORBIT's reputation attached.
        #
        # The 429 does leak that *this address* has been requested recently,
        # but only to someone already submitting that address, and only within
        # the window. That is a materially smaller disclosure than an
        # unmetered send, and the alternative (silently dropping over-limit
        # requests) would leave a real user with no signal at all.
        await self._guard.check(identity=email, client_ip=client_ip)

        async with self._uow_factory() as uow:
            user = await uow.users.get_by_email(email)

            if user is None or not user.can_authenticate:
                # Deliberate silent no-op. Audited, so the attempt is visible
                # to an operator reviewing the log even though it is invisible
                # to the caller.
                await self._audit.record(
                    AuditEvent(
                        action=AuditAction.PASSWORD_RESET_REQUESTED,
                        actor_email=email,
                        client_ip=client_ip,
                        metadata={"outcome": "no_matching_account"},
                    )
                )
                return

            issued = await issue_account_token(
                uow,
                TokenRequest(
                    user_id=user.id,
                    purpose=AccountTokenPurpose.PASSWORD_RESET,
                    ttl=RESET_TOKEN_TTL,
                    clock=self._clock,
                    client_ip=client_ip,
                ),
            )
            await uow.commit()

        # Sent after the commit: an email promising a token that was never
        # persisted is worse than a failed send, because the user follows a
        # link that cannot work. The mailer swallows delivery failures --
        # surfacing one here would leak that the address *was* known, since
        # the unknown-address branch above cannot fail.
        await self._mailer.send(
            to=user.email,
            subject=_RESET_SUBJECT,
            body=self._body(issued.plaintext),
            flow="password_reset",
        )

        await self._audit.record(
            AuditEvent(
                action=AuditAction.PASSWORD_RESET_REQUESTED,
                actor_user_id=user.id,
                actor_email=user.email,
                client_ip=client_ip,
                metadata={"outcome": "token_issued"},
            )
        )

    def _body(self, token: str) -> str:
        link = self._mailer.link_for(token)
        minutes = int(RESET_TOKEN_TTL.total_seconds() // 60)
        return (
            f"Use this link to choose a new ORBIT password:\n\n{link}\n\n"
            f"The link expires in {minutes} minutes and can be used once.\n\n"
            "If you did not request this, you can ignore this message -- your "
            "password has not changed."
        )


class CompletePasswordReset:
    """Redeems a reset token and sets a new password."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        audit: AuditSink,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._clock = clock or SystemClock()

    async def execute(self, *, token: str, new_password: str, client_ip: str | None = None) -> None:
        token_hash = hash_account_token(token)

        async with self._uow_factory() as uow:
            record = await uow.account_tokens.get_by_hash(
                token_hash, purpose=AccountTokenPurpose.PASSWORD_RESET
            )

            # One error for every failure mode -- unknown, expired, already
            # spent, wrong purpose. Distinguishing them would tell an attacker
            # holding a guessed or stale token which part to change.
            if record is None or not record.is_redeemable_at(self._clock.now()):
                await self._audit.record(
                    AuditEvent(
                        action=AuditAction.PASSWORD_RESET_COMPLETED,
                        actor_user_id=record.user_id if record else None,
                        client_ip=client_ip,
                        metadata={"outcome": "rejected"},
                    )
                )
                raise _invalid_token()

            # Compare-and-set. Two requests racing with the same token both
            # read it as unspent; only one `UPDATE` matches, and the loser is
            # rejected here rather than both succeeding.
            if not await uow.account_tokens.consume(record.id):
                raise _invalid_token()

            user = await uow.users.get(record.user_id)
            if user is None or not user.can_authenticate:
                raise _invalid_token()

            # `set_password`, not `upgrade_password_hash`: this bumps
            # `token_epoch`, killing every access token issued before the
            # reset. A reset that leaves the attacker's session alive has not
            # actually recovered the account.
            await uow.users.set_password(user.id, hash_password(new_password))

            # Refresh tokens are epoch-independent, so they need revoking on
            # their own. Missing this leaves a stolen refresh token able to
            # mint fresh access tokens indefinitely after the reset.
            await uow.refresh_tokens.revoke_all_for_user(user.id)

            await uow.commit()

        await self._audit.record(
            AuditEvent(
                action=AuditAction.PASSWORD_RESET_COMPLETED,
                actor_user_id=user.id,
                actor_email=user.email,
                client_ip=client_ip,
                metadata={"outcome": "succeeded"},
            )
        )


def _invalid_token() -> BadRequestError:
    return BadRequestError("This password reset link is invalid or has expired.")
