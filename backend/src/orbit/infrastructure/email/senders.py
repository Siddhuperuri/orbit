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

import asyncio
import smtplib
import ssl
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid

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


class SmtpEmailSender:
    """Delivers through an SMTP relay, using only the standard library.

    `smtplib` is blocking, so each send runs in a worker thread rather than on the
    event loop; a slow relay then delays one request, not every request. A relay's
    refusal, a timeout, and a connection failure all become `EmailDeliveryError`, so
    callers see one failure type -- and, as with every adapter, this one never
    decides that a failed send is fine. The recipient address is not logged on
    failure, for the reason `UnconfiguredEmailSender` gives.
    """

    def __init__(  # noqa: PLR0913 -- one relay's settings, all keyword-only
        self,
        *,
        host: str,
        port: int,
        from_address: str,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        use_ssl: bool = False,
        timeout_seconds: float = 15,
    ) -> None:
        self._host = host
        self._port = port
        self._from = from_address
        self._username = username
        self._password = password
        self._starttls = starttls
        self._use_ssl = use_ssl
        self._timeout = timeout_seconds

    async def send(self, message: EmailMessage) -> None:
        mime = self._mime(message)
        try:
            await asyncio.to_thread(self._deliver, mime)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            logger.exception(
                "email.smtp_failed",
                error=type(exc).__name__,
                host=self._host,
                port=self._port,
                subject=message.subject,
            )
            msg = "The mail relay did not accept the message."
            raise EmailDeliveryError(msg) from exc

    def _mime(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        # Header values are set through the API, which rejects embedded newlines --
        # so a subject or address cannot smuggle in extra headers.
        mime["From"] = self._from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Date"] = formatdate(localtime=False)
        mime["Message-ID"] = make_msgid(domain=self._from.rpartition("@")[2] or None)
        mime.set_content(message.text_body)
        if message.html_body is not None:
            mime.add_alternative(message.html_body, subtype="html")
        return mime

    def _deliver(self, mime: MimeMessage) -> None:
        context = ssl.create_default_context()
        if self._use_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self._host, self._port, timeout=self._timeout, context=context
            )
        else:
            client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        with client:
            if self._starttls and not self._use_ssl:
                client.starttls(context=context)
            if self._username is not None and self._password is not None:
                client.login(self._username, self._password)
            client.send_message(mime)
