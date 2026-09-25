"""The embed stage: chunk drafts in, validated vectors in one space out.

Cost is controlled before anything is sent:

1. **Identical inputs are embedded once.** Two chunks with the same heading
   path and text produce the same vector, so only distinct inputs are sent.
2. **Vectors already in the index are reused.** A reprocess of an indexed
   document, the same passages in another file (an exported copy, a shared
   policy section, a standard disclaimer), or a re-index finds those vectors
   by input hash, in the same space and the same workspace, and pays only for
   inputs the index has never seen.

   Two things it cannot reuse, by construction: vectors from an attempt that
   failed before its index transaction committed (never stored -- the
   in-process retry layer bounds that cost instead), and the previous
   *version* of the same document, whose chunks are deleted at the moment the
   new version is uploaded (docs/database/embeddings.md, "Known limits").
3. **The rest is batched** by both input count and approximate token total.

Every vector -- fresh or reused -- is validated against the space before it
is paired with its chunk, and pairing is by input hash, never by position.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from orbit.core.clock import Clock
from orbit.core.logging import get_logger
from orbit.domain.access import SystemContext
from orbit.domain.embeddings import (
    EmbeddingSpace,
    plan_batches,
    to_indexable_vector,
    to_indexable_vectors,
)
from orbit.domain.errors import ConfigurationError
from orbit.domain.ports.embeddings import EmbeddingProvider, StoredEmbedding
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory
from orbit.domain.processing.content import ChunkDraft, EmbeddedChunk

logger = get_logger(__name__)

#: Called after each provider request completes. The pipeline renews its lease
#: here, so a long document keeps its claim -- and learns promptly, before
#: paying for the next batch, if it has lost it.
BatchCallback = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class EmbeddingReport:
    chunks: int
    distinct_inputs: int
    reused: int
    embedded: int
    requests: int


@dataclass(frozen=True, slots=True)
class EmbeddedDocument:
    chunks: list[EmbeddedChunk]
    report: EmbeddingReport


async def verify_space_matches_schema(
    uow_factory: UnitOfWorkFactory, system: SystemContext, space: EmbeddingSpace
) -> None:
    """Refuse to embed into a space the database cannot store.

    Checked before any provider call, so a misconfigured width costs one
    catalog query rather than a document's worth of embeddings that the
    index transaction would then reject.
    """
    async with uow_factory() as uow:
        column = await uow.embeddings.column_dimensions(system)
    if column != space.dimensions:
        msg = (
            f"The embedding space {space.key} does not fit the vector column "
            f"(vector({column})). Changing dimensions is a migration: "
            "see docs/database/embeddings.md."
        )
        raise ConfigurationError(msg, configured=space.dimensions, column=column)


class ChunkEmbedder:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        provider: EmbeddingProvider,
        clock: Clock,
        system: SystemContext,
    ) -> None:
        self._uow_factory = uow_factory
        self._provider = provider
        self._clock = clock
        self._system = system

    @property
    def space(self) -> EmbeddingSpace:
        return self._provider.space

    async def verify_schema(self) -> None:
        await verify_space_matches_schema(self._uow_factory, self._system, self.space)

    async def embed(
        self,
        drafts: Sequence[ChunkDraft],
        *,
        workspace_id: uuid.UUID,
        after_batch: BatchCallback | None = None,
    ) -> EmbeddedDocument:
        space = self.space
        distinct: dict[str, ChunkDraft] = {}
        for draft in drafts:
            distinct.setdefault(draft.embedding_input_sha256, draft)

        async with self._uow_factory() as uow:
            reusable = await uow.embeddings.find_reusable(
                self._system,
                workspace_id=workspace_id,
                input_hashes=distinct.keys(),
                space=space,
            )
        vectors: dict[str, StoredEmbedding] = {
            input_hash: StoredEmbedding(
                vector=to_indexable_vector(stored.vector, space),
                embedded_at=stored.embedded_at,
            )
            for input_hash, stored in reusable.items()
            if input_hash in distinct
        }

        pending = [(h, d) for h, d in distinct.items() if h not in vectors]
        batches = plan_batches(
            [draft.token_count for _, draft in pending],
            max_items=max(1, self._provider.max_batch_size),
            max_tokens=max(1, self._provider.max_batch_tokens),
        )
        for batch in batches:
            items = [pending[index] for index in batch]
            raw = await self._provider.embed_documents(
                [draft.embedding_input for _, draft in items]
            )
            validated = to_indexable_vectors(raw, expected=len(items), space=space)
            embedded_at = self._clock.now()
            for (input_hash, _), vector in zip(items, validated, strict=True):
                vectors[input_hash] = StoredEmbedding(vector=vector, embedded_at=embedded_at)
            if after_batch is not None:
                await after_batch()

        chunks = [
            EmbeddedChunk(
                draft=draft,
                embedding=vectors[draft.embedding_input_sha256].vector,
                embedded_at=vectors[draft.embedding_input_sha256].embedded_at,
            )
            for draft in drafts
        ]
        return EmbeddedDocument(
            chunks=chunks,
            report=EmbeddingReport(
                chunks=len(drafts),
                distinct_inputs=len(distinct),
                reused=len(distinct) - len(pending),
                embedded=len(pending),
                requests=len(batches),
            ),
        )
