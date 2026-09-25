"""Retrieval: queries, candidates, fusion, and the results a search returns.

Pure -- no I/O. The two retrievers (PostgreSQL full-text search and pgvector
similarity) live behind `orbit.domain.ports.search.SearchRepository`; this module
decides what a query is, how their ranked lists become one, and what shape a
result has.

**Fusion is Reciprocal Rank Fusion** (ADR-0005, ADR-0021):

    score(chunk) = sum over retrievers r that returned it of 1 / (k + rank_r(chunk))

with `k = 60` and ranks starting at 1. It fuses by *position*, not by score:
`ts_rank_cd` and cosine similarity live on different scales with
query-dependent distributions, so adding them requires a normalisation that
has to be re-tuned per corpus. `min_max_fusion` exists as the evaluated
alternative (docs/benchmarks/retrieval.md) and is not used to serve queries.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from orbit.domain.errors import ValidationError

#: Longer than any question a person types; a pasted document is refused rather
#: than silently embedded at full cost.
MAX_QUERY_CHARACTERS = 2000
MAX_RESULTS = 50
MAX_DOCUMENT_FILTER = 100
#: Matches the document list's limit, so "filter by tag" means the same thing
#: and costs the same in both places (`domain.documents.MAX_FILTER_TAGS`).
MAX_FILTER_TAGS = 5
#: How deep paging may go. Fusion re-ranks a candidate pool, so page N costs
#: the same retrieval as pages 1..N together; this bounds that cost. Someone
#: who has not found it by result 200 needs a better query, not more pages.
MAX_SEARCH_OFFSET = 200

#: The RRF constant from Cormack, Clarke & Buettcher (SIGIR 2009). Not user
#: configuration; changed only with evaluation evidence.
RRF_K = 60

_WHITESPACE = re.compile(r"\s+")


class RetrievalMode(StrEnum):
    HYBRID = "hybrid"
    LEXICAL = "lexical"
    SEMANTIC = "semantic"


class RetrievalMethod(StrEnum):
    """One retriever."""

    LEXICAL = "lexical"
    SEMANTIC = "semantic"


class MatchedBy(StrEnum):
    """Which retrievers returned a result."""

    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    BOTH = "both"


class LexicalMatch(StrEnum):
    """How query terms combine in the full-text query.

    `ANY` (OR) ranks chunks by how much of the query they cover. `ALL` (AND,
    what `plainto_tsquery` does) returns nothing for most natural-language
    questions, because some word of the question is absent from the answer.
    Chosen by measurement: docs/benchmarks/retrieval.md.
    """

    ANY = "any"
    ALL = "all"


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """What a search is narrowed to, beyond the caller's workspace.

    Every field *narrows*. None of them can widen scope: they are conjoined
    with the workspace predicate inside the retrievers' SQL, so a folder, tag,
    or document id belonging to another tenant matches nothing rather than
    reaching across the boundary (ADR-0004).

    Folder and tag semantics deliberately match the document list
    (`domain.documents.DocumentListQuery`): a folder means the documents
    directly in it, not its subfolders, and tags are conjunctive -- a document
    must carry *every* tag named. A filter that meant one thing in Documents
    and another in Search would be a trap.
    """

    #: Restrict to these documents. `None` means the whole workspace. Always
    #: intersected with the caller's workspace -- never a way to widen scope.
    document_ids: frozenset[uuid.UUID] | None = None
    #: Documents filed directly in this folder. Mutually exclusive with `unfiled`.
    folder_id: uuid.UUID | None = None
    #: Documents in no folder at all.
    unfiled: bool = False
    #: A document must carry *every* one of these tags.
    tag_ids: frozenset[uuid.UUID] | None = None
    #: Restrict to these stored content types (e.g. `application/pdf`). The
    #: accepted set is small and closed, so this is a checklist, not free text.
    content_types: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.document_ids is not None:
            if not self.document_ids:
                msg = "document_ids, when given, must name at least one document."
                raise ValidationError(msg)
            if len(self.document_ids) > MAX_DOCUMENT_FILTER:
                msg = f"At most {MAX_DOCUMENT_FILTER} documents can be searched at once."
                raise ValidationError(msg, count=len(self.document_ids))
        if self.folder_id is not None and self.unfiled:
            msg = "Filter by a folder or by 'unfiled', not both."
            raise ValidationError(msg)
        if self.tag_ids is not None:
            if not self.tag_ids:
                msg = "tag_ids, when given, must name at least one tag."
                raise ValidationError(msg)
            if len(self.tag_ids) > MAX_FILTER_TAGS:
                msg = f"A search can be filtered by at most {MAX_FILTER_TAGS} tags."
                raise ValidationError(msg, count=len(self.tag_ids))
        if self.content_types is not None and not self.content_types:
            msg = "content_types, when given, must name at least one type."
            raise ValidationError(msg)

    @property
    def is_narrowed(self) -> bool:
        """Whether anything beyond the workspace is being applied.

        Lets a caller distinguish "no results in this workspace" from "no
        results *with these filters*", which are different things to say.
        """
        return (
            self.document_ids is not None
            or self.folder_id is not None
            or self.unfiled
            or self.tag_ids is not None
            or self.content_types is not None
        )


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str
    limit: int = 10
    mode: RetrievalMode = RetrievalMode.HYBRID
    filters: SearchFilters = field(default_factory=SearchFilters)
    #: Results to skip. Fusion ranks a candidate pool, so a later page is
    #: served by retrieving a deeper pool and slicing it -- not by asking the
    #: retrievers to resume, which they cannot do without changing the ranking.
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_RESULTS:
            msg = f"limit must be between 1 and {MAX_RESULTS}."
            raise ValidationError(msg, limit=self.limit)
        if not 0 <= self.offset <= MAX_SEARCH_OFFSET:
            msg = f"offset must be between 0 and {MAX_SEARCH_OFFSET}."
            raise ValidationError(msg, offset=self.offset)

    @property
    def depth(self) -> int:
        """How many fused results must exist for this page to be filled."""
        return self.offset + self.limit


def normalize_query(raw: str) -> str:
    """The text both retrievers see.

    * NFKC, so full-width, ligature, and compatibility forms match what the
      document normaliser stored (ADR-0012 normalises documents the same way).
    * Control and format characters removed: they carry no meaning, and a
      zero-width character inside a word silently defeats keyword matching.
    * Whitespace collapsed and trimmed.

    Case and punctuation are left alone: the full-text parser folds case and
    understands `ERR-4012` better than a pre-processed `err 4012` would, and
    the embedding model benefits from the original casing.
    """
    text = unicodedata.normalize("NFKC", raw)
    text = "".join(
        char
        for char in text
        if char.isspace() or unicodedata.category(char) not in ("Cc", "Cf", "Cs", "Co")
    )
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        msg = "A search query cannot be empty."
        raise ValidationError(msg)
    if len(text) > MAX_QUERY_CHARACTERS:
        msg = f"A search query is limited to {MAX_QUERY_CHARACTERS:,} characters."
        raise ValidationError(msg, length=len(text))
    return text


# ---------------------------------------------------------------------------
# Candidates and fusion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """One retriever's opinion of one chunk.

    `score` is the retriever's native score: `ts_rank_cd` for lexical, cosine
    similarity for semantic. They are not comparable with each other.
    """

    chunk_id: uuid.UUID
    score: float


@dataclass(frozen=True, slots=True)
class RankedList:
    """A retriever's candidates, best first."""

    method: RetrievalMethod
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True, slots=True)
class MethodEvidence:
    method: RetrievalMethod
    #: 1-based position in that retriever's list.
    rank: int
    score: float


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    chunk_id: uuid.UUID
    fused_score: float
    evidence: tuple[MethodEvidence, ...]

    def from_method(self, method: RetrievalMethod) -> MethodEvidence | None:
        return next((e for e in self.evidence if e.method is method), None)

    @property
    def matched_by(self) -> MatchedBy:
        methods = {e.method for e in self.evidence}
        if methods == {RetrievalMethod.LEXICAL, RetrievalMethod.SEMANTIC}:
            return MatchedBy.BOTH
        return MatchedBy(next(iter(methods)).value)

    @property
    def best_rank(self) -> int:
        return min(e.rank for e in self.evidence)


def _evidence(lists: Iterable[RankedList]) -> dict[uuid.UUID, list[MethodEvidence]]:
    collected: dict[uuid.UUID, list[MethodEvidence]] = {}
    for ranked in lists:
        seen: set[uuid.UUID] = set()
        for candidate in ranked.candidates:
            if candidate.chunk_id in seen:  # a retriever listing a chunk twice counts once
                continue
            seen.add(candidate.chunk_id)
            collected.setdefault(candidate.chunk_id, []).append(
                MethodEvidence(method=ranked.method, rank=len(seen), score=candidate.score)
            )
    return collected


def _ordered(scored: dict[uuid.UUID, tuple[float, list[MethodEvidence]]]) -> list[FusedCandidate]:
    fused = [
        FusedCandidate(chunk_id=chunk_id, fused_score=score, evidence=tuple(evidence))
        for chunk_id, (score, evidence) in scored.items()
    ]
    # Ties -- common in RRF, e.g. rank 1 in one list vs rank 1 in the other --
    # break on the best single rank, then on id, so a query always returns
    # the same order.
    fused.sort(key=lambda c: (-c.fused_score, c.best_rank, c.chunk_id))
    return fused


def reciprocal_rank_fusion(lists: Sequence[RankedList], *, k: int = RRF_K) -> list[FusedCandidate]:
    if k < 1:
        msg = "k must be positive."
        raise ValueError(msg)
    return _ordered(
        {
            chunk_id: (sum(1.0 / (k + e.rank) for e in evidence), evidence)
            for chunk_id, evidence in _evidence(lists).items()
        }
    )


def min_max_fusion(
    lists: Sequence[RankedList], *, weights: dict[RetrievalMethod, float]
) -> list[FusedCandidate]:
    """Score fusion: each list's scores rescaled to [0, 1], then a weighted sum.

    **Evaluation only.** The baseline RRF is compared against
    (docs/benchmarks/retrieval.md). A chunk missing from a list contributes 0
    from it; a list whose scores are all equal maps them all to 1.
    """
    bounds = {
        ranked.method: (
            min((c.score for c in ranked.candidates), default=0.0),
            max((c.score for c in ranked.candidates), default=0.0),
        )
        for ranked in lists
    }

    def scaled(e: MethodEvidence) -> float:
        low, high = bounds[e.method]
        return 1.0 if high == low else (e.score - low) / (high - low)

    return _ordered(
        {
            chunk_id: (sum(weights.get(e.method, 0.0) * scaled(e) for e in evidence), evidence)
            for chunk_id, evidence in _evidence(lists).items()
        }
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """A searchable chunk as the repository materialises it for display.

    Loaded only for fused winners, and only through a query that re-applies the
    caller's workspace and visibility rules.
    """

    chunk_id: uuid.UUID
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    #: The stored type of the current version's bytes, e.g. `application/pdf`.
    content_type: str
    #: When the document last changed -- a new version, a retitle, a move.
    #: What a reader means by "how current is this".
    document_updated_at: datetime
    version_id: uuid.UUID
    version_number: int
    ordinal: int
    content: str
    heading_path: str | None
    page_from: int | None
    page_to: int | None
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class DocumentRef:
    id: uuid.UUID
    title: str
    content_type: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class VersionRef:
    id: uuid.UUID
    version_number: int


@dataclass(frozen=True, slots=True)
class ChunkRef:
    id: uuid.UUID
    ordinal: int
    text: str


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Where the passage is in the document -- what a citation points at."""

    page_from: int | None
    page_to: int | None
    heading_path: str | None
    #: Offsets into the document's normalised text; `text == text[start:end]`.
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class Relevance:
    #: The ranking score (RRF): comparable within one response only.
    score: float
    #: Each retriever's rank and native score, when it returned this chunk.
    lexical: MethodEvidence | None
    semantic: MethodEvidence | None


@dataclass(frozen=True, slots=True)
class SearchResult:
    #: 1-based position in the response.
    rank: int
    document: DocumentRef
    version: VersionRef
    chunk: ChunkRef
    location: SourceLocation
    relevance: Relevance
    matched_by: MatchedBy

    @classmethod
    def build(cls, rank: int, fused: FusedCandidate, record: ChunkRecord) -> SearchResult:
        return cls(
            rank=rank,
            document=DocumentRef(
                id=record.document_id,
                title=record.document_title,
                content_type=record.content_type,
                updated_at=record.document_updated_at,
            ),
            version=VersionRef(id=record.version_id, version_number=record.version_number),
            chunk=ChunkRef(id=record.chunk_id, ordinal=record.ordinal, text=record.content),
            location=SourceLocation(
                page_from=record.page_from,
                page_to=record.page_to,
                heading_path=record.heading_path,
                char_start=record.char_start,
                char_end=record.char_end,
            ),
            relevance=Relevance(
                score=fused.fused_score,
                lexical=fused.from_method(RetrievalMethod.LEXICAL),
                semantic=fused.from_method(RetrievalMethod.SEMANTIC),
            ),
            matched_by=fused.matched_by,
        )


@dataclass(frozen=True, slots=True)
class Fusion:
    algorithm: str
    k: int


@dataclass(frozen=True, slots=True)
class SearchResponse:
    #: The normalised query both retrievers saw.
    query: str
    mode: RetrievalMode
    #: The retrievers that actually ran. A hybrid request that could not reach
    #: the embedding provider reports only `lexical` here -- and `degraded` --
    #: rather than calling a lexical ranking "hybrid".
    retrievers: tuple[RetrievalMethod, ...]
    degraded: str | None
    fusion: Fusion
    #: How many candidates each retriever contributed before fusion.
    candidates: dict[RetrievalMethod, int]
    results: tuple[SearchResult, ...]
    #: Results skipped to reach this page.
    offset: int = 0
    #: Whether a further page exists. Known exactly -- fusion produced the
    #: whole ranked list and this page is a slice of it -- so there is no
    #: "maybe more" and no total to estimate. A total is deliberately absent:
    #: counting every matching chunk would cost a second full scan to answer
    #: a question ("4,812 results") that helps nobody choose what to read.
    has_more: bool = False
    #: Whether anything beyond the workspace narrowed this search, so an empty
    #: page can offer to widen it rather than only saying "nothing found".
    filtered: bool = False
