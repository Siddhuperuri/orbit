"""PDF parser, on pypdf.

PDFs are the hostile format. They are programs for placing glyphs, not
documents with paragraphs, and malformed ones are common and sometimes
deliberate. Three rules follow:

* **Every library exception is translated.** pypdf raises a zoo of exception
  types -- its own, plus `KeyError`, `ValueError`, `RecursionError` from deep in
  a malformed object graph. Any of them escaping would be classified as a defect
  in ORBIT; they are properties of the file, so they become
  `DocumentCorruptError` with a user-safe message and the original kept as
  operator context.
* **One bad page does not sink the document**, but a document where every page
  fails is corrupt, not empty.
* **Limits are checked as text accumulates**, not after, so a decompression bomb
  is stopped at the character ceiling rather than after allocating all of it.
  pypdf's own decompression limits (`LimitReachedError`) map to the same error.

Paragraph and heading detection from extracted text is heuristic -- a PDF text
layer has lines, not paragraphs. The heuristics are deliberately conservative:
a missed paragraph break costs a slightly worse chunk boundary; a false heading
would stamp the wrong section onto every chunk under it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import IO

from pypdf import PasswordType, PdfReader
from pypdf.errors import FileNotDecryptedError, LimitReachedError

from orbit.core.logging import get_logger
from orbit.domain.errors import (
    DocumentCorruptError,
    DocumentEmptyError,
    DocumentEncryptedError,
    DocumentLimitExceededError,
)
from orbit.domain.processing.content import (
    BlockKind,
    DocumentFormat,
    ExtractedBlock,
    ExtractedDocument,
    ParseLimits,
    SourceLocation,
)

logger = get_logger(__name__)

_CORRUPT_MESSAGE = "This PDF appears to be damaged and could not be read."
_ENCRYPTED_MESSAGE = "This PDF is password-protected. Remove the password and upload it again."
_NO_PAGES_MESSAGE = "This PDF has no pages."

_TERMINAL = re.compile(r"[.!?:;\"'\u201d\u2019)\]]$")
_BULLET = re.compile(
    # a bullet glyph or dash ...
    r"^\s*(?:[\u2022\u25cf\u25aa\u25e6\u2023\u2219\u00b7\-\u2013\u2014*]"
    # ... or "1." / "2)" / "a)" list markers.
    r"|\(?[0-9]{1,2}[.)]|\(?[a-z][.)])\s+"
)
#: "1 Introduction", "2.3 Token rotation", "Chapter 4 Results". Deliberately
#: requires a capitalised title and no sentence punctuation.
_NUMBERED_HEADING = re.compile(
    r"^((?i:chapter|section|part)\s+)?(\d{1,2}(?:\.\d{1,2}){0,3})(\.?)\s+([A-Z][^.!?]{1,78})$"
)
_MAX_METADATA_LENGTH = 256
_MAX_HEADING_WORDS = 12


class PdfParser:
    content_types = frozenset({"application/pdf"})

    def parse(self, source: IO[bytes], *, limits: ParseLimits) -> ExtractedDocument:
        reader = _open(source)
        page_count = _page_count(reader)
        if page_count == 0:
            raise DocumentEmptyError(_NO_PAGES_MESSAGE)
        if page_count > limits.max_pages:
            msg = (
                f"This PDF has {page_count:,} pages. ORBIT can process documents of up to "
                f"{limits.max_pages:,} pages."
            )
            raise DocumentLimitExceededError(msg, pages=page_count)

        blocks: list[ExtractedBlock] = []
        characters = 0
        failed_pages: list[int] = []
        empty_pages = 0

        for number in range(1, page_count + 1):
            text = _page_text(reader, number, failed_pages)
            if text is None:
                continue
            characters += len(text)
            if characters > limits.max_characters:
                msg = (
                    f"This PDF contains too much text to process. The limit is "
                    f"{limits.max_characters:,} characters."
                )
                raise DocumentLimitExceededError(msg, pages_read=number)
            page_blocks = list(_segment_page(text, number))
            if not page_blocks:
                empty_pages += 1
            blocks.extend(page_blocks)
            if len(blocks) > limits.max_blocks:
                msg = "This PDF has too many sections to process."
                raise DocumentLimitExceededError(msg, blocks=len(blocks))

        if failed_pages and len(failed_pages) == page_count:
            raise DocumentCorruptError(_CORRUPT_MESSAGE, failed_pages=len(failed_pages))

        warnings: list[str] = []
        if failed_pages:
            warnings.append(f"pdf:unreadable_pages={len(failed_pages)}")
        if empty_pages:
            warnings.append(f"pdf:pages_without_text={empty_pages}")
        return ExtractedDocument(
            format=DocumentFormat.PDF,
            blocks=tuple(blocks),
            page_count=page_count,
            metadata=_metadata(reader),
            warnings=tuple(warnings),
        )


def _open(source: IO[bytes]) -> PdfReader:
    try:
        reader = PdfReader(source, strict=False)
    except LimitReachedError as exc:
        msg = "This PDF is too complex to process."
        raise DocumentLimitExceededError(msg, detail=str(exc)[:200]) from exc
    except Exception as exc:
        raise DocumentCorruptError(_CORRUPT_MESSAGE, detail=_describe(exc)) from exc

    try:
        encrypted = reader.is_encrypted
    except Exception as exc:
        raise DocumentCorruptError(_CORRUPT_MESSAGE, detail=_describe(exc)) from exc
    if encrypted:
        # Many PDFs are "encrypted" with an empty user password purely to set
        # permission flags; those open without a password in every viewer and
        # should here too. Anything else is genuinely protected.
        try:
            unlocked = reader.decrypt("") is not PasswordType.NOT_DECRYPTED
        except Exception as exc:
            raise DocumentEncryptedError(_ENCRYPTED_MESSAGE, detail=_describe(exc)) from exc
        if not unlocked:
            raise DocumentEncryptedError(_ENCRYPTED_MESSAGE)
    return reader


def _page_count(reader: PdfReader) -> int:
    try:
        return len(reader.pages)
    except FileNotDecryptedError as exc:
        raise DocumentEncryptedError(_ENCRYPTED_MESSAGE) from exc
    except LimitReachedError as exc:
        msg = "This PDF is too complex to process."
        raise DocumentLimitExceededError(msg, detail=str(exc)[:200]) from exc
    except Exception as exc:
        raise DocumentCorruptError(_CORRUPT_MESSAGE, detail=_describe(exc)) from exc


def _page_text(reader: PdfReader, number: int, failed_pages: list[int]) -> str | None:
    try:
        return reader.pages[number - 1].extract_text(extraction_mode="plain") or ""
    except LimitReachedError as exc:
        msg = "This PDF is too complex to process."
        raise DocumentLimitExceededError(msg, page=number, detail=str(exc)[:200]) from exc
    except MemoryError as exc:
        msg = "This PDF is too complex to process."
        raise DocumentLimitExceededError(msg, page=number) from exc
    except Exception as exc:
        # A single malformed content stream should not discard every other
        # page; the operator still hears about it.
        failed_pages.append(number)
        logger.warning("pdf.page_unreadable", page=number, detail=_describe(exc))
        return None


def _segment_page(text: str, page: int) -> Iterator[ExtractedBlock]:
    """Group a page's extracted lines into blocks.

    A new block starts at a blank line, at a bullet, around a heading-like
    line, and after a short line that ends a sentence (the ragged last line of
    a paragraph). Everything else is a wrapped continuation line.
    """
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    widths = sorted(len(line) for line in lines if line.strip())
    if not widths:
        return
    # The typical full line width: long enough that a wrapped line is not
    # mistaken for a paragraph's short last line.
    full_width = max(40, widths[int(len(widths) * 0.8)])

    kind = BlockKind.PARAGRAPH
    buffer: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            yield from _flush(buffer, kind, page)
            buffer, kind = [], BlockKind.PARAGRAPH
            continue

        heading_level = _heading_level(stripped)
        if heading_level is not None:
            yield from _flush(buffer, kind, page)
            yield ExtractedBlock(
                kind=BlockKind.HEADING,
                text=stripped,
                location=SourceLocation(page=page),
                heading_level=heading_level,
            )
            buffer, kind = [], BlockKind.PARAGRAPH
            continue

        if _BULLET.match(stripped):
            yield from _flush(buffer, kind, page)
            buffer, kind = [stripped], BlockKind.LIST_ITEM
            continue

        buffer.append(stripped)
        if len(stripped) < full_width * 0.7 and _TERMINAL.search(stripped):
            yield from _flush(buffer, kind, page)
            buffer, kind = [], BlockKind.PARAGRAPH

    yield from _flush(buffer, kind, page)


def _flush(buffer: list[str], kind: BlockKind, page: int) -> Iterator[ExtractedBlock]:
    if buffer:
        yield ExtractedBlock(kind=kind, text="\n".join(buffer), location=SourceLocation(page=page))


def _heading_level(line: str) -> int | None:
    match = _NUMBERED_HEADING.match(line)
    if match is None or len(line.split()) > _MAX_HEADING_WORDS:
        return None
    prefix, number, trailing_dot, _title = match.groups()
    # "1. Buy milk" is a numbered list item; "1 Introduction", "2.3 Scope",
    # and "Chapter 4 Results" are headings. A single-level number with a
    # trailing dot is ambiguous, and the conservative reading is the list.
    if not prefix and "." not in number and trailing_dot:
        return None
    return min(number.count(".") + 1, 6)


def _metadata(reader: PdfReader) -> dict[str, str]:
    """Title and author, advisory and bounded. A malformed info dictionary is
    not worth failing a readable document over."""
    try:
        info = reader.metadata
    except Exception:
        return {}
    if info is None:
        return {}
    metadata: dict[str, str] = {}
    for key, value in (("title", info.title), ("author", info.author)):
        if isinstance(value, str):
            cleaned = "".join(char for char in value if char.isprintable()).strip()
            if cleaned:
                metadata[key] = cleaned[:_MAX_METADATA_LENGTH]
    return metadata


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__qualname__}: {str(exc)[:300]}"
