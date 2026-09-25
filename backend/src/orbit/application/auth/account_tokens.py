"""Issuing and redeeming one-time account tokens.

Shared by password reset and email verification, which differ only in what
redemption does. The generation and hashing rules are identical to refresh
tokens (ADR-0003): 256 bits of `secrets` entropy, stored only as a SHA-256
hash, so a database read yields nothing redeemable.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from orbit.core.clock import Clock
from orbit.domain.models.entities import AccountTokenPurpose
from orbit.domain.ports.unit_of_work import UnitOfWork

# Matches the refresh-token entropy budget. These end up in a URL, so
# `token_urlsafe` avoids any percent-encoding surprises in a mail client that
# rewrites links.
_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class IssuedAccountToken:
    """The two forms of one token.

    `plaintext` goes into exactly one email and is never stored or logged;
    `sha256_hex` is what the row holds.
    """

    plaintext: str
    sha256_hex: str


def hash_account_token(plaintext: str) -> str:
    """SHA-256, not Argon2 -- and for the same reason as refresh tokens.

    The value already carries 256 bits of uniform entropy, so there is no
    low-entropy secret for a memory-hard function to protect. The hash's only
    job is making a database read insufficient to redeem anything.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TokenRequest:
    """What issuing one account token needs."""

    user_id: uuid.UUID
    purpose: AccountTokenPurpose
    ttl: timedelta
    clock: Clock
    client_ip: str | None = None


async def issue_account_token(uow: UnitOfWork, request: TokenRequest) -> IssuedAccountToken:
    """Mint a token, superseding any outstanding one of the same purpose.

    Superseding matters: without it, every reset email ever sent stays live
    until its own expiry, so an old message recovered from a compromised
    mailbox still takes over the account. Requesting a new reset must
    invalidate the last one.
    """
    await uow.account_tokens.invalidate_outstanding(
        user_id=request.user_id, purpose=request.purpose
    )

    plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
    token_hash = hash_account_token(plaintext)

    await uow.account_tokens.issue(
        user_id=request.user_id,
        purpose=request.purpose,
        token_hash=token_hash,
        expires_at=request.clock.now() + request.ttl,
        client_ip=request.client_ip,
    )
    return IssuedAccountToken(plaintext=plaintext, sha256_hex=token_hash)
