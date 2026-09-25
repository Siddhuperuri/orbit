"""Password hashing.

Argon2id via `argon2-cffi`, chosen over PBKDF2 and bcrypt for memory hardness
(ADR-0003). Parameters are configuration, encoded into the hash string itself,
so raising them later does not invalidate existing hashes -- `needs_rehash`
detects the gap and the caller re-hashes transparently on the next successful
login.

This module is the only place `argon2` is imported. A hash is a `str`; nothing
above this layer needs to know which algorithm produced it.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Parameters follow OWASP's current Argon2id guidance for an interactive login
# path: memory cost dominates the attacker's cost, and 2 iterations at this
# memory cost keeps p50 latency low enough not to be noticeable.
_TIME_COST = 2
_MEMORY_COST_KIB = 19 * 1024
_PARALLELISM = 1

_hasher = PasswordHasher(
    time_cost=_TIME_COST, memory_cost=_MEMORY_COST_KIB, parallelism=_PARALLELISM
)


def hash_password(password: str) -> str:
    """Hash a password for storage. Never call this on anything but a fresh
    plaintext password -- hashing an already-hashed value is a no-op that
    looks like a bug for months."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-equivalent verification.

    `argon2-cffi` itself performs the constant-time comparison; the point of
    catching only these two exceptions is that anything else -- a malformed
    hash from data corruption, say -- should propagate as a defect rather than
    be silently treated as "wrong password".
    """
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """Whether this hash was produced with weaker-than-current parameters.

    Called only after a successful `verify_password`; rehashing on a failed
    login would let a caller probe parameter changes.
    """
    return _hasher.check_needs_rehash(password_hash)
