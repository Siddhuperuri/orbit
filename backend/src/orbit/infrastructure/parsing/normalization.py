"""The shared normalization stage (ADR-0012).

Runs once, for every format, between parsing and chunking. Doing this per
parser guarantees the PDF and Markdown paths drift, which shows up as chunks
that embed differently for identical text.

In order:

1. **Running headers and footers** are removed from paged documents: a line
   that opens or closes most pages ("Quarterly Report -- Confidential",
   "Page 3 of 12") is layout, not content. Left in, it becomes the most
   repeated passage in the corpus and a duplicate-chunk generator.
2. **Character cleanup**: Unicode NFC, ligature expansion (PDFs are full of
   `ﬁ`), zero-width and soft-hyphen removal, exotic spaces to plain spaces,
   control characters stripped.
3. **Whitespace**: prose has its hard wraps de-hyphenated and collapsed to one
   line; code and tables keep their line structure and only lose trailing
   space and runs of blank lines.
4. **Paragraphs split by a page break are rejoined**, so a sentence is not cut
   in half by where the printer's page happened to end.
5. **Offsets and heading paths are assigned**, last, against the final text --
   so `block.text == document.text[char_start:char_end]` holds by construction.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace

from orbit.domain.errors import DocumentLimitExceededError
from orbit.domain.processing.content import (
    BLOCK_SEPARATOR,
    BlockKind,
    ExtractedBlock,
    ExtractedDocument,
    NormalizationStats,
    ParsedDocument,
    ParseLimits,
    TextBlock,
)

_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\ufb05": "st",
        "\ufb06": "st",
    }
)
#: Removed outright: zero-width characters, the BOM, and the soft hyphen, which
#: is invisible when rendered and splits words when embedded.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
_EXOTIC_SPACE = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_HYPHENATED_BREAK = re.compile(r"(?<=[^\W\d_])-\n(?=[^\W\d_])")
_WHITESPACE = re.compile(r"\s+")
_BLANK_LINES = re.compile(r"\n{3,}")
_DIGITS = re.compile(r"\d+")
_SENTENCE_END = re.compile(r"[.!?:;\"'\u201d\u2019)\]]\s*$")

#: A running header or footer is short; a long repeated line is content.
_MAX_BOILERPLATE_LENGTH = 120
#: How many lines at each end of a page are candidates.
_BOILERPLATE_EDGE_LINES = 2
_MIN_PAGES_FOR_BOILERPLATE = 3
#: Both edges plus a body in between; see `_remove_running_boilerplate`.
_MIN_LINES_FOR_EVIDENCE = 2 * _BOILERPLATE_EDGE_LINES + 1
_MAX_HEADING_PATH_ITEM = 200


@dataclass(slots=True)
class _Block:
    """Mutable working copy of a block during normalization."""

    kind: BlockKind
    text: str
    page_start: int | None
    page_end: int | None
    heading_level: int | None
    source: ExtractedBlock


class DocumentNormalizer:
    def normalize(self, document: ExtractedDocument, *, limits: ParseLimits) -> ParsedDocument:
        stats = NormalizationStats(input_blocks=len(document.blocks))
        blocks = [
            _Block(
                kind=block.kind,
                text=block.text,
                page_start=block.location.page,
                page_end=block.location.page,
                heading_level=block.heading_level,
                source=block,
            )
            for block in document.blocks
        ]

        removed = _remove_running_boilerplate(blocks, page_count=document.page_count)
        replacement_characters = 0
        cleaned: list[_Block] = []
        for block in blocks:
            replacement_characters += block.text.count("\ufffd")
            block.text = _clean(block.text, block.kind)
            if block.text:
                cleaned.append(block)
        dropped = len(blocks) - len(cleaned)

        merged_blocks, merged = _rejoin_across_pages(cleaned)

        text_blocks, text = _assign_offsets(merged_blocks)
        if len(text) > limits.max_characters:
            msg = (
                f"This document is too long to process. The limit is "
                f"{limits.max_characters:,} characters."
            )
            raise DocumentLimitExceededError(msg, characters=len(text))
        if len(text_blocks) > limits.max_blocks:
            msg = "This document has too many sections to process."
            raise DocumentLimitExceededError(msg, blocks=len(text_blocks))

        return ParsedDocument(
            format=document.format,
            blocks=tuple(text_blocks),
            text=text,
            page_count=document.page_count,
            metadata={
                key: _clean(value, BlockKind.PARAGRAPH) for key, value in document.metadata.items()
            },
            stats=replace(
                stats,
                dropped_empty_blocks=dropped,
                removed_boilerplate_lines=removed,
                merged_across_pages=merged,
                replacement_characters=replacement_characters,
            ),
        )


def _clean(text: str, kind: BlockKind) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_LIGATURES).translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    text = _EXOTIC_SPACE.sub(" ", text)
    text = _CONTROL.sub("", text)
    # U+FFFD marks bytes the decoder could not map. It carries no meaning and
    # would otherwise become a token that matches every other broken document.
    text = text.replace("\ufffd", "")

    if kind.preserves_line_breaks:
        lines = [line.rstrip() for line in text.split("\n")]
        return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip("\n")

    text = _HYPHENATED_BREAK.sub(_dehyphenate, text)
    return _WHITESPACE.sub(" ", text).strip()


def _dehyphenate(match: re.Match[str]) -> str:
    """Join "infor-" + "mation", but keep "Anglo-" + "Saxon": only a lowercase
    continuation is evidence the hyphen was inserted by line wrapping."""
    following = match.string[match.end() : match.end() + 1]
    return "" if following.islower() else match.group()


def _boilerplate_key(line: str) -> str:
    """Page numbers differ page to page; the line they sit in does not."""
    return _DIGITS.sub("#", _WHITESPACE.sub(" ", line).strip().casefold())


def _remove_running_boilerplate(blocks: list[_Block], *, page_count: int | None) -> int:
    """Strip lines repeated at the top or bottom of most pages.

    A line qualifies if, after page numbers are masked, it appears within the
    first or last two lines of at least half the pages (and at least three).
    Only those edge positions are touched: the same sentence repeated in the
    body of the document is content and is left alone.

    Boilerplate is only *learned* from pages long enough to have a body
    between their edges. On a three-line page every line is an "edge", and a
    sentence repeated on every short page would be indistinguishable from a
    running header -- so short pages contribute no evidence, although a header
    learned from the long pages is still stripped from them.
    """
    if not page_count or page_count < _MIN_PAGES_FOR_BOILERPLATE:
        return 0

    pages: dict[int, list[_Block]] = {}
    for block in blocks:
        if block.page_start is not None:
            pages.setdefault(block.page_start, []).append(block)
    if len(pages) < _MIN_PAGES_FOR_BOILERPLATE:
        return 0

    def edges(page_blocks: list[_Block]) -> list[str]:
        lines = [line for block in page_blocks for line in block.text.split("\n") if line.strip()]
        head = lines[:_BOILERPLATE_EDGE_LINES]
        tail = lines[-_BOILERPLATE_EDGE_LINES:] if len(lines) > _BOILERPLATE_EDGE_LINES else []
        return [line for line in (*head, *tail) if len(line.strip()) <= _MAX_BOILERPLATE_LENGTH]

    occurrences: Counter[str] = Counter()
    evidence_pages = 0
    for page_blocks in pages.values():
        if _line_count(page_blocks) < _MIN_LINES_FOR_EVIDENCE:
            continue
        evidence_pages += 1
        occurrences.update({_boilerplate_key(line) for line in edges(page_blocks)})
    if evidence_pages < _MIN_PAGES_FOR_BOILERPLATE:
        return 0

    threshold = max(_MIN_PAGES_FOR_BOILERPLATE, math.ceil(evidence_pages * 0.5))
    boilerplate = {key for key, count in occurrences.items() if count >= threshold and key}
    if not boilerplate:
        return 0

    removed = 0
    for page_blocks in pages.values():
        removed += _strip_edge_lines(page_blocks, boilerplate, from_top=True)
        removed += _strip_edge_lines(page_blocks, boilerplate, from_top=False)
    return removed


def _line_count(page_blocks: list[_Block]) -> int:
    return sum(1 for block in page_blocks for line in block.text.split("\n") if line.strip())


def _strip_edge_lines(page_blocks: list[_Block], boilerplate: set[str], *, from_top: bool) -> int:
    removed = 0
    ordered = page_blocks if from_top else list(reversed(page_blocks))
    for block in ordered:
        lines = block.text.split("\n")
        while (
            removed < _BOILERPLATE_EDGE_LINES
            and lines
            and _boilerplate_key(lines[0 if from_top else -1]) in boilerplate
        ):
            lines.pop(0 if from_top else -1)
            removed += 1
        block.text = "\n".join(lines)
        if lines or removed >= _BOILERPLATE_EDGE_LINES:
            break
    return removed


def _rejoin_across_pages(blocks: list[_Block]) -> tuple[list[_Block], int]:
    """Merge a paragraph that a page break split in two.

    Only when the evidence is unambiguous: both halves are prose paragraphs on
    consecutive pages, the first does not end a sentence, and the second starts
    in lowercase -- i.e. it is visibly the same sentence continuing.
    """
    merged: list[_Block] = []
    count = 0
    for block in blocks:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.kind is BlockKind.PARAGRAPH
            and block.kind is BlockKind.PARAGRAPH
            and previous.page_end is not None
            and block.page_start is not None
            and block.page_start == previous.page_end + 1
            and not _SENTENCE_END.search(previous.text)
            and block.text[:1].islower()
        ):
            previous.text = f"{previous.text} {block.text}"
            previous.page_end = block.page_end
            count += 1
            continue
        merged.append(block)
    return merged, count


def _assign_offsets(blocks: list[_Block]) -> tuple[list[TextBlock], str]:
    headings: list[tuple[int, str]] = []
    result: list[TextBlock] = []
    parts: list[str] = []
    position = 0
    for ordinal, block in enumerate(blocks):
        if block.kind is BlockKind.HEADING and block.heading_level is not None:
            while headings and headings[-1][0] >= block.heading_level:
                headings.pop()
            headings.append((block.heading_level, block.text[:_MAX_HEADING_PATH_ITEM]))

        if parts:
            position += len(BLOCK_SEPARATOR)
        start = position
        position += len(block.text)
        parts.append(block.text)
        result.append(
            TextBlock(
                ordinal=ordinal,
                kind=block.kind,
                text=block.text,
                char_start=start,
                char_end=position,
                page_start=block.page_start,
                page_end=block.page_end,
                heading_path=tuple(text for _, text in headings),
                heading_level=block.heading_level,
                location=block.source.location,
            )
        )
    return result, BLOCK_SEPARATOR.join(parts)
