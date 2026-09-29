"""The SMTP sender and its configuration.

The delivery test talks to a real (in-process) SMTP server over a real socket, so
it proves the wire conversation -- greeting, AUTH, envelope, DATA -- and not just
that our code calls `smtplib`.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from email import message_from_bytes, policy
from email.message import EmailMessage as MimeMessage

import pytest
from pydantic import ValidationError

from orbit.core.config import EmailProvider
from orbit.domain.ports.email import EmailDeliveryError, EmailMessage
from orbit.infrastructure.email.senders import SmtpEmailSender
from tests.conftest import build_settings


class _Inbox:
    """A minimal SMTP server that records what it is sent."""

    def __init__(self, *, require_auth: bool = False, reject_data: bool = False) -> None:
        self.messages: list[MimeMessage] = []
        self.envelopes: list[tuple[str, list[str]]] = []
        self.login: tuple[str, str] | None = None
        self._require_auth = require_auth
        self._reject_data = reject_data
        self._server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def say(line: str) -> None:
            writer.write(line.encode() + b"\r\n")
            await writer.drain()

        sender, rcpts, authed = "", [], not self._require_auth
        await say("220 inbox.test ready")
        while line := (await reader.readline()).decode().rstrip("\r\n"):
            verb = line.split(" ", 1)[0].upper()
            if verb == "EHLO":
                await writer.drain()
                writer.write(b"250-inbox.test\r\n250 AUTH PLAIN\r\n")
                await writer.drain()
            elif verb == "AUTH":
                _, _, blob = line.split(" ", 2)
                _, user, password = base64.b64decode(blob).decode().split("\x00")
                self.login = (user, password)
                authed = True
                await say("235 ok")
            elif verb == "MAIL":
                if not authed:
                    await say("530 authentication required")
                    continue
                sender = line.split("<", 1)[1].split(">", 1)[0]
                await say("250 ok")
            elif verb == "RCPT":
                rcpts.append(line.split("<", 1)[1].split(">", 1)[0])
                await say("250 ok")
            elif verb == "DATA":
                await say("354 go")
                body = b""
                while (chunk := await reader.readline()) != b".\r\n":
                    body += chunk
                if self._reject_data:
                    await say("554 rejected")
                    continue
                self.messages.append(message_from_bytes(body, policy=policy.default))
                self.envelopes.append((sender, rcpts))
                await say("250 queued")
            elif verb == "QUIT":
                await say("221 bye")
                break
            else:
                await say("250 ok")
        writer.close()


@pytest.fixture
async def inbox() -> AsyncIterator[_Inbox]:
    server = _Inbox()
    await server.start()
    yield server
    await server.stop()


def _sender(
    port: int,
    *,
    username: str | None = None,
    password: str | None = None,
    timeout_seconds: float = 5,
) -> SmtpEmailSender:
    return SmtpEmailSender(
        host="127.0.0.1",
        port=port,
        from_address="ORBIT <no-reply@orbit.test>",
        username=username,
        password=password,
        starttls=False,
        timeout_seconds=timeout_seconds,
    )


MESSAGE = EmailMessage(
    to="ada@example.test",
    subject="Reset your password",
    text_body="Open https://orbit.test/reset?token=abc",
    html_body="<p>Open the link</p>",
)


class TestDelivery:
    async def test_a_message_reaches_the_relay_with_its_envelope_and_both_bodies(
        self, inbox: _Inbox
    ) -> None:
        await _sender(inbox.port).send(MESSAGE)

        assert inbox.envelopes == [("no-reply@orbit.test", ["ada@example.test"])]
        received = inbox.messages[0]
        assert received["To"] == "ada@example.test"
        assert received["Subject"] == "Reset your password"
        assert received["From"] == "ORBIT <no-reply@orbit.test>"
        assert received["Message-ID"] and received["Date"]
        assert received.is_multipart()
        bodies = {part.get_content_type(): part for part in received.iter_parts()}
        plain = bodies["text/plain"].get_payload(decode=True)
        html = bodies["text/html"].get_payload(decode=True)
        assert isinstance(plain, bytes) and isinstance(html, bytes)
        assert "token=abc" in plain.decode()
        assert "<p>Open the link</p>" in html.decode()

    async def test_credentials_are_sent_when_configured(self) -> None:
        server = _Inbox(require_auth=True)
        await server.start()
        try:
            await _sender(server.port, username="mailer", password="s3cret").send(MESSAGE)
            assert server.login == ("mailer", "s3cret")
            assert len(server.messages) == 1
        finally:
            await server.stop()

    async def test_plain_text_only_stays_plain(self, inbox: _Inbox) -> None:
        await _sender(inbox.port).send(EmailMessage(to="a@b.test", subject="s", text_body="hi"))
        assert not inbox.messages[0].is_multipart()


class TestFailure:
    async def test_a_relay_refusal_is_a_delivery_error(self) -> None:
        server = _Inbox(reject_data=True)
        await server.start()
        try:
            with pytest.raises(EmailDeliveryError):
                await _sender(server.port).send(MESSAGE)
        finally:
            await server.stop()

    async def test_a_missing_login_is_a_delivery_error(self) -> None:
        server = _Inbox(require_auth=True)
        await server.start()
        try:
            with pytest.raises(EmailDeliveryError):
                await _sender(server.port).send(MESSAGE)
        finally:
            await server.stop()

    async def test_an_unreachable_relay_is_a_delivery_error(self) -> None:
        with pytest.raises(EmailDeliveryError):
            await _sender(1, timeout_seconds=1).send(MESSAGE)

    async def test_a_header_injection_attempt_is_refused_not_sent(self, inbox: _Inbox) -> None:
        hostile = EmailMessage(to="a@b.test", subject="hi\r\nBcc: evil@x.test", text_body="x")
        with pytest.raises(ValueError):
            await _sender(inbox.port).send(hostile)
        assert inbox.messages == []


class TestConfiguration:
    def test_smtp_requires_a_host(self) -> None:
        with pytest.raises(ValidationError, match="ORBIT_SMTP_HOST"):
            build_settings(email_provider=EmailProvider.SMTP)

    def test_a_username_needs_a_password_and_the_reverse(self) -> None:
        with pytest.raises(ValidationError, match="together"):
            build_settings(
                email_provider=EmailProvider.SMTP, smtp_host="mail.test", smtp_username="u"
            )

    def test_starttls_and_implicit_tls_are_exclusive(self) -> None:
        with pytest.raises(ValidationError, match="mutually exclusive"):
            build_settings(
                email_provider=EmailProvider.SMTP,
                smtp_host="mail.test",
                smtp_starttls=True,
                smtp_use_ssl=True,
            )

    def test_a_valid_relay_configuration_loads(self) -> None:
        settings = build_settings(
            email_provider=EmailProvider.SMTP,
            smtp_host="mail.test",
            smtp_username="mailer",
            smtp_password="hunter2-relay-secret",
        )
        assert settings.smtp_port == 587
        assert settings.smtp_password is not None
        assert "hunter2-relay-secret" not in repr(settings), "no password in a settings dump"
