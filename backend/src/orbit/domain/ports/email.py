"""Outbound email port.

Password reset and email verification both need to deliver a secret to an
address, and neither is meaningful without real delivery. This port exists so
that the *architecture* around those flows can be built, tested, and reviewed
now, while the choice of provider (SES, Postmark, SMTP relay) stays a later,
separate decision -- one that depends on deliverability and data-residency
questions that are not this module's to answer.

There is deliberately no fake or "pretend it worked" implementation. A sender
that silently swallows mail is worse than none: it makes a broken reset flow
look healthy in every environment until a real user needs it. The shipped
adapter refuses loudly instead (see infrastructure/email/).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One message to one recipient.

    Bodies are pre-rendered by the caller. Templating belongs above this port:
    a provider adapter's job is delivery, and giving it opinions about content
    is what makes providers hard to swap.
    """

    to: str
    subject: str
    text_body: str
    #: Optional HTML alternative. Plain text is mandatory because some clients
    #: and most security-conscious users never render HTML.
    html_body: str | None = None


class EmailDeliveryError(Exception):
    """Delivery failed.

    A `core`/`domain`-level exception rather than an `OrbitError`: whether a
    failure to send is fatal depends entirely on the calling flow. Password
    reset treats it as non-fatal (the response must not change), while an
    operator-triggered send would want it surfaced.
    """


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Deliver `message`, or raise `EmailDeliveryError`.

        Implementations must not swallow failures. The caller decides what a
        failure means; an adapter that decides for them removes the only
        signal anyone has that mail is not arriving.
        """
        ...
