"""The shared normalization stage: one set of text rules for every format."""

from __future__ import annotations

import pytest

from orbit.domain.errors import DocumentLimitExceededError
from orbit.domain.processing.content import (
    BLOCK_SEPARATOR,
    BlockKind,
    DocumentFormat,
    ExtractedBlock,
    ExtractedDocument,
    ParsedDocument,
    ParseLimits,
    SourceLocation,
)
from orbit.infrastructure.parsing.normalization import DocumentNormalizer

LIMITS = ParseLimits()


def _normalize(
    *blocks: ExtractedBlock,
    page_count: int | None = None,
    limits: ParseLimits = LIMITS,
    fmt: DocumentFormat = DocumentFormat.TEXT,
) -> ParsedDocument:
    return DocumentNormalizer().normalize(
        ExtractedDocument(format=fmt, blocks=blocks, page_count=page_count), limits=limits
    )


def _p(text: str, page: int | None = None) -> ExtractedBlock:
    return ExtractedBlock(kind=BlockKind.PARAGRAPH, text=text, location=SourceLocation(page=page))


def _h(text: str, level: int, page: int | None = None) -> ExtractedBlock:
    return ExtractedBlock(
        kind=BlockKind.HEADING, text=text, heading_level=level, location=SourceLocation(page=page)
    )


class TestCharacterCleanup:
    def test_unicode_is_nfc_normalized(self) -> None:
        decomposed = "cafe\u0301"
        assert _normalize(_p(decomposed)).text == "caf\u00e9"

    def test_ligatures_are_expanded(self) -> None:
        assert _normalize(_p("\ufb01nal e\ufb03cient")).text == "final efficient"

    def test_invisible_characters_are_removed(self) -> None:
        text = "zero\u200bwidth soft\u00adhyphen\ufeff"
        assert _normalize(_p(text)).text == "zerowidth softhyphen"

    def test_exotic_spaces_become_plain_spaces_and_collapse(self) -> None:
        assert _normalize(_p("a\u00a0\u2003b\u3000c")).text == "a b c"

    def test_control_characters_and_replacement_characters_are_stripped(self) -> None:
        document = _normalize(_p("ok\x07 bell \ufffd gone"))
        assert document.text == "ok bell gone"
        assert document.stats.replacement_characters == 1


class TestWhitespace:
    def test_repeated_whitespace_and_hard_wraps_collapse_in_prose(self) -> None:
        assert _normalize(_p("  one   two\n\nthree\t four  ")).text == "one two three four"

    def test_hyphenation_across_a_line_break_is_rejoined(self) -> None:
        assert _normalize(_p("the infor-\nmation age")).text == "the information age"

    def test_a_capitalised_continuation_is_not_dehyphenated(self) -> None:
        # "Anglo-\nSaxon" is a real hyphen; only lowercase continuations join.
        assert _normalize(_p("Anglo-\nSaxon")).text == "Anglo- Saxon"

    def test_code_keeps_its_lines_but_loses_trailing_space_and_blank_runs(self) -> None:
        code = ExtractedBlock(kind=BlockKind.CODE, text="def f():   \n\n\n\n    return 1\n")
        assert _normalize(code).text == "def f():\n\n    return 1"

    def test_blocks_that_become_empty_are_dropped(self) -> None:
        document = _normalize(_p("   \u200b  "), _p("kept"))
        assert [block.text for block in document.blocks] == ["kept"]
        assert document.stats.dropped_empty_blocks == 1

    def test_an_all_whitespace_document_normalizes_to_empty(self) -> None:
        document = _normalize(_p(" \n\t "), _p("\u00a0"))
        assert document.is_empty
        assert document.text == ""


class TestOffsetsAndStructure:
    def test_every_block_is_an_exact_slice_of_the_document_text(self) -> None:
        document = _normalize(_h("Title", 1), _p("first  para"), _p("second\npara"))
        for block in document.blocks:
            assert document.text[block.char_start : block.char_end] == block.text
        assert document.text == BLOCK_SEPARATOR.join(block.text for block in document.blocks)
        assert [block.ordinal for block in document.blocks] == [0, 1, 2]

    def test_heading_paths_nest_and_pop_by_level(self) -> None:
        document = _normalize(
            _h("Guide", 1),
            _p("intro"),
            _h("Security", 2),
            _h("Tokens", 3),
            _p("rotation"),
            _h("Operations", 2),
            _p("runbook"),
        )
        paths = {block.text: block.heading_path for block in document.blocks}
        assert paths["intro"] == ("Guide",)
        assert paths["rotation"] == ("Guide", "Security", "Tokens")
        assert paths["runbook"] == ("Guide", "Operations")

    def test_content_before_any_heading_has_no_path(self) -> None:
        assert _normalize(_p("preamble"), _h("H", 1)).blocks[0].heading_path == ()


class TestPageBoundaries:
    def test_a_sentence_split_by_a_page_break_is_rejoined_with_its_page_range(self) -> None:
        document = _normalize(
            _p("The contract renews on", page=1),
            _p("the first of January.", page=2),
            page_count=2,
            fmt=DocumentFormat.PDF,
        )
        assert [block.text for block in document.blocks] == [
            "The contract renews on the first of January."
        ]
        assert (document.blocks[0].page_start, document.blocks[0].page_end) == (1, 2)
        assert document.stats.merged_across_pages == 1

    def test_a_finished_sentence_is_not_merged_with_the_next_page(self) -> None:
        document = _normalize(
            _p("First page ends here.", page=1),
            _p("next page starts lowercase", page=2),
            page_count=2,
        )
        assert len(document.blocks) == 2

    def test_running_headers_and_footers_are_removed_from_most_pages(self) -> None:
        words = ["alpha", "bravo", "charlie", "delta", "echo"]
        pages = []
        for number, word in enumerate(words, start=1):
            body = f"Opening about {word}.\nMiddle about {word}.\nClosing about {word}."
            pages.append(_p(f"ACME Handbook\n{body}", page=number))
            pages.append(_p(f"Page {number} of 5", page=number))
        document = _normalize(*pages, page_count=5, fmt=DocumentFormat.PDF)
        assert "ACME Handbook" not in document.text
        assert "of 5" not in document.text
        assert document.text.count("Middle about") == 5
        assert document.text.count("Closing about") == 5
        assert document.stats.removed_boilerplate_lines == 10

    def test_repetition_in_the_body_is_content_not_boilerplate(self) -> None:
        words = ["alpha", "bravo", "charlie", "delta", "echo"]
        pages = [
            _p(
                f"Opening {w}.\nIntro {w}.\nSee the disclaimer.\nMore {w}.\nClosing {w}.",
                page=n,
            )
            for n, w in enumerate(words, start=1)
        ]
        document = _normalize(*pages, page_count=5)
        assert document.text.count("See the disclaimer.") == 5

    def test_short_pages_give_no_evidence_so_nothing_is_removed(self) -> None:
        # Every line of a three-line page is an "edge"; a sentence repeated on
        # each one cannot be told apart from a header, so it is kept.
        pages = [
            _p(f"Opening {n}.\nSee the disclaimer.\nClosing {n}.", page=n) for n in range(1, 6)
        ]
        document = _normalize(*pages, page_count=5)
        assert document.stats.removed_boilerplate_lines == 0
        assert document.text.count("See the disclaimer.") == 5

    def test_short_documents_keep_repeated_lines(self) -> None:
        document = _normalize(_p("Header\nA.", page=1), _p("Header\nB.", page=2), page_count=2)
        assert document.text.count("Header") == 2


class TestLimits:
    def test_character_limit_applies_to_normalized_text(self) -> None:
        with pytest.raises(DocumentLimitExceededError):
            _normalize(_p("x" * 2000), limits=ParseLimits(max_characters=1000))

    def test_block_limit(self) -> None:
        with pytest.raises(DocumentLimitExceededError):
            _normalize(*[_p("x")] * 11, limits=ParseLimits(max_blocks=10))
