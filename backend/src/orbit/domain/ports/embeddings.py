"""Ports for embeddings: the provider and the vector index.

Search over the index is `orbit.domain.ports.search`.

The rest of ORBIT reaches an embedding model only through `EmbeddingProvider`.
No use case knows which vendor, endpoint, or credential is behind it -- the
composition root decides that from configuration (ADR-0007, ADR-0020).
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from orbit.domain.access import SystemContext
from orbit.domain.embeddings import EmbeddingSpace

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class EmbeddingProvider(Protocol):
    """Text to vectors in one declared space.

    `embed_documents` and `embed_query` are separate because some models
    expect different instructions or prefixes for the two sides, and a single
    `embed` would silently build a worse retrieval space for them.

    Failure contract:

    * `AIProviderUnavailableError` (or a subclass) for anything worth retrying
      -- rate limits, timeouts, 5xx, a malformed response. It may carry a
      `retry_after_seconds` context value when the provider said how long to
      wait.
    * `ConfigurationError` for what retrying cannot fix -- rejected
      credentials, exhausted quota, a model that does not produce this space.
    """

    @property
    def space(self) -> EmbeddingSpace: ...

    @property
    def max_batch_size(self) -> int:
        """Most inputs per request."""
        ...

    @property
    def max_batch_tokens(self) -> int:
        """Most (approximate) tokens per request, summed over its inputs."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """One vector per input, in input order."""
        ...

    async def embed_query(self, text: str) -> Sequence[float]: ...


# ---------------------------------------------------------------------------
# Vector index
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredEmbedding:
    """A vector already in the index, found by the hash of its exact input."""

    vector: Sequence[float]
    embedded_at: datetime


@dataclass(frozen=True, slots=True)
class StaleChunk:
    """A chunk whose vector is not in the active space."""

    chunk_id: uuid.UUID
    workspace_id: uuid.UUID
    content: str
    heading_path: str | None
    token_count: int
    embedding_input_sha256: str


@dataclass(frozen=True, slots=True)
class EmbeddingUpdate:
    chunk_id: uuid.UUID
    #: The input hash the vector was computed from. The update applies only if
    #: the row still has it, so a chunk rebuilt in the meantime is never given
    #: a vector for text it no longer holds.
    embedding_input_sha256: str
    vector: Sequence[float]
    embedded_at: datetime


@dataclass(frozen=True, slots=True)
class SpaceUsage:
    model: str
    dimensions: int
    chunks: int
    versions: int
    oldest_embedded_at: datetime | None
    newest_embedded_at: datetime | None

    @property
    def space_key(self) -> str:
        return f"{self.model}@{self.dimensions}"


@dataclass(frozen=True, slots=True)
class IndexCoverage:
    """How much of the live corpus is searchable in the active space.

    "Live" means chunks of current versions of documents that are not deleted
    -- the only chunks retrieval can return.
    """

    active: EmbeddingSpace
    #: The pgvector column's declared width; `None` if the column is untyped.
    column_dimensions: int | None
    spaces: tuple[SpaceUsage, ...]
    chunker_versions: Mapping[str, int]

    @property
    def indexed_chunks(self) -> int:
        return sum(
            usage.chunks
            for usage in self.spaces
            if (usage.model, usage.dimensions) == (self.active.model, self.active.dimensions)
        )

    @property
    def stale_chunks(self) -> int:
        return sum(usage.chunks for usage in self.spaces) - self.indexed_chunks

    @property
    def schema_matches(self) -> bool:
        return self.column_dimensions == self.active.dimensions

    @property
    def is_complete(self) -> bool:
        return self.schema_matches and self.stale_chunks == 0


class EmbeddingIndexRepository(Protocol):
    """The stored vectors, as an index rather than as chunk rows.

    Every method here is system-scoped: reuse, verification, and re-indexing
    are performed by the worker and by operator tooling, never on behalf of a
    user. Reuse is nonetheless always *within one workspace* -- see
    `find_reusable`.
    """

    async def column_dimensions(self, system: SystemContext) -> int | None:
        """The width the database will accept, read from the catalog."""
        ...

    async def find_reusable(
        self,
        system: SystemContext,
        *,
        workspace_id: uuid.UUID,
        input_hashes: Collection[str],
        space: EmbeddingSpace,
    ) -> Mapping[str, StoredEmbedding]:
        """Vectors already computed, in this space, for these exact inputs.

        Scoped to one workspace deliberately. Reusing another tenant's vector
        would be correct arithmetic and a side channel: how quickly -- and how
        cheaply -- a document indexes would reveal whether some other tenant
        already holds the same text.
        """
        ...

    async def count_indexed(
        self, system: SystemContext, version_id: uuid.UUID, *, space: EmbeddingSpace
    ) -> int:
        """Chunks of the version whose vector is in `space`."""
        ...

    async def coverage(self, system: SystemContext, *, space: EmbeddingSpace) -> IndexCoverage: ...

    async def find_stale(
        self,
        system: SystemContext,
        *,
        space: EmbeddingSpace,
        after: uuid.UUID | None,
        limit: int,
    ) -> Sequence[StaleChunk]:
        """Live chunks not in `space`, in id order, starting after `after`."""
        ...

    async def update_embeddings(
        self,
        system: SystemContext,
        updates: Sequence[EmbeddingUpdate],
        *,
        space: EmbeddingSpace,
    ) -> int:
        """Replace vectors in place; returns how many rows changed.

        Conditional per row on the input hash still matching and the row not
        already being in `space`, so re-running is harmless and a concurrent
        reprocess always wins.
        """
        ...
