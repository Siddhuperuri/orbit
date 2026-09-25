"""Folders and tags: the rules that a column constraint cannot express.

The numeric bounds below are *product* limits, not schema limits. They exist so
that every list a screen renders is bounded by construction: the folder tree and
the tag list are each returned whole (a tree cannot be paged), so "how many can
there be" has to have an answer.

Where a bound is also a database constraint it is said so, and the two are kept
equal by a test rather than by care.
"""

from __future__ import annotations

import unicodedata
from typing import Final

from orbit.domain.errors import ValidationError

#: Mirrors `ck_folders_depth_within_bounds`. Deeper trees make the recursive
#: queries the schema allows for unbounded.
MAX_FOLDER_DEPTH: Final = 16
MAX_FOLDER_NAME_LENGTH: Final = 255
MAX_FOLDERS_PER_WORKSPACE: Final = 500

MAX_TAG_NAME_LENGTH: Final = 64
MAX_TAGS_PER_WORKSPACE: Final = 200
#: A document carrying more labels than this is not being organised, it is being
#: annotated; the chip row would also stop fitting on a phone.
MAX_TAGS_PER_DOCUMENT: Final = 20

#: The tag palette, as theme *tones* rather than hex values. Stored as a token so
#: a theme change never rewrites rows, and limited to tones the design system
#: already verifies for contrast in both themes -- an arbitrary colour picker
#: would let a user create a tag nobody can read.
TAG_COLORS: Final[tuple[str, ...]] = ("neutral", "accent", "success", "warning", "danger")
DEFAULT_TAG_COLOR: Final = "neutral"


def normalize_name(value: str, *, label: str, max_length: int) -> str:
    """Canonicalise a folder or tag name, or refuse it.

    Unicode-normalised (so "é" typed two ways is one name -- the uniqueness
    index compares the stored bytes) and whitespace-collapsed (so "Q3  plan" and
    "Q3 plan" cannot coexist as visually identical siblings). Control
    characters are refused rather than stripped: silently altering a name the
    user typed is a worse surprise than telling them.
    """
    cleaned = " ".join(unicodedata.normalize("NFC", value).split())
    if not cleaned:
        msg = f"{label} cannot be blank."
        raise ValidationError(msg)
    if len(cleaned) > max_length:
        msg = f"{label} cannot exceed {max_length} characters."
        raise ValidationError(msg)
    if any(unicodedata.category(character) == "Cc" for character in cleaned):
        msg = f"{label} cannot contain control characters."
        raise ValidationError(msg)
    return cleaned


def normalize_folder_name(value: str) -> str:
    return normalize_name(value, label="A folder name", max_length=MAX_FOLDER_NAME_LENGTH)


def normalize_tag_name(value: str) -> str:
    return normalize_name(value, label="A tag name", max_length=MAX_TAG_NAME_LENGTH)


def require_tag_color(value: str | None) -> str:
    """`None` means "the default"; anything else must be in the palette."""
    if value is None:
        return DEFAULT_TAG_COLOR
    if value not in TAG_COLORS:
        msg = f"Tag colour must be one of: {', '.join(TAG_COLORS)}."
        raise ValidationError(msg)
    return value
