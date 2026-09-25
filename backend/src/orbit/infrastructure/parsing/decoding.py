"""Bytes to text for the text-based formats (Markdown, plain text).

Encoding is guessed conservatively, and a guess that produces garbage is
refused rather than indexed. Silently decoding a UTF-16 file as Latin-1 yields
text that looks like `ÿþH\x00e\x00l\x00l\x00o` -- it embeds, it is searchable,
and it answers nothing.

Order of attempts:

1. A byte-order mark is authoritative (UTF-8, UTF-16 LE/BE).
2. BOM-less UTF-16, only when the NUL-byte pattern says so. (Checked before
   UTF-8 because NUL bytes are valid UTF-8.)
3. Strict UTF-8 -- by far the most common, and self-validating: random
   non-UTF-8 bytes almost never form valid UTF-8 sequences.
4. Windows-1252, the realistic legacy encoding for Western text files. It
   leaves five byte values undefined, so it rejects some binary outright.

Whatever decodes is then checked for control characters; text that is mostly
control characters is binary that happened to decode.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

from orbit.domain.errors import DocumentEncodingError

_UNREADABLE_MESSAGE = (
    "This file's text encoding could not be recognised. Save it as UTF-8 and upload it again."
)

#: Above this fraction of control characters the "text" is binary.
_MAX_CONTROL_RATIO = 0.05
#: Above this fraction of NULs in alternate positions, BOM-less UTF-16 is likely.
_UTF16_NUL_RATIO = 0.3

_ALLOWED_CONTROLS = frozenset("\n\r\t\f\v")


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str


def decode_text(data: bytes) -> DecodedText:
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            return _checked(_decode(data, encoding), encoding)

    # Before UTF-8, not after: NUL is a valid UTF-8 byte, so BOM-less UTF-16
    # "decodes" as UTF-8 into text that is half NUL characters.
    utf16 = _bomless_utf16_encoding(data)
    if utf16 is not None:
        return _checked(_decode(data, utf16), utf16)

    try:
        return _checked(data.decode("utf-8"), "utf-8")
    except UnicodeDecodeError:
        pass

    try:
        return _checked(data.decode("cp1252"), "cp1252")
    except UnicodeDecodeError as exc:
        raise DocumentEncodingError(_UNREADABLE_MESSAGE, attempted="cp1252") from exc


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode(data: bytes, encoding: str) -> str:
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise DocumentEncodingError(_UNREADABLE_MESSAGE, attempted=encoding) from exc


def _bomless_utf16_encoding(data: bytes) -> str | None:
    sample = data[:4096]
    if len(sample) < 4:  # noqa: PLR2004
        return None
    even_nuls = sample[0::2].count(0) / len(sample[0::2])
    odd_nuls = sample[1::2].count(0) / len(sample[1::2])
    if odd_nuls > _UTF16_NUL_RATIO and even_nuls < _UTF16_NUL_RATIO / 3:
        return "utf-16-le"
    if even_nuls > _UTF16_NUL_RATIO and odd_nuls < _UTF16_NUL_RATIO / 3:
        return "utf-16-be"
    return None


def _checked(text: str, encoding: str) -> DecodedText:
    if text:
        controls = sum(
            1
            for char in text
            if ord(char) < 32 and char not in _ALLOWED_CONTROLS  # noqa: PLR2004
        )
        if controls / len(text) > _MAX_CONTROL_RATIO:
            raise DocumentEncodingError(
                _UNREADABLE_MESSAGE, encoding=encoding, control_characters=controls
            )
    return DecodedText(text=text, encoding=encoding)
