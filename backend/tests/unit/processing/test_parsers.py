"""Parsers: PDF, Markdown, plain text, the text decoder, and the registry.

Every parser must produce the shared `ExtractedDocument` model and nothing
else, and every failure caused by the file must surface as a
`DocumentProcessingError` with a user-safe message -- never as a library's own
exception, which the pipeline would have to treat as a defect in ORBIT.
"""

from __future__ import annotations

import codecs
import io

import pytest

from orbit.domain.errors import (
    DocumentCorruptError,
    DocumentEmptyError,
    DocumentEncodingError,
    DocumentEncryptedError,
    DocumentFormatUnsupportedError,
    DocumentLimitExceededError,
    DocumentProcessingError,
)
from orbit.domain.processing.content import (
    BlockKind,
    DocumentFormat,
    ExtractedDocument,
    ParseLimits,
)
from orbit.infrastructure.parsing.decoding import decode_text
from orbit.infrastructure.parsing.markdown import MarkdownParser
from orbit.infrastructure.parsing.pdf import PdfParser
from orbit.infrastructure.parsing.plain_text import PlainTextParser
from orbit.infrastructure.parsing.registry import StaticParserRegistry, default_parser_registry
from tests.fixtures.pdf import build_pdf, encrypt_pdf, paragraph_lines

LIMITS = ParseLimits()


def _pdf(pages: list[list[str]], **kwargs: object) -> ExtractedDocument:
    return PdfParser().parse(io.BytesIO(build_pdf(pages, **kwargs)), limits=LIMITS)  # type: ignore[arg-type]


def _md(text: str | bytes) -> ExtractedDocument:
    data = text.encode() if isinstance(text, str) else text
    return MarkdownParser().parse(io.BytesIO(data), limits=LIMITS)


def _txt(data: bytes, limits: ParseLimits = LIMITS) -> ExtractedDocument:
    return PlainTextParser().parse(io.BytesIO(data), limits=limits)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


class TestPdfParser:
    def test_extracts_text_with_page_numbers(self) -> None:
        document = _pdf([["First page text."], ["Second page text."]])
        assert document.format is DocumentFormat.PDF
        assert document.page_count == 2
        assert [(block.text, block.location.page) for block in document.blocks] == [
            ("First page text.", 1),
            ("Second page text.", 2),
        ]

    def test_compressed_and_uncompressed_streams_extract_identically(self) -> None:
        pages = [["Alpha beta gamma.", "", "Delta epsilon."]]
        assert _pdf(pages, compress=True).blocks == _pdf(pages, compress=False).blocks

    def test_wrapped_lines_stay_in_one_paragraph_and_blank_lines_split(self) -> None:
        body = "Tokens rotate on every refresh and reuse revokes the family. " * 4
        document = _pdf([[*paragraph_lines(body), "", *paragraph_lines("A second paragraph.")]])
        paragraphs = [block for block in document.blocks if block.kind is BlockKind.PARAGRAPH]
        assert len(paragraphs) == 2
        assert "\n" in paragraphs[0].text  # line structure kept for normalization

    def test_numbered_headings_are_detected_with_their_level(self) -> None:
        document = _pdf([["1 Introduction", "Body.", "", "2.1 Token rotation", "More body."]])
        headings = [
            (b.text, b.heading_level) for b in document.blocks if b.kind is BlockKind.HEADING
        ]
        assert headings == [("1 Introduction", 1), ("2.1 Token rotation", 2)]

    def test_a_numbered_list_item_is_not_mistaken_for_a_heading(self) -> None:
        document = _pdf([["1. Buy milk", "2. Walk the dog"]])
        assert all(block.kind is BlockKind.LIST_ITEM for block in document.blocks)

    def test_title_metadata_is_advisory_and_bounded(self) -> None:
        document = _pdf([["Body."]], title="Quarterly " + "x" * 400)
        assert document.metadata["title"].startswith("Quarterly")
        assert len(document.metadata["title"]) <= 256

    def test_a_page_without_a_text_layer_is_reported_not_fatal(self) -> None:
        document = _pdf([["Readable page."], []])
        assert document.page_count == 2
        assert "pdf:pages_without_text=1" in document.warnings

    def test_a_pdf_with_only_image_pages_parses_to_no_blocks(self) -> None:
        # The *pipeline* turns this into DOCUMENT_NO_EXTRACTABLE_TEXT; the
        # parser's job is only to report honestly that there is no text.
        document = _pdf([[], [], []])
        assert document.page_count == 3
        assert document.blocks == ()

    def test_password_protected_pdf_is_a_distinct_failure(self) -> None:
        protected = encrypt_pdf(build_pdf([["Secret."]]), user_password="hunter2")
        with pytest.raises(DocumentEncryptedError) as caught:
            PdfParser().parse(io.BytesIO(protected), limits=LIMITS)
        assert "password" in caught.value.message.lower()

    def test_permissions_only_encryption_opens_like_any_viewer_would(self) -> None:
        permissions_only = encrypt_pdf(build_pdf([["Readable."]]), user_password="")
        document = PdfParser().parse(io.BytesIO(permissions_only), limits=LIMITS)
        assert [block.text for block in document.blocks] == ["Readable."]

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(b"%PDF-1.7\n", id="header-only"),
            pytest.param(b"%PDF-1.7\n" + b"\x00\xff" * 500, id="binary-garbage"),
            pytest.param(build_pdf([["Hello."]])[:120], id="truncated"),
            pytest.param(b"not a pdf at all", id="no-magic"),
        ],
    )
    def test_malformed_pdfs_fail_as_corrupt_with_a_user_safe_message(self, payload: bytes) -> None:
        with pytest.raises((DocumentCorruptError, DocumentEmptyError)) as caught:
            PdfParser().parse(io.BytesIO(payload), limits=LIMITS)
        message = caught.value.message
        assert "Traceback" not in message
        assert "pypdf" not in message.lower()

    def test_a_corrupt_xref_is_recovered_when_the_objects_are_intact(self) -> None:
        # pypdf rebuilds a damaged cross-reference table by scanning; a file a
        # viewer would open must not be rejected.
        pdf = build_pdf([["Recoverable text."]])
        damaged = pdf.replace(b"startxref\n", b"startxref\n9999")
        document = PdfParser().parse(io.BytesIO(damaged), limits=LIMITS)
        assert [block.text for block in document.blocks] == ["Recoverable text."]

    def test_page_limit_is_enforced_before_extraction(self) -> None:
        pdf = build_pdf([["p"]] * 5)
        with pytest.raises(DocumentLimitExceededError) as caught:
            PdfParser().parse(io.BytesIO(pdf), limits=ParseLimits(max_pages=4))
        assert "5" in caught.value.message and "4" in caught.value.message

    def test_character_limit_stops_extraction_as_text_accumulates(self) -> None:
        pdf = build_pdf([["word " * 60]] * 10)
        with pytest.raises(DocumentLimitExceededError):
            PdfParser().parse(io.BytesIO(pdf), limits=ParseLimits(max_characters=1000))


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


class TestMarkdownParser:
    def test_structure_is_preserved_as_block_kinds(self) -> None:
        document = _md(
            "# Title\n\nIntro *emphasis* and `code`.\n\n- item one\n- item two\n\n"
            "> quoted text\n\n```python\ndef f():\n    return 1\n```\n\n"
            "| k | v |\n|---|---|\n| a | 1 |\n"
        )
        kinds = [block.kind for block in document.blocks]
        assert kinds == [
            BlockKind.HEADING,
            BlockKind.PARAGRAPH,
            BlockKind.LIST_ITEM,
            BlockKind.LIST_ITEM,
            BlockKind.QUOTE,
            BlockKind.CODE,
            BlockKind.TABLE,
        ]
        assert document.blocks[1].text == "Intro emphasis and code."
        assert document.blocks[5].text == "def f():\n    return 1\n"
        assert document.blocks[6].text == "k | v\na | 1"

    def test_heading_levels_come_from_atx_and_setext_forms(self) -> None:
        document = _md("Top\n===\n\n### Deep\n")
        assert [(b.text, b.heading_level) for b in document.blocks] == [("Top", 1), ("Deep", 3)]

    def test_blocks_carry_source_line_ranges(self) -> None:
        document = _md("# One\n\nline two\nline three\n\n## Four\n")
        locations = [(b.location.line_start, b.location.line_end) for b in document.blocks]
        assert locations == [(1, 1), (3, 4), (6, 6)]

    def test_front_matter_is_removed_but_line_numbers_still_match_the_file(self) -> None:
        document = _md('---\ntitle: "Runbook"\nowner: ops\n---\n# Heading\n')
        assert document.metadata == {"title": "Runbook"}
        assert document.blocks[0].text == "Heading"
        assert document.blocks[0].location.line_start == 5

    def test_link_targets_and_raw_html_are_dropped_but_link_text_kept(self) -> None:
        document = _md(
            "See [the docs](https://example.com/secret?token=x).\n\n"
            "<script>alert(1)</script>\n\nAfter<br>break ![diagram of flow](x.png)\n"
        )
        texts = [block.text for block in document.blocks]
        assert texts == ["See the docs.", "After break diagram of flow"]

    def test_empty_markdown_yields_no_blocks(self) -> None:
        assert _md("\n\n   \n").blocks == ()


# ---------------------------------------------------------------------------
# Plain text and decoding
# ---------------------------------------------------------------------------


class TestPlainTextParser:
    def test_paragraphs_split_on_blank_lines_with_line_ranges(self) -> None:
        document = _txt(b"first line\nwrapped line\n\n\nsecond para\n")
        assert [(b.text, b.location.line_start, b.location.line_end) for b in document.blocks] == [
            ("first line\nwrapped line", 1, 2),
            ("second para", 5, 5),
        ]
        assert all(block.kind is BlockKind.PARAGRAPH for block in document.blocks)

    def test_windows_line_endings_are_normalized(self) -> None:
        document = _txt(b"one\r\ntwo\r\n\r\nthree")
        assert [block.text for block in document.blocks] == ["one\ntwo", "three"]

    def test_whitespace_only_file_has_no_blocks(self) -> None:
        assert _txt(b" \n\t\n \n").blocks == ()

    def test_file_beyond_the_character_limit_is_refused_before_decoding(self) -> None:
        with pytest.raises(DocumentLimitExceededError):
            _txt(b"a" * 5000, limits=ParseLimits(max_characters=1000))


class TestDecoding:
    def test_utf8_with_and_without_bom(self) -> None:
        assert decode_text("h\u00e9llo".encode()).text == "h\u00e9llo"
        decoded = decode_text(codecs.BOM_UTF8 + "h\u00e9llo".encode())
        assert decoded.text == "h\u00e9llo"

    @pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
    def test_utf16_with_a_bom(self, encoding: str) -> None:
        bom = codecs.BOM_UTF16_LE if encoding.endswith("le") else codecs.BOM_UTF16_BE
        assert decode_text(bom + "Gr\u00fc\u00dfe".encode(encoding)).text == "Gr\u00fc\u00dfe"

    def test_bomless_utf16_is_detected_from_nul_pattern(self) -> None:
        decoded = decode_text("plain ascii words here".encode("utf-16-le"))
        assert decoded.text == "plain ascii words here"
        assert decoded.encoding == "utf-16-le"

    def test_windows_1252_fallback_for_legacy_files(self) -> None:
        decoded = decode_text("na\u00efve caf\u00e9 \u201cquoted\u201d".encode("cp1252"))
        assert decoded.text == "na\u00efve caf\u00e9 \u201cquoted\u201d"
        assert decoded.encoding == "cp1252"

    def test_binary_masquerading_as_text_is_refused(self) -> None:
        with pytest.raises(DocumentEncodingError) as caught:
            decode_text(bytes(range(1, 32)) * 50)
        assert "UTF-8" in caught.value.message

    def test_markdown_and_text_warn_the_operator_about_a_fallback_encoding(self) -> None:
        assert _txt("caf\u00e9".encode("cp1252")).warnings == ("encoding:cp1252",)
        assert _md("# caf\u00e9".encode("cp1252")).warnings == ("encoding:cp1252",)


# ---------------------------------------------------------------------------
# Registry and the shared contract
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_resolves_by_sniffed_content_type(self) -> None:
        registry = default_parser_registry()
        assert isinstance(registry.resolve("application/pdf"), PdfParser)
        assert isinstance(registry.resolve("text/markdown"), MarkdownParser)
        assert isinstance(registry.resolve("text/plain; charset=utf-8"), PlainTextParser)

    def test_unknown_type_is_refused_never_treated_as_text(self) -> None:
        with pytest.raises(DocumentFormatUnsupportedError):
            default_parser_registry().resolve("application/x-msdownload")

    def test_two_parsers_for_one_type_is_a_wiring_error(self) -> None:
        with pytest.raises(ValueError, match="Two parsers"):
            StaticParserRegistry([PlainTextParser(), PlainTextParser()])

    @pytest.mark.parametrize(
        ("parser", "payload"),
        [
            (PdfParser(), build_pdf([["x"]])),
            (MarkdownParser(), b"# x\n"),
            (PlainTextParser(), b"x"),
        ],
    )
    def test_every_parser_produces_the_shared_model(
        self, parser: PdfParser | MarkdownParser | PlainTextParser, payload: bytes
    ) -> None:
        document = parser.parse(io.BytesIO(payload), limits=LIMITS)
        assert isinstance(document, ExtractedDocument)

    def test_processing_errors_are_all_domain_errors(self) -> None:
        # The classifier relies on this: parser failures are permanent only
        # because they are `DocumentProcessingError`s.
        for error in (DocumentCorruptError, DocumentEncryptedError, DocumentEncodingError):
            assert issubclass(error, DocumentProcessingError)
