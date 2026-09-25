"""Parser resolution by sniffed content type (ADR-0012)."""

from __future__ import annotations

from collections.abc import Iterable

from orbit.domain.errors import DocumentFormatUnsupportedError
from orbit.domain.ports.processing import DocumentParser
from orbit.infrastructure.parsing.markdown import MarkdownParser
from orbit.infrastructure.parsing.pdf import PdfParser
from orbit.infrastructure.parsing.plain_text import PlainTextParser


class StaticParserRegistry:
    """Resolves the one parser registered for a content type.

    The content type comes from the upload's magic-byte sniff, never the file
    extension or the client's claim. An unknown type is refused -- there is no
    "treat it as text" fallback, because an unknown binary parsed as UTF-8
    produces garbage chunks indistinguishable from real content.
    """

    def __init__(self, parsers: Iterable[DocumentParser]) -> None:
        self._by_type: dict[str, DocumentParser] = {}
        for parser in parsers:
            for content_type in parser.content_types:
                if content_type in self._by_type:
                    msg = f"Two parsers registered for {content_type}."
                    raise ValueError(msg)
                self._by_type[content_type] = parser

    def resolve(self, content_type: str) -> DocumentParser:
        parser = self._by_type.get(content_type.split(";", 1)[0].strip().lower())
        if parser is None:
            msg = "ORBIT cannot process this type of file."
            raise DocumentFormatUnsupportedError(msg, content_type=content_type[:128])
        return parser


def default_parser_registry() -> StaticParserRegistry:
    return StaticParserRegistry((PdfParser(), MarkdownParser(), PlainTextParser()))
