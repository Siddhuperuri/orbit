"""Email sender adapters.

There is no fake sender here, and that is the point. A "development" adapter
that accepts a message and does nothing makes a broken reset flow look healthy
in every environment where nobody checks an inbox -- and the environment where
someone finally does is production, during a lockout.

Two adapters ship:

* `UnconfiguredEmailSender` -- the default. Refuses to send and says why.
* `ConsoleEmailSender` -- writes the message to the log so a developer can
  copy a reset link locally. It is loud about the fact that nothing was
  delivered, and configuration validation refuses it in production.

A real provider (SES, Postmark, SMTP relay) is a later, separate decision;
`EmailSender` exists so that decision does not reach into the reset flow.
"""

from __future__ import annotations

from orbit.core.logging import get_logger
from orbit.domain.ports.email import EmailDeliveryError, EmailMessage

logger = get_logger(__name__)


class UnconfiguredEmailSender:
    """Refuses every send, with an explanation.

    The default binding, so that any flow depending on real delivery fails
    visibly the first time it is exercised rather than appearing to work.
    """

    async def send(self, message: EmailMessage) -> None:
        logger.error(
            "email.no_provider_configured",
            subject=message.subject,
            # The recipient is deliberately not logged: an address in an error
            # log is exactly the kind of personal data that should not
            # accumulate there.
        )
        msg = (
            "No email provider is configured. Set ORBIT_EMAIL_PROVIDER and the "
            "corresponding credentials, or use 'console' in development."
        )
        raise EmailDeliveryError(msg)


class ConsoleEmailSender:
    """Writes the message to the application log instead of delivering it.

    For local development only -- `Settings` rejects it in production. The log
    line names itself `email.not_delivered` rather than anything resembling
    success, so nobody reading logs mistakes it for delivery.
    """

    async def send(self, message: EmailMessage) -> None:
        logger.warning(
            "email.not_delivered",
            reason="console sender: message written to the log, not sent",
            to=message.to,
            subject=message.subject,
            body=message.text_body,
        )
