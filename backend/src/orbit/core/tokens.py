"""Token primitives: access-token JWTs and opaque refresh tokens.

Two different shapes for two different jobs (ADR-0003). The access token is a
signed JWT so it can be verified with no database round trip; it is never
persisted. The refresh token is 256 bits of randomness with no structure at
all -- it authenticates only by being looked up, and only its SHA-256 hash is
ever stored, so a database leak yields no usable credential.

Nothing here touches HTTP or cookies. That belongs to the API layer
(ADR-0009); this module only encodes, decodes, and hashes.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from orbit.core.clock import Clock, SystemClock

# Refresh tokens carry 256 bits of entropy from `secrets.token_urlsafe`, which
# encodes at roughly 4 bits per character -- 43 characters comfortably clears
# that floor with margin for the base64 rounding.
_REFRESH_TOKEN_BYTES = 32

_JWT_ALGORITHM = "HS256"

# Claim names kept short and deliberately non-standard-colliding where it
# matters: `epoch` is ORBIT-specific and has no JWT-registered meaning.
_CLAIM_SUBJECT = "sub"
_CLAIM_WORKSPACE = "wsid"
_CLAIM_EPOCH = "epoch"
_CLAIM_ISSUED_AT = "iat"
_CLAIM_EXPIRES_AT = "exp"


class InvalidTokenError(Exception):
    """A token failed to decode: expired, malformed, or wrongly signed.

    `core` sits below `domain` in the layering (backend/.importlinter) and may
    not raise a domain error. The caller -- an application-layer use case or
    an API dependency -- is what translates this into
    `AuthenticationRequiredError`, at the boundary where that translation
    belongs.
    """


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """The decoded, verified contents of an access token.

    `workspace_id` is deliberately absent: an access token authenticates a
    *user*, not a user-in-a-workspace. Which workspace a request concerns is
    named by the request path, and membership is re-checked against the
    database on every request (ADR-0004) -- baking a workspace into the token
    would let a membership revocation be ignored for up to the token's
    lifetime.
    """

    user_id: uuid.UUID
    token_epoch: int
    issued_at: datetime
    expires_at: datetime


def issue_access_token(
    *, user_id: uuid.UUID, token_epoch: int, secret_key: str, ttl: timedelta, clock: Clock
) -> str:
    """Mint a signed access token.

    `token_epoch` is embedded so that a password change or forced logout --
    both of which bump the epoch in the same statement (see
    infrastructure/db/repositories/users.py) -- invalidates every token issued
    before it without a database round trip: verification simply compares the
    embedded epoch to the current one.
    """
    now = clock.now()
    payload = {
        _CLAIM_SUBJECT: str(user_id),
        _CLAIM_EPOCH: token_epoch,
        _CLAIM_ISSUED_AT: now,
        _CLAIM_EXPIRES_AT: now + ttl,
    }
    return jwt.encode(payload, secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(
    token: str, *, secret_key: str, clock: Clock | None = None
) -> AccessTokenClaims:
    """Verify signature and expiry, and return the claims.

    Expiry is checked against `clock`, not against PyJWT's own built-in check
    (`verify_exp` is disabled below). PyJWT validates `exp` against real wall
    time with no hook for overriding it, which would make expiry untestable
    without an actual `sleep` -- exactly the flaky-test class `core/clock.py`
    exists to eliminate. `clock` defaults to the real clock in production and
    to a `FixedClock` in tests.

    Every failure mode -- expired, malformed, wrong signature -- collapses to
    the same `InvalidTokenError`. Distinguishing them to the end user would say
    more about *why* their credential failed than they need to act on it, for
    no benefit: the remedy is identical in every case (log in again).
    """
    clock = clock or SystemClock()

    try:
        payload = jwt.decode(
            token, secret_key, algorithms=[_JWT_ALGORITHM], options={"verify_exp": False}
        )
    except jwt.PyJWTError as exc:
        msg = "token failed to decode or verify"
        raise InvalidTokenError(msg) from exc

    try:
        claims = AccessTokenClaims(
            user_id=uuid.UUID(payload[_CLAIM_SUBJECT]),
            token_epoch=int(payload[_CLAIM_EPOCH]),
            issued_at=_as_datetime(payload[_CLAIM_ISSUED_AT]),
            expires_at=_as_datetime(payload[_CLAIM_EXPIRES_AT]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        # A syntactically valid, correctly signed token with the wrong shape of
        # claims. Only reachable if the signing key were ever shared with
        # something producing a different token shape, but the fallback must
        # still be the same generic error rather than a 500.
        msg = "token claims have an unexpected shape"
        raise InvalidTokenError(msg) from exc

    if claims.expires_at <= clock.now():
        msg = "token has expired"
        raise InvalidTokenError(msg)

    return claims


def _as_datetime(value: object) -> datetime:
    """Convert a decoded `iat`/`exp` claim back into an aware `datetime`.

    `jwt.encode` accepts a `datetime` for these claims and silently converts
    it to a POSIX timestamp on the wire; `jwt.decode` does *not* convert it
    back -- it returns the raw number. Both directions have to be handled
    here, or every decoded token's `issued_at`/`expires_at` is the wrong type.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    msg = "time claim was not a number or a datetime"
    raise InvalidTokenError(msg)


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    """A freshly minted refresh token, in both forms it is needed.

    `plaintext` goes into the response cookie and nowhere else. `sha256_hex` is
    what gets stored; the two are never both persisted, which is the entire
    point of hashing it.
    """

    plaintext: str
    sha256_hex: str


def issue_refresh_token() -> IssuedRefreshToken:
    plaintext = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    return IssuedRefreshToken(plaintext=plaintext, sha256_hex=hash_refresh_token(plaintext))


def hash_refresh_token(plaintext: str) -> str:
    """SHA-256 is sufficient here, unlike for passwords.

    A refresh token already carries 256 bits of uniform entropy -- there is no
    low-entropy secret to slow down brute-forcing, which is what a memory-hard
    function like Argon2 exists to do. The hash's only job is to make a
    database read insufficient to obtain a usable credential.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
