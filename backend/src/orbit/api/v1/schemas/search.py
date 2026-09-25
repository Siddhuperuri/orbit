"""Search request/response contracts.

Every result is structured -- document, version, chunk, source location,
relevance per retriever, and ranking -- so a client (and M6's citation binder)
never has to parse meaning out of a string.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from orbit.domain.retrieval import (
    MAX_DOCUMENT_FILTER,
    MAX_FILTER_TAGS,
    MAX_QUERY_CHARACTERS,
    MAX_RESULTS,
    MAX_SEARCH_OFFSET,
    MatchedBy,
    MethodEvidence,
    RetrievalMethod,
    RetrievalMode,
    SearchResponse,
    SearchResult,
)


class SearchRequest(BaseModel):
    """A search, and what to narrow it to.

    Every filter narrows and none widens: they are conjoined with the
    caller's workspace inside the retrievers' SQL, so an id belonging to
    another tenant matches nothing rather than reaching across the boundary.
    Folder and tag semantics match the document list exactly -- a folder is
    the documents directly in it, and tags are conjunctive.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARACTERS)
    limit: int = Field(default=10, ge=1, le=MAX_RESULTS)
    #: Results to skip, for paging. Capped: fusion ranks a candidate pool, so
    #: a deeper page costs a deeper pool.
    offset: int = Field(default=0, ge=0, le=MAX_SEARCH_OFFSET)
    mode: RetrievalMode = RetrievalMode.HYBRID
    #: Restrict to these documents of the workspace. Never widens scope: a
    #: document id from another workspace simply matches nothing.
    document_ids: list[uuid.UUID] | None = Field(
        default=None, min_length=1, max_length=MAX_DOCUMENT_FILTER
    )
    #: Documents filed directly in this folder (not its subfolders).
    folder_id: uuid.UUID | None = None
    #: Documents in no folder. Rejected together with `folder_id`.
    unfiled: bool = False
    #: A document must carry *every* one of these tags.
    tag_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=MAX_FILTER_TAGS)
    #: Stored content types to include, e.g. `application/pdf`.
    content_types: list[str] | None = Field(default=None, min_length=1, max_length=16)


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    #: The stored type of the current version, e.g. `application/pdf`.
    content_type: str
    #: When the document last changed.
    updated_at: datetime


class VersionOut(BaseModel):
    id: uuid.UUID
    version_number: int


class ChunkOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    text: str


class LocationOut(BaseModel):
    page_from: int | None
    page_to: int | None
    heading_path: str | None
    char_start: int
    char_end: int


class EvidenceOut(BaseModel):
    #: 1-based rank within that retriever's candidate list.
    rank: int
    #: The retriever's native score: `ts_rank_cd` (lexical) or cosine
    #: similarity (semantic). Not comparable across retrievers.
    score: float

    @classmethod
    def from_domain(cls, evidence: MethodEvidence | None) -> EvidenceOut | None:
        return None if evidence is None else cls(rank=evidence.rank, score=evidence.score)


class RelevanceOut(BaseModel):
    """Why a result placed where it did.

    These are *diagnostics*, for evaluation, benchmarks, and debugging a
    ranking -- not numbers to put in front of a reader. The fused score has no
    meaning outside the response that produced it, and the retrievers' native
    scores are on different scales; a user shown "0.0164" learns nothing they
    can act on. Clients should present `matched_by` and position instead.
    """

    #: Fused (RRF) score. Orders results within this response; carries no
    #: meaning across queries.
    score: float
    lexical: EvidenceOut | None
    semantic: EvidenceOut | None


class SearchResultOut(BaseModel):
    rank: int
    matched_by: MatchedBy
    document: DocumentOut
    version: VersionOut
    chunk: ChunkOut
    location: LocationOut
    relevance: RelevanceOut

    @classmethod
    def from_domain(cls, result: SearchResult) -> SearchResultOut:
        return cls(
            rank=result.rank,
            matched_by=result.matched_by,
            document=DocumentOut(
                id=result.document.id,
                title=result.document.title,
                content_type=result.document.content_type,
                updated_at=result.document.updated_at,
            ),
            version=VersionOut(id=result.version.id, version_number=result.version.version_number),
            chunk=ChunkOut(
                id=result.chunk.id, ordinal=result.chunk.ordinal, text=result.chunk.text
            ),
            location=LocationOut(
                page_from=result.location.page_from,
                page_to=result.location.page_to,
                heading_path=result.location.heading_path,
                char_start=result.location.char_start,
                char_end=result.location.char_end,
            ),
            relevance=RelevanceOut(
                score=result.relevance.score,
                lexical=EvidenceOut.from_domain(result.relevance.lexical),
                semantic=EvidenceOut.from_domain(result.relevance.semantic),
            ),
        )


class FusionOut(BaseModel):
    algorithm: str
    k: int


class SearchResponseOut(BaseModel):
    query: str
    mode: RetrievalMode
    #: The retrievers that actually contributed to the ranking.
    retrievers: list[RetrievalMethod]
    #: Set when a requested retriever could not run (e.g. `semantic_unavailable`).
    degraded: str | None
    fusion: FusionOut
    candidates: dict[RetrievalMethod, int]
    results: list[SearchResultOut]
    #: Results skipped to reach this page.
    offset: int
    #: Whether a further page exists. There is deliberately no total: counting
    #: every matching chunk would cost a second full scan to answer a question
    #: that does not help anyone choose what to read.
    has_more: bool
    #: Whether a filter narrowed this search beyond the workspace, so a client
    #: can offer to widen an empty result rather than only reporting it.
    filtered: bool

    @classmethod
    def from_domain(cls, response: SearchResponse) -> SearchResponseOut:
        return cls(
            query=response.query,
            mode=response.mode,
            retrievers=list(response.retrievers),
            degraded=response.degraded,
            fusion=FusionOut(algorithm=response.fusion.algorithm, k=response.fusion.k),
            candidates=dict(response.candidates),
            results=[SearchResultOut.from_domain(result) for result in response.results],
            offset=response.offset,
            has_more=response.has_more,
            filtered=response.filtered,
        )
