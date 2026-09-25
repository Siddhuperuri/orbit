"""Upload validation: the gate a file crosses before it becomes a document.

Everything in this module runs in the API process, before a single byte
reaches object storage, and treats its input as hostile:

* the **declared** filename may contain path separators, control characters,
  or nothing recognisable at all;
* the **declared** content type is whatever the client's HTTP stack put in a
  header, which is to say attacker-controlled and used for nothing but a log
  line;
* the **declared** extension may not match what the bytes actually are.

None of the three declared values is trusted for a decision. The extension
selects which magic-byte signature the content must match; the content itself
is what decides whether the upload proceeds. This mirrors ADR-0011's rule for
the browser's `Content-Type` header, applied to the filename's extension too.

Supported formats match ADR-0012's launch set exactly (PDF, Markdown, plain
text) -- there is no format this module accepts that the parser cannot later
consume.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass

from orbit.core.config import Settings

MAX_FILENAME_LENGTH = 255

#: Bytes inspected for a magic-byte match. Comfortably larger than the longest
#: signature checked below; deliberately small, because it is buffered in
#: memory for every upload regardless of the configured size ceiling.
SNIFF_WINDOW_BYTES = 64

_PDF_MAGIC = b"%PDF-"

# Extension -> the one content type it is allowed to declare. A closed map
# rather than a permissive parser: an extension not listed here is rejected
# before the request body is read at all, which is the cheapest possible
# rejection for the overwhelmingly common case of an unsupported format.
_EXTENSION_CONTENT_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}


#: The extensions an upload may carry, for clients that want to say so before a
#: byte is sent. Derived from the map above so the two cannot disagree.
ACCEPTED_EXTENSIONS: tuple[str, ...] = tuple(sorted(_EXTENSION_CONTENT_TYPES))


class UploadRejectedError(Exception):
    """Base for every validation failure raised in this module.

    `core` sits below `domain` in the layering (`backend/.importlinter`:
    `application > {infrastructure} > domain > core`), so this module cannot
    raise a `domain.errors` type directly -- the same constraint
    `core/tokens.py`'s `InvalidTokenError` exists to satisfy. Translation into
    a domain error (`ValidationError`, `UnsupportedContentTypeError`,
    `UploadTooLargeError`) happens one layer up, in
    `application/documents/upload_document.py`.
    """

    def __init__(self, message: str, /, **context: object) -> None:
        super().__init__(message)
        self.message = message
        #: Structured detail for the translated domain error's log context.
        self.context = context


class FilenameRejectedError(UploadRejectedError):
    """The filename is empty, unusable, or reduces to nothing after cleanup."""


class ContentTypeRejectedError(UploadRejectedError):
    """The extension is unsupported, or the content does not match it."""


class UploadSizeExceededError(UploadRejectedError):
    """The byte count crossed the configured ceiling mid-stream."""


def sanitize_filename(raw: str) -> str:
    """Produce a filename safe to store as data and to echo in a response header.

    This is **not** the path-traversal defence -- the storage key never
    contains any part of a filename, sanitized or not (`core/storage_keys.py`),
    so there is no path for a sanitized-but-imperfect value to traverse. This
    function exists for two narrower reasons: a filename becomes a
    `Content-Disposition` header on download, where a raw `\\r`/`\\n` would be a
    header-injection primitive; and it becomes a `VARCHAR(255)` column, which a
    long or control-character-laden value would either violate or pollute.
    """
    # A client may send a full path in the filename field (some browsers and
    # most naive HTTP clients do for a `<input type=file>` selection). Only the
    # final component is ever meaningful; anything before it is discarded
    # outright rather than escaped, because there is nothing downstream that
    # ever treats this value as a path for the escaping to protect.
    candidate = raw.replace("\\", "/").rsplit("/", 1)[-1]

    # NFC normalization before anything else: two byte-sequences that render
    # identically but compare unequal is a real source of confused duplicate
    # detection and confused users, independent of security.
    candidate = unicodedata.normalize("NFC", candidate)

    # Strip control characters (including CR/LF) rather than rejecting the
    # whole upload over them -- a client mangling a filename is common and
    # recoverable; refusing the file over it is not proportionate.
    candidate = "".join(ch for ch in candidate if unicodedata.category(ch) != "Cc").strip()

    if not candidate or candidate in {".", ".."}:
        msg = "Filename is missing or invalid."
        raise FilenameRejectedError(msg)

    if len(candidate.encode("utf-8")) > MAX_FILENAME_LENGTH:
        # Truncated in a way that preserves the extension: a name cut off
        # mid-extension would silently change what a duplicate detector or a
        # human skimming a list believes the file is.
        stem, _, ext = candidate.rpartition(".")
        ext_suffix = f".{ext}" if ext else ""
        budget = MAX_FILENAME_LENGTH - len(ext_suffix.encode("utf-8"))
        candidate = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + ext_suffix

    return candidate


def extension_of(filename: str) -> str:
    """The lowercased extension, including the dot, or "" if there is none."""
    _, _, ext = filename.rpartition(".")
    return f".{ext.lower()}" if ext and "." in filename else ""


def expected_content_type_for_extension(extension: str) -> str:
    """The one content type this extension is allowed to declare.

    Raises `ContentTypeRejectedError` for anything not in the launch set -- the
    same error a sniff mismatch raises, because a client should not be able to
    distinguish "wrong extension" from "wrong content" by response shape; both
    mean "ORBIT will not store this file."
    """
    content_type = _EXTENSION_CONTENT_TYPES.get(extension)
    if content_type is None:
        msg = "This file type is not supported. ORBIT accepts PDF, Markdown, and plain text."
        raise ContentTypeRejectedError(msg, extension=extension or "(none)")
    return content_type


def _looks_binary(head: bytes) -> bool:
    """Whether `head` fails to look like text.

    Markdown and plain text share no magic number -- both are simply UTF-8
    text, which is precisely why file-type sniffing tools (and browsers) do not
    distinguish them by content either. The signal that *is* real: legitimate
    text does not contain a NUL byte, and does decode as UTF-8. Binary content
    disguised with a `.txt` or `.md` extension -- an executable, an image, a
    PDF -- reliably fails one of the two.
    """
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def sniff_and_validate(head: bytes, *, extension: str) -> str:
    """Confirm `head` -- the first bytes of the upload -- matches `extension`.

    Returns the validated content type (identical to
    `expected_content_type_for_extension`'s return, since a mismatch raises
    instead of returning a different value). Called with the *content*, never
    the client's declared `Content-Type` header, which plays no part in this
    decision (ADR-0011).
    """
    expected = expected_content_type_for_extension(extension)

    if expected == "application/pdf":
        if not head.startswith(_PDF_MAGIC):
            msg = "This file's content does not match a PDF."
            raise ContentTypeRejectedError(msg, declared_extension=extension)
        return expected

    # text/markdown and text/plain: no magic number distinguishes them, so the
    # shared check is simply "this is not binary" (see `_looks_binary`).
    if _looks_binary(head):
        msg = "This file's content does not look like text."
        raise ContentTypeRejectedError(msg, declared_extension=extension)
    return expected


@dataclass
class _StreamStats:
    """Mutable result of consuming a validated upload stream.

    A plain dataclass rather than reading properties off the stream object
    itself, so the "has this been fully consumed yet" question has an honest
    answer: the stats exist and are populated only once, by the generator,
    after it raises `StopAsyncIteration` -- there is no property that could be
    read prematurely and return a stale zero.
    """

    byte_size: int = 0
    content_sha256: str | None = None
    content_type: str | None = None


async def _validate_and_hash(
    source: AsyncIterator[bytes],
    *,
    first_chunk: bytes,
    max_bytes: int,
    stats: _StreamStats,
) -> AsyncIterator[bytes]:
    """Wrap `source`, enforcing size as bytes pass through.

    `first_chunk` has already been pulled off `source` and sniffed by
    `prepare_upload` -- `stats.content_type` is set before this generator ever
    runs, which is what lets the *caller* pass a correct `content_type` to
    `ObjectStorage.put_stream` at the moment it is called, rather than only
    discovering it after the stream has been partly consumed. This generator's
    job is what is left: re-yield that first chunk, then count and hash every
    chunk as it passes, aborting the instant the size limit is crossed rather
    than after the stream ends -- an oversized upload is stopped mid-flight,
    not fully received and then rejected (ADR-0011).
    """
    hasher = hashlib.sha256()
    total = 0

    for chunk in (first_chunk,):
        total += len(chunk)
        if total > max_bytes:
            msg = f"File exceeds the {max_bytes}-byte upload limit."
            raise UploadSizeExceededError(msg, max_bytes=max_bytes)
        hasher.update(chunk)
        yield chunk

    async for chunk in source:
        total += len(chunk)
        if total > max_bytes:
            msg = f"File exceeds the {max_bytes}-byte upload limit."
            raise UploadSizeExceededError(msg, max_bytes=max_bytes)
        hasher.update(chunk)
        yield chunk

    stats.byte_size = total
    stats.content_sha256 = hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """Everything decided about an upload before storage is touched, plus the
    still-to-be-consumed byte stream and the stats it will populate."""

    sanitized_filename: str
    extension: str
    stream: AsyncIterator[bytes]
    stats: _StreamStats


async def _pull_first_chunk(source: AsyncIterator[bytes]) -> bytes:
    try:
        return await source.__anext__()
    except StopAsyncIteration:
        return b""


async def prepare_upload(
    raw_filename: str, source: AsyncIterator[bytes], *, settings: Settings
) -> ValidatedUpload:
    """Validate what can be validated before reading any bytes, sniff the
    first chunk, then wrap the remaining stream so size is enforced as the
    rest arrives.

    Filename and extension problems are rejected without reading a single byte
    of the body -- they are also the cheapest way to attack this endpoint at
    volume, so paying nothing for them matters. The content-type check reads
    exactly one chunk (bounded by whatever the transport's natural chunk size
    is, typically far smaller than the upload limit) before deciding whether
    to proceed at all, so a rejected upload never causes a connection to
    object storage to be opened.
    """
    filename = sanitize_filename(raw_filename)
    extension = extension_of(filename)
    # Fails fast on an unsupported extension before the stream is ever touched.
    expected_content_type_for_extension(extension)

    first_chunk = await _pull_first_chunk(source)
    if not first_chunk:
        msg = "The uploaded file is empty."
        raise ContentTypeRejectedError(msg, extension=extension)

    content_type = sniff_and_validate(first_chunk[:SNIFF_WINDOW_BYTES], extension=extension)
    stats = _StreamStats(content_type=content_type)

    wrapped = _validate_and_hash(
        source, first_chunk=first_chunk, max_bytes=settings.max_upload_bytes, stats=stats
    )
    return ValidatedUpload(
        sanitized_filename=filename, extension=extension, stream=wrapped, stats=stats
    )
