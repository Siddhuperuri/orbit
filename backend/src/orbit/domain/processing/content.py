"""The internal document representation (ADR-0012).

There are exactly two shapes a document takes between its bytes and its chunks,
and every format passes through both:

1. **`ExtractedDocument`** -- what a parser produces. Blocks of raw text in
   reading order, each with a kind and a source location. Parsers are obliged
   to produce this and forbidden from producing anything else, so no parser can
   invent its own data model and nothing downstream knows what format a
   document came from.
2. **`ParsedDocument`** -- what the shared normalization stage produces from
   it. Normalized text, stable ordinals, character offsets into the normalized
   document text, page ranges, and the heading path each block sits under.

The split exists because ADR-0012 requires offsets to be assigned *after*
normalization -- an offset into un-normalized text would disagree with the text
that is stored and cited. A parser therefore cannot assign offsets at all; it
has not seen the final text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from orbit.domain.embeddings import build_embedding_input, embedding_input_sha256


class DocumentFormat(StrEnum):
    PDF = "pdf"
    MARKDOWN = "markdown"
    TEXT = "text"


class BlockKind(StrEnum):
    """What a block *is*, which is what the chunker's boundary rules act on."""

    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CODE = "code"
    QUOTE = "quote"

    @property
    def preserves_line_breaks(self) -> bool:
        """Whether newlines inside the block carry meaning.

        In prose a newline is a layout artefact of wherever the source wrapped
        the line. In a code block or a table row it is structure, and collapsing
        it produces text that neither reads nor embeds correctly.
        """
        return self in (BlockKind.CODE, BlockKind.TABLE)


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Where a block came from in the original file.

    Every field is optional because formats differ in what they can say: a PDF
    knows pages but not lines; Markdown and plain text know lines but have no
    pages. `None` means "this format has no such concept", never "unknown".
    """

    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None

    def __post_init__(self) -> None:
        if self.page is not None and self.page < 1:
            msg = "page is 1-based."
            raise ValueError(msg)
        if self.line_start is not None and self.line_start < 1:
            msg = "line_start is 1-based."
            raise ValueError(msg)
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            msg = "line_end precedes line_start."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ExtractedBlock:
    """One block of raw, un-normalized text, as a parser found it."""

    kind: BlockKind
    text: str
    location: SourceLocation = field(default_factory=SourceLocation)
    #: 1-6 for `HEADING`, `None` otherwise. Drives the heading path.
    heading_level: int | None = None

    def __post_init__(self) -> None:
        if (self.kind is BlockKind.HEADING) != (self.heading_level is not None):
            msg = "heading_level is required for headings and forbidden otherwise."
            raise ValueError(msg)
        if self.heading_level is not None and not 1 <= self.heading_level <= 6:  # noqa: PLR2004
            msg = "heading_level must be between 1 and 6."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """A parser's entire output."""

    format: DocumentFormat
    blocks: tuple[ExtractedBlock, ...]
    page_count: int | None = None
    #: Title, author, and similar. Attacker-controlled and advisory only: never
    #: rendered as HTML, never used for authorization or path construction.
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    #: Non-fatal observations for the operator log -- a fallback encoding was
    #: used, some pages had no text layer. Never shown to the user.
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParseLimits:
    """Bounds a hostile document cannot push the worker past.

    A hard time limit alone is not enough: a document can produce an enormous
    amount of text just under the deadline and break the next stage instead.
    """

    max_pages: int = 2_000
    max_characters: int = 5_000_000
    max_blocks: int = 250_000
    max_chunks: int = 20_000


@dataclass(frozen=True, slots=True)
class TextBlock:
    """One normalized block, with provenance."""

    ordinal: int
    kind: BlockKind
    text: str
    #: Offsets into `ParsedDocument.text`; `text == document.text[start:end]`.
    char_start: int
    char_end: int
    #: A paragraph that continues across a page break becomes one block
    #: spanning both pages, so the range is a range, not a single page.
    page_start: int | None
    page_end: int | None
    #: The headings this block sits under, outermost first. A heading block's
    #: own path includes itself.
    heading_path: tuple[str, ...]
    heading_level: int | None = None
    location: SourceLocation = field(default_factory=SourceLocation)


@dataclass(frozen=True, slots=True)
class NormalizationStats:
    """What normalization changed, for the operator log."""

    input_blocks: int = 0
    dropped_empty_blocks: int = 0
    removed_boilerplate_lines: int = 0
    merged_across_pages: int = 0
    replacement_characters: int = 0


#: Separator placed between blocks in `ParsedDocument.text`. A blank line, so
#: that the stored text of a chunk spanning blocks reads as paragraphs.
BLOCK_SEPARATOR = "\n\n"


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    format: DocumentFormat
    blocks: tuple[TextBlock, ...]
    text: str
    page_count: int | None = None
    language: str | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    stats: NormalizationStats = field(default_factory=NormalizationStats)

    @property
    def is_empty(self) -> bool:
        return not self.blocks

    @property
    def character_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A retrievable passage, before it has an embedding.

    `text` is exactly `document.text[char_start:char_end]`, so a citation
    resolved from these offsets shows precisely what was retrieved. The heading
    path is kept separate rather than spliced into `text` for the same reason,
    and is prepended only to what is *embedded* (`embedding_input`).
    """

    ordinal: int
    text: str
    token_count: int
    char_start: int
    char_end: int
    page_start: int | None
    page_end: int | None
    heading_path: tuple[str, ...]

    @property
    def heading_path_text(self) -> str | None:
        return " > ".join(self.heading_path) if self.heading_path else None

    @property
    def embedding_input(self) -> str:
        """What the embedding provider sees.

        The heading is often where the discriminating term lives while the body
        says "it" (ADR-0013), so it is prepended here -- and only here.
        """
        return build_embedding_input(self.heading_path_text, self.text)

    @property
    def embedding_input_sha256(self) -> str:
        """Identity of the embedding input. Equal hashes get equal vectors in
        a given space, which is what makes reusing a stored vector safe."""
        return embedding_input_sha256(self.heading_path_text, self.text)

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """ADR-0013's parameters. Changing any of them changes `version`."""

    target_tokens: int = 512
    max_tokens: int = 768
    overlap_tokens: int = 64
    min_tokens: int = 32

    def __post_init__(self) -> None:
        if not 0 < self.min_tokens <= self.target_tokens <= self.max_tokens:
            msg = "Chunking requires 0 < min_tokens <= target_tokens <= max_tokens."
            raise ValueError(msg)
        if not 0 <= self.overlap_tokens < self.target_tokens:
            msg = "overlap_tokens must be non-negative and below target_tokens."
            raise ValueError(msg)

    @property
    def version(self) -> str:
        """Recorded on every chunk row, so a corpus chunked under different
        parameters is a query rather than a guess. Fits `String(32)`."""
        return f"sa1-{self.target_tokens}-{self.max_tokens}-{self.overlap_tokens}-{self.min_tokens}"


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """A chunk and its vector, ready to index."""

    draft: ChunkDraft
    #: Any float sequence -- in practice a compact `array('f')`, because ten
    #: thousand 1536-dimension vectors as Python float lists is ~360 MB.
    #: Validated against the space before it gets here
    #: (`orbit.domain.embeddings.to_indexable_vector`).
    embedding: Sequence[float]
    #: When the provider produced this vector -- earlier than now if it was
    #: reused from an identical input already in the index.
    embedded_at: datetime
