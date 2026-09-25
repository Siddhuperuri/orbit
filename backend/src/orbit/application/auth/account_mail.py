"""Mailing a one-time account link.

Password reset and email verification differ in what redemption *does*; the
act of putting a token into a message is identical, and identical enough that
duplicating it would mean two places to get the failure handling wrong.

The failure handling is the substance here. A delivery failure must never
propagate: for password reset it would leak that the address is known (the
unknown-address branch cannot fail, so an error would mean "known"), and for
verification it would make an unconfigured mail provider look like a bug in
registration. The operator learns about it from the log; the caller does not.
"""

from __future__ import annotations

from orbit.core.logging import get_logger
from orbit.domain.ports.email import EmailDeliveryError, EmailMessage, EmailSender

logger = get_logger(__name__)


class AccountLinkMailer:
    """Formats a one-time link into a message and sends it, never raising."""

    def __init__(self, email_sender: EmailSender, url_template: str) -> None:
        self._sender = email_sender
        self._url_template = url_template

    def link_for(self, token: str) -> str:
        # The template is validated at startup to contain `{token}`, so this
        # cannot silently produce a link with nothing in it.
        return self._url_template.format(token=token)

    async def send(self, *, to: str, subject: str, body: str, flow: str) -> bool:
        """Send it. Returns whether delivery was accepted; callers may ignore it.

        `flow` names the calling flow in the log line so a delivery outage
        can be attributed without recording the recipient, which does not
        belong in logs (ADR-0015). It is not called `event` because structlog
        already uses that key for the message itself.
        """
        try:
            await self._sender.send(EmailMessage(to=to, subject=subject, text_body=body))
        except EmailDeliveryError:
            logger.exception("account_mail.delivery_failed", flow=flow)
            return False
        return True
