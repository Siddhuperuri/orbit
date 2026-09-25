"""Structure-aware, token-bounded chunking with overlap (ADR-0013).

Chunks are built from the normalized document's blocks, not from its raw
character stream, so the question at every boundary is "where does this idea
end", not "where is character 2000".

**Units.** Every block becomes one unit, unless it alone exceeds the hard
maximum -- then it is split at sentence boundaries, and only a single sentence
longer than the maximum is split further, at word boundaries (and, for a
pathological run with no whitespace at all, at a fixed character width).
Code and tables split at line boundaries instead.

**Assembly.** Units accumulate until the next would push the chunk past the
target size. Headings start a new chunk rather than landing at the end of the
previous one, and a heading is carried forward with the content it introduces
rather than being stranded. A section too small to stand alone (under the
minimum) is merged forward instead of becoming a noise chunk.

**Overlap.** A new chunk begins with the last sentences of the previous one --
whole sentences, up to the overlap budget, and only within the same section --
so a fact that straddles a boundary is fully present in at least one chunk.

**Duplicates.** A chunk whose text is identical (ignoring case and whitespace)
to an earlier chunk of the same document is dropped. Repeated boilerplate --
the same disclaimer after every section -- would otherwise crowd real content
out of every result list.

Pure and deterministic: no I/O, no randomness, no clock. Same document and
configuration, same chunks -- which is what makes reprocessing idempotent.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from orbit.domain.ports.processing import TokenCounter
from orbit.domain.processing.content import (
    BlockKind,
    ChunkDraft,
    ChunkingConfig,
    ParsedDocument,
    TextBlock,
)
from orbit.infrastructure.chunking.sentences import sentence_spans
from orbit.infrastructure.chunking.tokens import ApproximateTokenCounter

_WORD = re.compile(r"\S+")
_LINE = re.compile(r"[^\n]*\n?")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class _Unit:
    """An indivisible piece of a chunk: a span of the document text."""

    start: int
    end: int
    tokens: int
    kind: BlockKind
    page_start: int | None
    page_end: int | None
    heading_path: tuple[str, ...]

    @property
    def is_heading(self) -> bool:
        return self.kind is BlockKind.HEADING


class StructureAwareChunker:
    def __init__(
        self,
        config: ChunkingConfig | None = None,
        counter: TokenCounter | None = None,
    ) -> None:
        self._config = config or ChunkingConfig()
        self._counter = counter or ApproximateTokenCounter()

    @property
    def version(self) -> str:
        return self._config.version

    @property
    def config(self) -> ChunkingConfig:
        return self._config

    def chunk(self, document: ParsedDocument) -> Sequence[ChunkDraft]:
        if document.is_empty:
            return []
        text = document.text
        units = [unit for block in document.blocks for unit in self._units(text, block)]
        spans = self._assemble(text, units)
        spans = self._merge_small_tail(text, spans)
        return self._finalize(text, spans)

    # -- units -----------------------------------------------------------------

    def _units(self, text: str, block: TextBlock) -> list[_Unit]:
        tokens = self._counter.count(block.text)
        if tokens <= self._config.max_tokens:
            return [self._unit(block, block.char_start, block.char_end, tokens)]

        if block.kind.preserves_line_breaks:
            # The line's own newline is excluded, so no unit -- and therefore
            # no chunk -- ends in whitespace.
            pieces = [
                (
                    block.char_start + match.start(),
                    block.char_start + match.start() + len(match.group().rstrip()),
                )
                for match in _LINE.finditer(block.text)
                if match.group().strip()
            ]
        else:
            pieces = [
                (block.char_start + start, block.char_start + end)
                for start, end in sentence_spans(block.text)
            ]

        units: list[_Unit] = []
        # Packed to leave room for the overlap a following chunk will prepend,
        # so chunks cut from one long block land near the target, not above it.
        budget = self._config.target_tokens - self._config.overlap_tokens
        for start, end in _pack(text, pieces, self._counter, budget):
            piece_tokens = self._counter.count(text[start:end])
            if piece_tokens <= self._config.max_tokens:
                units.append(self._unit(block, start, end, piece_tokens))
            else:
                units.extend(self._split_long_piece(text, block, start, end))
        return units

    def _split_long_piece(self, text: str, block: TextBlock, start: int, end: int) -> list[_Unit]:
        """A single sentence (or line) beyond the hard maximum.

        Split at word boundaries; a "word" that is itself too long -- a base64
        blob, a URL-encoded dump -- is cut at a fixed width, which is the one
        place a mid-token cut is accepted, because the alternative is dropping
        the text or exceeding the maximum.
        """
        segment = text[start:end]
        words: list[tuple[int, int]] = []
        width = self._config.target_tokens
        for match in _WORD.finditer(segment):
            word_start, word_end = start + match.start(), start + match.end()
            if self._counter.count(text[word_start:word_end]) <= self._config.target_tokens:
                words.append((word_start, word_end))
                continue
            words.extend(
                (offset, min(offset + width, word_end))
                for offset in range(word_start, word_end, width)
            )
        return [
            self._unit(
                block, piece_start, piece_end, self._counter.count(text[piece_start:piece_end])
            )
            for piece_start, piece_end in _pack(
                text, words, self._counter, self._config.target_tokens
            )
        ]

    @staticmethod
    def _unit(block: TextBlock, start: int, end: int, tokens: int) -> _Unit:
        return _Unit(
            start=start,
            end=end,
            tokens=max(tokens, 1),
            kind=block.kind,
            page_start=block.page_start,
            page_end=block.page_end,
            heading_path=block.heading_path,
        )

    # -- assembly --------------------------------------------------------------

    def _assemble(self, text: str, units: list[_Unit]) -> list[list[_Unit]]:
        config = self._config
        chunks: list[list[_Unit]] = []
        current: list[_Unit] = []
        # A running total rather than a recount per unit: the text between two
        # units is whitespace, which the counter scores independently, so the
        # sum is exact (or, across a hard-cut word, a slight overestimate --
        # the safe direction for a maximum).
        current_tokens = 0

        for unit in units:
            if not current:
                current, current_tokens = [unit], unit.tokens
                continue

            gap = self._counter.count(text[current[-1].end : unit.start])
            candidate = current_tokens + gap + unit.tokens

            if (
                unit.is_heading
                and not current[-1].is_heading
                and current_tokens >= config.min_tokens
            ):
                # A new section starts a new chunk. No overlap across the
                # boundary: the next section should not open with the tail of
                # an unrelated one.
                chunks.append(current)
                current, current_tokens = [unit], unit.tokens
                continue

            fits_target = candidate <= config.target_tokens
            # Past the target but within the maximum, two kinds of chunk still
            # take the unit: one holding only headings (a heading must not
            # stand alone), and one still under the minimum (it would be
            # noise on its own, so it is merged forward instead).
            undersized = (
                all(member.is_heading for member in current) or current_tokens < config.min_tokens
            )
            if fits_target or (undersized and candidate <= config.max_tokens):
                current.append(unit)
                current_tokens = candidate
                continue

            # Over target. Headings at the end of the current chunk belong to
            # what follows them, so they move to the next chunk -- unless that
            # would push the next chunk past the hard maximum.
            carried: list[_Unit] = []
            while len(current) > 1 and current[-1].is_heading:
                carried.insert(0, current.pop())
            if carried and (
                self._span_tokens(text, carried[0].start, unit.end) > config.max_tokens
                or self._span_tokens(text, current[0].start, current[-1].end) < config.min_tokens
            ):
                # Carrying would either overfill the next chunk or leave this
                # one as noise; the headings stay where they are.
                current.extend(carried)
                carried = []

            chunks.append(current)
            overlap = [] if carried else self._overlap(text, current, unit)
            current = [*overlap, *carried, unit]
            current_tokens = self._span_tokens(text, current[0].start, unit.end)

        if current:
            chunks.append(current)
        return chunks

    def _overlap(self, text: str, previous: list[_Unit], following: _Unit) -> list[_Unit]:
        """The previous chunk's trailing sentences, within the overlap budget.

        Whole sentences only, same section only, prose only. The overlap is
        dropped entirely if it would push the next chunk past the maximum --
        losing some context at one boundary is better than an oversized chunk.
        """
        config = self._config
        if config.overlap_tokens == 0:
            return []

        collected: list[_Unit] = []
        budget = config.overlap_tokens
        for unit in reversed(previous):
            if (
                unit.is_heading
                or unit.kind.preserves_line_breaks
                or unit.heading_path != following.heading_path
            ):
                break
            fits_whole = True
            for start, end in reversed(sentence_spans(text[unit.start : unit.end])):
                sentence_start, sentence_end = unit.start + start, unit.start + end
                tokens = self._counter.count(text[sentence_start:sentence_end])
                if tokens > budget:
                    fits_whole = False
                    break
                budget -= tokens
                collected.insert(
                    0,
                    replace(
                        unit,
                        start=sentence_start,
                        end=sentence_end,
                        tokens=tokens,
                        # The tail of a page-spanning paragraph is on its last page.
                        page_start=unit.page_end,
                    ),
                )
            if not fits_whole:
                break

        if (
            collected
            and self._span_tokens(text, collected[0].start, following.end) > config.max_tokens
        ):
            return []
        return collected

    def _merge_small_tail(self, text: str, chunks: list[list[_Unit]]) -> list[list[_Unit]]:
        """A final chunk below the minimum joins the previous one if it fits.

        If it does not fit, it stands alone: a small chunk is noise, but losing
        the end of a document is worse. A document whose *only* chunk is small
        keeps it -- a two-line note is still worth retrieving.
        """
        if len(chunks) < 2:  # noqa: PLR2004
            return chunks
        last, previous = chunks[-1], chunks[-2]
        if self._span_tokens(text, last[0].start, last[-1].end) >= self._config.min_tokens:
            return chunks
        if self._span_tokens(text, previous[0].start, last[-1].end) > self._config.max_tokens:
            return chunks
        fresh = [unit for unit in last if unit.start >= previous[-1].end]
        return [*chunks[:-2], [*previous, *fresh]]

    def _finalize(self, text: str, chunks: list[list[_Unit]]) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        seen: set[str] = set()
        for units in chunks:
            start, end = units[0].start, units[-1].end
            content = text[start:end]
            key = _WHITESPACE.sub(" ", content).strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            pages = [page for unit in units for page in (unit.page_start, unit.page_end) if page]
            drafts.append(
                ChunkDraft(
                    ordinal=len(drafts),
                    text=content,
                    token_count=max(self._counter.count(content), 1),
                    char_start=start,
                    char_end=end,
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    heading_path=_common_prefix([unit.heading_path for unit in units]),
                )
            )
        return drafts

    def _span_tokens(self, text: str, start: int, end: int) -> int:
        return self._counter.count(text[start:end])


def _pack(
    text: str, pieces: list[tuple[int, int]], counter: TokenCounter, budget: int
) -> list[tuple[int, int]]:
    """Greedily join adjacent pieces into spans of at most `budget` tokens.

    A piece already over budget is returned alone, for the caller to split
    further.
    """
    packed: list[tuple[int, int]] = []
    for start, end in pieces:
        if packed and counter.count(text[packed[-1][0] : end]) <= budget:
            packed[-1] = (packed[-1][0], end)
        else:
            packed.append((start, end))
    return packed


def _common_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    if not paths:
        return ()
    prefix = paths[0]
    for path in paths[1:]:
        length = 0
        for left, right in zip(prefix, path, strict=False):
            if left != right:
                break
            length += 1
        prefix = prefix[:length]
    return prefix
