"""Markdown parser.

Built on markdown-it-py's token stream rather than on rendered HTML: tokens
carry the source line range of every block, which becomes provenance, and they
never produce markup that would then need to be stripped back out.

Markdown is where structure is richest, so it is where heading paths, list
items, code blocks, and tables are all real. Inline formatting is reduced to
its text; link targets and raw HTML are dropped, because neither is prose a
question could be answered from, and raw HTML from an upload is never
something ORBIT should carry forward.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import IO

from markdown_it import MarkdownIt
from markdown_it.token import Token

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
from orbit.infrastructure.parsing.plain_text import read_bounded

_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?\n)---[ \t]*(?:\n|\Z)", re.DOTALL)
_FRONT_MATTER_TITLE = re.compile(r"^title:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)
_HTML_TAG = re.compile(r"<[^>]*>")
_MAX_METADATA_LENGTH = 256


class MarkdownParser:
    content_types = frozenset({"text/markdown"})

    def __init__(self) -> None:
        # CommonMark plus GFM tables and strikethrough. `html` stays enabled so
        # HTML blocks are *recognised* (and then dropped) rather than being
        # misread as paragraphs of angle brackets.
        self._md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])

    def parse(self, source: IO[bytes], *, limits: ParseLimits) -> ExtractedDocument:
        decoded = decode_text(read_bounded(source, limits=limits))
        text = normalize_newlines(decoded.text)
        text, metadata = _strip_front_matter(text)

        tokens = self._md.parse(text)
        blocks = _Extractor(limits).extract(tokens)

        warnings = () if decoded.encoding.startswith("utf-8") else (f"encoding:{decoded.encoding}",)
        return ExtractedDocument(
            format=DocumentFormat.MARKDOWN,
            blocks=tuple(blocks),
            metadata=metadata,
            warnings=warnings,
        )


def _strip_front_matter(text: str) -> tuple[str, dict[str, str]]:
    """Remove YAML front matter, keeping its title as advisory metadata.

    Replaced with the same number of newlines rather than deleted, so every
    line number after it still points at the right line of the original file.
    """
    match = _FRONT_MATTER.match(text)
    if match is None:
        return text, {}
    metadata: dict[str, str] = {}
    title = _FRONT_MATTER_TITLE.search(match.group(1))
    if title:
        metadata["title"] = title.group(1)[:_MAX_METADATA_LENGTH]
    return "\n" * match.group(0).count("\n") + text[match.end() :], metadata


class _Extractor:
    def __init__(self, limits: ParseLimits) -> None:
        self._limits = limits
        self._blocks: list[ExtractedBlock] = []
        self._list_depth = 0
        self._quote_depth = 0

    def extract(self, tokens: Sequence[Token]) -> list[ExtractedBlock]:
        index = 0
        while index < len(tokens):
            token = tokens[index]
            match token.type:
                case "bullet_list_open" | "ordered_list_open":
                    self._list_depth += 1
                case "bullet_list_close" | "ordered_list_close":
                    self._list_depth -= 1
                case "blockquote_open":
                    self._quote_depth += 1
                case "blockquote_close":
                    self._quote_depth -= 1
                case "heading_open":
                    inline = tokens[index + 1]
                    self._add(
                        BlockKind.HEADING,
                        _inline_text(inline),
                        token,
                        heading_level=int(token.tag[1]),
                    )
                    index += 2
                case "paragraph_open":
                    inline = tokens[index + 1]
                    kind = (
                        BlockKind.LIST_ITEM
                        if self._list_depth
                        else BlockKind.QUOTE
                        if self._quote_depth
                        else BlockKind.PARAGRAPH
                    )
                    self._add(kind, _inline_text(inline), token)
                    index += 2
                case "fence" | "code_block":
                    self._add(BlockKind.CODE, token.content, token)
                case "table_open":
                    index = self._table(tokens, index)
                # `html_block`, `hr`, and closing tokens carry no prose.
            index += 1
        return self._blocks

    def _table(self, tokens: Sequence[Token], start: int) -> int:
        """Flatten a table to one row per line, cells separated by `|`.

        A table is one block: splitting it into rows would separate every
        value from the header that says what it is.
        """
        opening = tokens[start]
        rows: list[list[str]] = []
        index = start + 1
        while index < len(tokens) and tokens[index].type != "table_close":
            token = tokens[index]
            if token.type == "tr_open":
                rows.append([])
            elif token.type == "inline" and rows:
                rows[-1].append(_inline_text(token).replace("\n", " ").strip())
            index += 1
        body = "\n".join(" | ".join(cells) for cells in rows if any(cells))
        self._add(BlockKind.TABLE, body, opening)
        return index

    def _add(
        self,
        kind: BlockKind,
        text: str,
        token: Token,
        *,
        heading_level: int | None = None,
    ) -> None:
        if not text.strip():
            return
        location = SourceLocation()
        if token.map:
            first, end = token.map
            location = SourceLocation(line_start=first + 1, line_end=max(end, first + 1))
        self._blocks.append(
            ExtractedBlock(kind=kind, text=text, location=location, heading_level=heading_level)
        )
        if len(self._blocks) > self._limits.max_blocks:
            msg = "This document has too many sections to process."
            raise DocumentLimitExceededError(msg, blocks=len(self._blocks))


def _inline_text(token: Token) -> str:
    parts: list[str] = []
    for child in token.children or ():
        match child.type:
            case "text" | "code_inline":
                parts.append(child.content)
            case "softbreak" | "hardbreak":
                parts.append("\n")
            case "image":
                # Alt text is the image's only prose.
                parts.append(child.content)
            case "html_inline":
                # Inline HTML is dropped, but a tag like `<br>` separated words.
                parts.append(" " if _HTML_TAG.fullmatch(child.content) else "")
    return "".join(parts)
