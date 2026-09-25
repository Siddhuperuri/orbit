"""Identifier generation.

Correlation identifiers are ULIDs rather than UUIDv4 because their leading 48
bits are a millisecond timestamp: identifiers sort chronologically as strings,
so a log viewer ordering by ``request_id`` shows events in the order they
happened. UUIDv4 sorts randomly and gives up that property for nothing.

ULID is implemented here rather than pulled in as a dependency. The
specification is a timestamp plus 80 random bits in Crockford base32; the code
below is the whole of it, and a third-party package for twenty lines is not a
trade worth making (see docs/decisions/0008-npm-task-runner.md for the same
reasoning applied elsewhere).
"""

from __future__ import annotations

import secrets
import time
import uuid

# Crockford base32: excludes I, L, O, and U so that hand-transcribed identifiers
# cannot be confused with 1, 0, or a profanity.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_TIMESTAMP_CHARS = 10
_RANDOM_CHARS = 16
ULID_LENGTH = _TIMESTAMP_CHARS + _RANDOM_CHARS

_TIMESTAMP_BITS = 48
_RANDOM_BITS = 80


def _encode(value: int, length: int) -> str:
    chars = [""] * length
    for index in range(length - 1, -1, -1):
        chars[index] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_ulid() -> str:
    """Return a 26-character, lexicographically sortable identifier."""
    timestamp_ms = time.time_ns() // 1_000_000
    # Wraps in the year 10889; masking keeps the encoding total rather than
    # producing a 27-character value at some far-future boundary.
    timestamp = timestamp_ms & ((1 << _TIMESTAMP_BITS) - 1)
    randomness = secrets.randbits(_RANDOM_BITS)
    return _encode(timestamp, _TIMESTAMP_CHARS) + _encode(randomness, _RANDOM_CHARS)


def is_ulid(value: str) -> bool:
    """Whether ``value`` is well-formed.

    Used to validate a client-supplied ``X-Request-ID`` before it is echoed into
    logs: an unvalidated header would let a caller inject newlines or arbitrary
    length into every log record for that request.
    """
    return len(value) == ULID_LENGTH and all(char in _ALPHABET for char in value)


# ---------------------------------------------------------------------------
# UUIDv7 -- primary keys
# ---------------------------------------------------------------------------
#
# Primary keys are UUIDv7 rather than UUIDv4 for one concrete reason: index
# locality. A v4 key is uniformly random, so every insert lands on a random leaf
# of the B-tree, dirtying a new page each time and fragmenting the index. A v7
# key carries a millisecond timestamp in its leading 48 bits, so inserts append
# to the right-hand edge -- the same access pattern a sequence produces, without
# a sequence's coordination or its enumerable, guessable values.
#
# It also makes `ORDER BY id` a meaningful chronological ordering, which is what
# lets keyset pagination use the primary key as its tiebreaker.
#
# PostgreSQL gained a native `uuidv7()` in 18 and Python gains `uuid.uuid7()` in
# 3.14; ORBIT targets PostgreSQL 17 and Python 3.11-3.12, so generation happens
# here. It is deliberately application-side regardless: the identifier is needed
# before the INSERT, so that a caller can build a whole object graph in memory
# and write it in one statement.

_UUID7_VERSION = 0x7
_UUID7_VARIANT_RFC4122 = 0b10


def new_uuid7() -> uuid.UUID:
    """Return a time-ordered UUID (RFC 9562 version 7).

    Layout: 48 bits of Unix milliseconds, 4 version bits, 12 random bits,
    2 variant bits, 62 random bits.
    """
    timestamp_ms = time.time_ns() // 1_000_000

    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= _UUID7_VERSION << 76
    value |= secrets.randbits(12) << 64
    value |= _UUID7_VARIANT_RFC4122 << 62
    value |= secrets.randbits(62)

    return uuid.UUID(int=value)


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Extract the embedded millisecond timestamp.

    Useful when reconstructing a timeline from identifiers alone -- for example
    while investigating rows whose ``created_at`` is suspect.
    """
    if value.version != _UUID7_VERSION:
        msg = f"not a UUIDv7: version is {value.version}"
        raise ValueError(msg)
    return value.int >> 80
