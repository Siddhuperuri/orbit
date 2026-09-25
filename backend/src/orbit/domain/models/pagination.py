"""Keyset pagination.

Offset pagination is rejected outright (ADR-0010): `OFFSET n` makes PostgreSQL
scan and discard `n` rows, so page 500 costs 500 pages of work, and concurrent
inserts shift the window so a user paging through documents sees duplicates and
misses rows.

A keyset cursor names the last row seen -- here `(created_at, id)` -- and the
next page is "everything strictly before that". Cost is constant and the result
is stable under concurrent writes.

Cursors are **signed**. Without a signature a client could craft one to reorder
results or to page outside the filter it was issued for; with one, a tampered
cursor is rejected rather than trusted.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

from orbit.domain.errors import BadRequestError

# Server-enforced ceiling. Without it, `limit=1000000` is a denial-of-service
# vector that looks like an ordinary request.
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

# Truncated HMAC-SHA256; 128 bits is ample for a short-lived pagination token.
_SIGNATURE_BYTES = 16


def _seal(data: dict[str, str], secret: str) -> str:
    """Serialise and sign a cursor payload."""
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    return _b64encode(payload + b"." + signature)


def _unseal(value: str, secret: str) -> dict[str, Any]:
    """Verify a cursor's signature and return its payload, or raise the one
    uninformative error every kind of forgery gets."""
    try:
        raw = _b64decode(value)
    except (ValueError, binascii.Error) as exc:
        raise _invalid_cursor() from exc
    # Split by position, never by searching for the separator: the
    # signature is raw HMAC bytes, and any of them can be 0x2E ("."). A
    # separator search then cuts inside the signature and a perfectly
    # valid cursor is rejected as forged -- about 6% of all cursors.
    if len(raw) <= _SIGNATURE_BYTES or raw[-_SIGNATURE_BYTES - 1 : -_SIGNATURE_BYTES] != b".":
        raise _invalid_cursor()
    payload, signature = raw[: -_SIGNATURE_BYTES - 1], raw[-_SIGNATURE_BYTES:]

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    # Constant-time: a timing-variable comparison would let a client probe
    # for a valid signature byte by byte.
    if not hmac.compare_digest(signature, expected):
        raise _invalid_cursor()

    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise _invalid_cursor() from exc
    if not isinstance(data, dict):
        raise _invalid_cursor()
    return data


@dataclass(frozen=True, slots=True)
class Cursor:
    """Position of the last row on the previous page."""

    created_at: datetime
    row_id: uuid.UUID

    def encode(self, secret: str) -> str:
        return _seal(
            {"t": self.created_at.astimezone(UTC).isoformat(), "i": str(self.row_id)}, secret
        )

    @classmethod
    def decode(cls, value: str, secret: str) -> Self:
        data = _unseal(value, secret)
        try:
            return cls(
                created_at=datetime.fromisoformat(data["t"]),
                row_id=uuid.UUID(data["i"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise _invalid_cursor() from exc


@dataclass(frozen=True, slots=True)
class SortCursor:
    """Position within a list ordered by something other than creation time.

    Carries the *sort it belongs to* inside the signed payload. Without that, a
    cursor minted under "title A-Z" could be replayed under "newest first", and
    the row-value comparison would silently compare a title against a
    timestamp -- returning nonsense rather than an error. Decoding therefore
    demands the sort it is about to be used for.

    `value` is whatever the database computed as the sort key for the last row
    (a lowercased title, an ISO timestamp), never a value the application
    recomputed: Python's and PostgreSQL's notion of "lowercase" disagree for
    some scripts, and a key that differs by one character skips or repeats a
    row at every page boundary.
    """

    sort: str
    value: str
    row_id: uuid.UUID

    def encode(self, secret: str) -> str:
        return _seal({"s": self.sort, "v": self.value, "i": str(self.row_id)}, secret)

    @classmethod
    def decode(cls, value: str, secret: str, *, sort: str) -> Self:
        data = _unseal(value, secret)
        try:
            cursor = cls(sort=str(data["s"]), value=str(data["v"]), row_id=uuid.UUID(data["i"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise _invalid_cursor() from exc
        if cursor.sort != sort:
            raise _invalid_cursor()
        return cursor


@dataclass(frozen=True, slots=True)
class Page:
    """One page of results plus the cursor for the next.

    `next_cursor is None` means this is the last page. There is deliberately no
    total count: computing one requires a second full scan on every request, and
    it is stale the moment it is returned.
    """

    items: tuple[Any, ...]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


def clamp_limit(requested: int | None) -> int:
    """Bound a client-supplied page size."""
    if requested is None:
        return DEFAULT_PAGE_SIZE
    if requested < 1:
        msg = "limit must be at least 1."
        raise BadRequestError(msg)
    return min(requested, MAX_PAGE_SIZE)


def _invalid_cursor() -> BadRequestError:
    # Deliberately uninformative: distinguishing "malformed" from "bad
    # signature" would tell an attacker which half of a forgery attempt failed.
    return BadRequestError("The pagination cursor is not valid.")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
