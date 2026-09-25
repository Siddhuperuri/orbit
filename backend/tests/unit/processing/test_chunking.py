"""Chunking: a pure subsystem, tested for properties rather than exact counts.

ADR-0013 is explicit that token counts are approximate, so these tests assert
what must hold for *any* input -- bounds, exact offsets, sentence integrity,
determinism, overlap, heading provenance -- over deliberately awkward documents:
empty, tiny, enormous, one endless sentence, a wall of unbroken characters,
repeated boilerplate, page-spanning paragraphs.
"""

from __future__ import annotations

import io
import itertools
import random
import re

import pytest

from orbit.domain.processing.content import (
    BlockKind,
    ChunkDraft,
    ChunkingConfig,
    DocumentFormat,
    ExtractedBlock,
    ExtractedDocument,
    ParsedDocument,
    ParseLimits,
    SourceLocation,
)
from orbit.infrastructure.chunking.sentences import sentence_spans
from orbit.infrastructure.chunking.structure_aware import StructureAwareChunker
from orbit.infrastructure.chunking.tokens import ApproximateTokenCounter
from orbit.infrastructure.parsing.markdown import MarkdownParser
from orbit.infrastructure.parsing.normalization import DocumentNormalizer

COUNTER = ApproximateTokenCounter()
CONFIG = ChunkingConfig()


def _parse(blocks: list[ExtractedBlock], page_count: int | None = None) -> ParsedDocument:
    return DocumentNormalizer().normalize(
        ExtractedDocument(format=DocumentFormat.TEXT, blocks=tuple(blocks), page_count=page_count),
        limits=ParseLimits(),
    )


def _markdown(text: str) -> ParsedDocument:
    extracted = MarkdownParser().parse(io.BytesIO(text.encode()), limits=ParseLimits())
    return DocumentNormalizer().normalize(extracted, limits=ParseLimits())


def _chunk(document: ParsedDocument, config: ChunkingConfig = CONFIG) -> list[ChunkDraft]:
    return list(StructureAwareChunker(config).chunk(document))


def _prose(sentences: int, seed: int = 7) -> str:
    rng = random.Random(seed)  # noqa: S311 -- seeded fixture data
    vocabulary = [
        "refresh",
        "token",
        "rotation",
        "family",
        "revoked",
        "session",
        "cookie",
        "workspace",
        "member",
        "policy",
        "document",
        "version",
        "chunk",
        "embedding",
        "retrieval",
        "citation",
        "latency",
        "provider",
        "outage",
    ]
    out = []
    for index in range(sentences):
        words = [rng.choice(vocabulary) for _ in range(rng.randint(6, 18))]
        out.append(f"Fact {index} says {' '.join(words)}.")
    return " ".join(out)


def _assert_invariants(document: ParsedDocument, chunks: list[ChunkDraft]) -> None:
    """What every chunking of every document must satisfy."""
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    previous_start = -1
    for chunk in chunks:
        assert document.text[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.text == chunk.text.strip(), "chunks never start or end in whitespace"
        assert 0 < chunk.token_count <= CONFIG.max_tokens
        assert chunk.token_count == COUNTER.count(chunk.text)
        assert chunk.char_start > previous_start, "chunks advance through the document"
        previous_start = chunk.char_start
    covered = [False] * len(document.text)
    for chunk in chunks:
        for index in range(chunk.char_start, chunk.char_end):
            covered[index] = True
    uncovered = "".join(ch for ch, hit in zip(document.text, covered, strict=True) if not hit)
    assert not uncovered.strip(), "no text is lost between chunks"


class TestEdgeCases:
    def test_empty_document_has_no_chunks(self) -> None:
        assert _chunk(_parse([])) == []

    def test_whitespace_only_document_has_no_chunks(self) -> None:
        assert _chunk(_parse([ExtractedBlock(kind=BlockKind.PARAGRAPH, text=" \n\t ")])) == []

    def test_a_document_with_little_text_keeps_its_single_small_chunk(self) -> None:
        document = _parse([ExtractedBlock(kind=BlockKind.PARAGRAPH, text="Call Sam.")])
        chunks = _chunk(document)
        assert [chunk.text for chunk in chunks] == ["Call Sam."]
        assert chunks[0].token_count < CONFIG.min_tokens

    def test_huge_document_stays_bounded_and_complete(self) -> None:
        paragraphs = [
            ExtractedBlock(kind=BlockKind.PARAGRAPH, text=_prose(12, seed=seed))
            for seed in range(400)
        ]
        document = _parse(paragraphs)
        chunks = _chunk(document)
        assert len(chunks) > 50
        _assert_invariants(document, chunks)

    def test_one_endless_sentence_is_split_at_words_within_the_maximum(self) -> None:
        text = " ".join(f"word{index}" for index in range(5000))
        document = _parse([ExtractedBlock(kind=BlockKind.PARAGRAPH, text=text)])
        chunks = _chunk(document)
        assert len(chunks) > 1
        _assert_invariants(document, chunks)
        for chunk in chunks:
            assert re.match(r"^word\d+", chunk.text) and re.search(r"word\d+$", chunk.text)

    def test_an_unbroken_run_of_characters_is_hard_split_rather_than_dropped(self) -> None:
        # Non-repeating on purpose: identical windows would be (correctly)
        # deduplicated, which is a different property from "nothing is lost".
        rng = random.Random(11)  # noqa: S311 -- seeded fixture data
        latin = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(20_000))
        blob = latin + "".join(chr(0x4E00 + rng.randrange(2000)) for _ in range(3000))
        document = _parse([ExtractedBlock(kind=BlockKind.PARAGRAPH, text=blob)])
        chunks = _chunk(document)
        _assert_invariants(document, chunks)
        assert sum(len(chunk.text) for chunk in chunks) >= len(blob)

    def test_repeated_whitespace_never_reaches_a_chunk(self) -> None:
        document = _parse(
            [ExtractedBlock(kind=BlockKind.PARAGRAPH, text="a   lot\n\n\n of    space   ")]
        )
        assert [chunk.text for chunk in _chunk(document)] == ["a lot of space"]


class TestBoundaries:
    def test_long_prose_splits_only_at_sentence_boundaries(self) -> None:
        document = _parse([ExtractedBlock(kind=BlockKind.PARAGRAPH, text=_prose(300))])
        chunks = _chunk(document)
        assert len(chunks) > 3
        _assert_invariants(document, chunks)
        for chunk in chunks:
            assert chunk.text.startswith("Fact "), chunk.text[:40]
            assert chunk.text.endswith("."), chunk.text[-40:]

    def test_chunks_aim_for_the_target_not_the_maximum(self) -> None:
        document = _parse([ExtractedBlock(kind=BlockKind.PARAGRAPH, text=_prose(600))])
        chunks = _chunk(document)
        middle = chunks[1:-1]
        average = sum(chunk.token_count for chunk in middle) / len(middle)
        assert CONFIG.target_tokens * 0.6 <= average <= CONFIG.target_tokens * 1.15

    def test_small_paragraphs_are_packed_together_not_chunked_one_by_one(self) -> None:
        blocks = [
            ExtractedBlock(kind=BlockKind.PARAGRAPH, text=_prose(2, seed=seed))
            for seed in range(60)
        ]
        document = _parse(blocks)
        chunks = _chunk(document)
        assert len(chunks) < len(blocks) / 3
        _assert_invariants(document, chunks)

    def test_a_short_trailing_chunk_is_merged_into_the_previous_one(self) -> None:
        document = _parse(
            [
                ExtractedBlock(kind=BlockKind.PARAGRAPH, text=_prose(40)),
                ExtractedBlock(kind=BlockKind.PARAGRAPH, text="Tail."),
            ]
        )
        chunks = _chunk(document)
        assert chunks[-1].text.endswith("Tail.")
        assert chunks[-1].token_count >= CONFIG.min_tokens

    def test_code_blocks_split_at_line_boundaries(self) -> None:
        code = "\n".join(f"value_{index} = compute({index}, 'parameter')" for index in range(400))
        document = _markdown(f"```python\n{code}\n```\n")
        chunks = _chunk(document)
        assert len(chunks) > 1
        _assert_invariants(document, chunks)
        for chunk in chunks:
            assert all(line.startswith("value_") for line in chunk.text.split("\n"))


class TestOverlap:
    def test_consecutive_chunks_share_whole_trailing_sentences(self) -> None:
        document = _parse([ExtractedBlock(kind=BlockKind.PARAGRAPH, text=_prose(200))])
        chunks = _chunk(document)
        for previous, following in itertools.pairwise(chunks):
            assert following.char_start < previous.char_end, "chunks overlap"
            shared = document.text[following.char_start : previous.char_end]
            assert shared.startswith("Fact ") and shared.endswith(".")
            assert COUNTER.count(shared) <= CONFIG.overlap_tokens

    def test_no_overlap_when_disabled(self) -> None:
        config = ChunkingConfig(overlap_tokens=0)
        document = _parse([ExtractedBlock(kind=BlockKind.PARAGRAPH, text=_prose(200))])
        chunks = _chunk(document, config)
        for previous, following in itertools.pairwise(chunks):
            assert following.char_start >= previous.char_end

    def test_overlap_never_crosses_a_section_boundary(self) -> None:
        document = _markdown(f"# One\n\n{_prose(60)}\n\n# Two\n\n{_prose(60, seed=9)}\n")
        chunks = _chunk(document)
        second_section = document.text.index("\n\nTwo\n\n") + 2
        for chunk in chunks:
            if chunk.char_end > second_section:
                assert chunk.char_start >= second_section or chunk.char_end <= second_section


class TestStructure:
    def test_heading_paths_follow_the_section_each_chunk_is_in(self) -> None:
        document = _markdown(
            f"# Guide\n\n## Security\n\n{_prose(80)}\n\n## Operations\n\n{_prose(80, seed=3)}\n"
        )
        chunks = _chunk(document)
        paths = {chunk.heading_path for chunk in chunks}
        assert ("Guide", "Security") in paths
        assert ("Guide", "Operations") in paths
        for chunk in chunks:
            if chunk.heading_path_text:
                assert chunk.embedding_input.startswith(chunk.heading_path_text + "\n\n")
                assert chunk.embedding_input.endswith(chunk.text)

    def test_a_heading_starts_a_chunk_and_is_never_stranded_at_the_end_of_one(self) -> None:
        document = _markdown(f"# A\n\n{_prose(70)}\n\n# B\n\n{_prose(70, seed=5)}\n")
        chunks = _chunk(document)
        for chunk in chunks:
            last_line = chunk.text.rsplit("\n\n", 1)[-1]
            assert last_line not in {"A", "B"}, "heading left dangling at a chunk's end"
        assert any(chunk.text.startswith("B\n\n") for chunk in chunks)

    def test_page_ranges_are_carried_from_blocks(self) -> None:
        blocks = [
            ExtractedBlock(
                kind=BlockKind.PARAGRAPH,
                text=_prose(30, seed=page),
                location=SourceLocation(page=page),
            )
            for page in range(1, 7)
        ]
        document = _parse(blocks, page_count=6)
        chunks = _chunk(document)
        assert chunks[0].page_start == 1
        assert chunks[-1].page_end == 6
        for chunk in chunks:
            assert chunk.page_start is not None and chunk.page_end is not None
            assert chunk.page_start <= chunk.page_end

    def test_formats_without_pages_have_no_page_range(self) -> None:
        chunks = _chunk(_markdown("# T\n\nSome text here.\n"))
        assert all(chunk.page_start is None and chunk.page_end is None for chunk in chunks)


class TestDeduplication:
    def test_identical_repeated_sections_become_one_chunk(self) -> None:
        disclaimer = "This document is confidential and intended only for the named recipient. " * 8
        text = "\n\n".join(
            f"# Section {name}\n\n{_prose(40, seed=seed)}\n\n# Notice\n\n{disclaimer}"
            for seed, name in enumerate(["Alpha", "Bravo", "Charlie"])
        )
        chunks = _chunk(_markdown(text))
        keys = [re.sub(r"\s+", " ", chunk.text).casefold() for chunk in chunks]
        assert len(keys) == len(set(keys))
        assert sum("confidential" in key for key in keys) < 3

    def test_duplicates_differing_only_in_case_and_spacing_are_dropped(self) -> None:
        block = _prose(50)
        document = _parse(
            [
                ExtractedBlock(kind=BlockKind.HEADING, text="Copy", heading_level=1),
                ExtractedBlock(kind=BlockKind.PARAGRAPH, text=block),
                ExtractedBlock(kind=BlockKind.HEADING, text="copy", heading_level=1),
                ExtractedBlock(kind=BlockKind.PARAGRAPH, text=block.upper()),
            ]
        )
        chunks = _chunk(document)
        assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
        assert len({re.sub(r"\s+", " ", c.text).casefold() for c in chunks}) == len(chunks)


class TestDeterminism:
    def test_same_input_same_chunks(self) -> None:
        text = f"# Guide\n\n{_prose(150)}\n\n## Part\n\n{_prose(90, seed=2)}\n"
        assert _chunk(_markdown(text)) == _chunk(_markdown(text))

    def test_version_identifies_the_configuration(self) -> None:
        assert StructureAwareChunker().version == "sa1-512-768-64-32"
        assert StructureAwareChunker(ChunkingConfig(target_tokens=400)).version != (
            StructureAwareChunker().version
        )
        assert len(StructureAwareChunker().version) <= 32  # chunks.chunker_version

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"min_tokens": 0},
            {"target_tokens": 900},
            {"overlap_tokens": 600},
            {"min_tokens": 600},
        ],
    )
    def test_incoherent_configuration_is_rejected(self, kwargs: dict[str, int]) -> None:
        with pytest.raises(ValueError):
            ChunkingConfig(**kwargs)


class TestTokenCounter:
    def test_empty_text_is_zero(self) -> None:
        assert COUNTER.count("") == 0
        assert COUNTER.count("   \t ") == 0

    def test_common_words_are_one_token_long_words_several(self) -> None:
        assert COUNTER.count("the cat sat") == 3
        assert COUNTER.count("internationalization") == 3

    def test_numbers_group_in_threes_and_punctuation_counts(self) -> None:
        assert COUNTER.count("1234567") == 3
        assert COUNTER.count("a, b.") == 4

    def test_non_latin_scripts_count_per_character(self) -> None:
        assert COUNTER.count("\u4e2d\u6587\u5b57") == 3

    def test_is_additive_across_whitespace(self) -> None:
        left, right = "Tokens rotate.", "Families revoke 42 sessions!"
        assert COUNTER.count(f"{left} {right}") == COUNTER.count(left) + COUNTER.count(right)


class TestSentences:
    def test_splits_on_terminal_punctuation(self) -> None:
        text = 'First one. Second one! "Third?" Fourth.'
        assert [text[s:e] for s, e in sentence_spans(text)] == [
            "First one.",
            "Second one!",
            '"Third?"',
            "Fourth.",
        ]

    @pytest.mark.parametrize(
        "text",
        [
            "Use a cache, e.g. Redis for this.",
            "Written by J. Smith in 2020.",
            "See Fig. 4 for details.",
            "Version 2.5 shipped.",
        ],
    )
    def test_abbreviations_initials_and_decimals_are_not_boundaries(self, text: str) -> None:
        assert sentence_spans(text) == [(0, len(text))]

    def test_lowercase_continuation_is_not_a_new_sentence(self) -> None:
        text = "It costs approx. five dollars."
        assert len(sentence_spans(text)) == 1

    def test_spans_are_exact_and_ordered(self) -> None:
        text = "  One.  Two.   Three.  "
        spans = sentence_spans(text)
        assert [text[s:e] for s, e in spans] == ["One.", "Two.", "Three."]
