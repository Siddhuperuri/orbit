"""Validation utilities shared across request schemas.

Pydantic validates *shape* (a string is present, a field is required). These
functions validate the domain-level rules that shape checking cannot express,
and they exist here -- rather than as ad hoc regexes copied into each schema --
so that a rule like "what a slug may contain" has exactly one definition to
keep in sync with the database check constraint that is the actual source of
truth (`ck_workspaces_slug_format`).

No dependency is added for this (no `email-validator`, no `python-slugify`):
the rules below are simple, stable, and already have to match a hand-written
SQL check constraint byte-for-byte, so a library's more elaborate notion of
"valid" would drift from ours regardless.
"""

from __future__ import annotations

import re

# Deliberately permissive: the receiving side of an email address is not this
# application's business to validate strictly. RFC 5322 has edge cases no
# regex captures correctly, so this rejects only what is unambiguously wrong
# and leaves the rest to delivery -- an address that does not exist still
# "looks like" one, and confirmation email is the real validator.
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_EMAIL_LENGTH = 320

# Mirrors ck_workspaces_slug_format exactly. A slug ends up in a URL path, so
# its charset is a routing concern as much as a data-quality one.
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

MIN_PASSWORD_LENGTH = 12
# NIST SP 800-63B recommends a length ceiling rather than complexity rules, to
# stop a hashing function being handed gigabytes of input as a denial-of-service.
MAX_PASSWORD_LENGTH = 256


def normalize_email(value: str) -> str:
    """Validate shape and trim incidental whitespace. Preserves case: lookups
    are case-insensitive (see `uq_users_email_lower`), but the address a
    person typed is what correspondence should be sent to."""
    cleaned = value.strip()
    if not cleaned or len(cleaned) > MAX_EMAIL_LENGTH or not _EMAIL_PATTERN.match(cleaned):
        msg = "Enter a valid email address."
        raise ValueError(msg)
    return cleaned


def require_non_blank(value: str, *, field_name: str = "value", max_length: int = 512) -> str:
    """Reject empty-after-trimming strings, which pass a naive `min_length=1`
    check by being pure whitespace."""
    cleaned = value.strip()
    if not cleaned:
        msg = f"{field_name} cannot be blank."
        raise ValueError(msg)
    if len(cleaned) > max_length:
        msg = f"{field_name} cannot exceed {max_length} characters."
        raise ValueError(msg)
    return cleaned


def validate_slug(value: str) -> str:
    cleaned = value.strip().lower()
    if not _SLUG_PATTERN.match(cleaned):
        msg = (
            "Must be 3-64 characters: lowercase letters, digits, and hyphens, "
            "starting and ending with a letter or digit."
        )
        raise ValueError(msg)
    return cleaned


def validate_password(value: str) -> str:
    """Length only. Composition rules (require a digit, a symbol, ...) are a
    documented anti-pattern: they push users toward predictable substitutions
    and do not measurably resist offline attack the way length does."""
    if len(value) < MIN_PASSWORD_LENGTH:
        msg = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        raise ValueError(msg)
    if len(value) > MAX_PASSWORD_LENGTH:
        msg = f"Password cannot exceed {MAX_PASSWORD_LENGTH} characters."
        raise ValueError(msg)
    return value
