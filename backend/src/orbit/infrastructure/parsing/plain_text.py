"""Plain text parser.

Paragraphs are separated by blank lines; that is the only structure plain text
reliably has. Single line breaks inside a paragraph are hard wraps, and are
collapsed by the shared normalization stage, not here. Plain text has no
headings, so its chunks carry no heading path -- correct, not a defect
(ADR-0013).
"""

from __future__ import annotations

import re
from typing import IO

from orbit.domain.errors import DocumentLimitExceededError
from orbit.domain.processing.content import (
    BlockKind,
    DocumentFormat,
    ExtractedBlock,
    ExtractedDocument,
    ParseLimits,
    SourceLocation,
)
from orbit.infrastructure.parsing.decoding import decode_text, normalize_newlines

_BLANK_LINE = re.compile(r"\n[ \t\f\v]*\n")


def read_bounded(source: IO[bytes], *, limits: ParseLimits) -> bytes:
    """Read the whole source, refusing input that cannot fit the character limit.

    UTF-8 needs at most four bytes per character, so a text file larger than
    four times the character limit cannot possibly be within it -- refused
    before it is decoded into a string several times its size.
    """
    ceiling = limits.max_characters * 4
    data = source.read(ceiling + 1)
    if len(data) > ceiling:
        msg = (
            f"This document is too long to process. The limit is "
            f"{limits.max_characters:,} characters."
        )
        raise DocumentLimitExceededError(msg, bytes_read=len(data))
    return data


class PlainTextParser:
    content_types = frozenset({"text/plain"})

    def parse(self, source: IO[bytes], *, limits: ParseLimits) -> ExtractedDocument:
        decoded = decode_text(read_bounded(source, limits=limits))
        text = normalize_newlines(decoded.text)

        blocks: list[ExtractedBlock] = []
        position = 0
        line = 1
        for match in [*_BLANK_LINE.finditer(text), None]:
            end = match.start() if match else len(text)
            paragraph = text[position:end]
            leading_newlines = len(paragraph) - len(paragraph.lstrip("\n"))
            body = paragraph.strip("\n")
            if body.strip():
                start_line = line + leading_newlines
                blocks.append(
                    ExtractedBlock(
                        kind=BlockKind.PARAGRAPH,
                        text=body,
                        location=SourceLocation(
                            line_start=start_line, line_end=start_line + body.count("\n")
                        ),
                    )
                )
                if len(blocks) > limits.max_blocks:
                    msg = "This document has too many paragraphs to process."
                    raise DocumentLimitExceededError(msg, blocks=len(blocks))
            if match is None:
                break
            line += text.count("\n", position, match.end())
            position = match.end()

        warnings = () if decoded.encoding.startswith("utf-8") else (f"encoding:{decoded.encoding}",)
        return ExtractedDocument(
            format=DocumentFormat.TEXT, blocks=tuple(blocks), warnings=warnings
        )
