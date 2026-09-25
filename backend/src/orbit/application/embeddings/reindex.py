"""Index coverage and in-place re-embedding after an embedding model change.

The procedure these serve is in docs/database/embeddings.md. In short: when
`ORBIT_EMBEDDING_MODEL` changes at the same width, every live chunk still
holds a vector from the old space. Dense retrieval filters on the active
space, so those chunks are *excluded* -- never compared against query vectors
they are not comparable with -- and remain findable lexically. This use case
moves them into the active space, batch by batch, without touching their text,
their ids, their citations, or their document's READY status.

It is **resumable and idempotent**: progress is the data itself (a chunk
already in the active space is no longer stale), so an interrupted run simply
continues, and running two at once wastes provider calls but writes nothing
wrong.

It is **safe against concurrent processing**: each update is conditional on
the chunk still holding the exact input it was embedded from. If a reprocess
or a new version replaced the chunk meanwhile, the update matches nothing and
the replacement -- written in the active space by the pipeline -- stands.

A change of **width** cannot be done in place: the column's dimension is
schema. That refuses here and is a migration (expand/contract) instead.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from orbit.application.embeddings.embed_chunks import verify_space_matches_schema
from orbit.core.clock import Clock
from orbit.core.logging import get_logger
from orbit.domain.access import SystemContext
from orbit.domain.embeddings import (
    build_embedding_input,
    embedding_input_sha256,
    plan_batches,
    to_indexable_vector,
    to_indexable_vectors,
)
from orbit.domain.ports.embeddings import (
    EmbeddingProvider,
    EmbeddingUpdate,
    IndexCoverage,
    StaleChunk,
    StoredEmbedding,
)
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory

logger = get_logger(__name__)

REINDEX_SYSTEM = SystemContext(reason="embedding-reindex")


class GetIndexCoverage:
    def __init__(self, uow_factory: UnitOfWorkFactory, provider: EmbeddingProvider) -> None:
        self._uow_factory = uow_factory
        self._provider = provider

    async def execute(self) -> IndexCoverage:
        async with self._uow_factory() as uow:
            return await uow.embeddings.coverage(REINDEX_SYSTEM, space=self._provider.space)


@dataclass(frozen=True, slots=True)
class ReindexReport:
    space: str
    #: Stale chunks examined.
    scanned: int
    #: Rows moved into the active space.
    updated: int
    #: Vectors taken from an identical input already in the active space.
    reused: int
    #: Distinct inputs sent to the provider.
    embedded: int
    requests: int
    #: Updates that matched nothing: the chunk changed or moved concurrently.
    superseded: int
    #: Chunks whose stored input hash disagrees with their own text. Never
    #: re-embedded blind; reported for investigation.
    inconsistent: int
    #: True when a scan found nothing further to do.
    complete: bool


class ReindexEmbeddings:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        provider: EmbeddingProvider,
        clock: Clock,
        *,
        batch_size: int,
    ) -> None:
        if batch_size < 1:
            msg = "batch_size must be positive."
            raise ValueError(msg)
        self._uow_factory = uow_factory
        self._provider = provider
        self._clock = clock
        self._batch_size = batch_size

    async def execute(self, *, max_chunks: int | None = None) -> ReindexReport:
        space = self._provider.space
        await verify_space_matches_schema(self._uow_factory, REINDEX_SYSTEM, space)

        totals = dict.fromkeys(
            ("scanned", "updated", "reused", "embedded", "requests", "superseded", "inconsistent"),
            0,
        )
        after: uuid.UUID | None = None
        complete = False
        while max_chunks is None or totals["scanned"] < max_chunks:
            limit = self._batch_size
            if max_chunks is not None:
                limit = min(limit, max_chunks - totals["scanned"])
            async with self._uow_factory() as uow:
                stale = await uow.embeddings.find_stale(
                    REINDEX_SYSTEM, space=space, after=after, limit=limit
                )
            if not stale:
                complete = True
                break
            after = stale[-1].chunk_id
            batch = await self._reindex_batch(stale)
            for key, value in batch.items():
                totals[key] += value
            logger.info("embedding.reindex_batch", space=space.key, **batch)

        report = ReindexReport(space=space.key, complete=complete, **totals)
        logger.info(
            "embedding.reindex_finished",
            space=report.space,
            complete=report.complete,
            scanned=report.scanned,
            updated=report.updated,
            reused=report.reused,
            embedded=report.embedded,
            requests=report.requests,
            superseded=report.superseded,
            inconsistent=report.inconsistent,
        )
        return report

    async def _reindex_batch(self, stale: Sequence[StaleChunk]) -> dict[str, int]:
        space = self._provider.space
        consistent: list[StaleChunk] = []
        inconsistent = 0
        for chunk in stale:
            if embedding_input_sha256(chunk.heading_path, chunk.content) != (
                chunk.embedding_input_sha256
            ):
                inconsistent += 1
                logger.error(
                    "embedding.reindex_inconsistent_chunk",
                    chunk_id=str(chunk.chunk_id),
                    workspace_id=str(chunk.workspace_id),
                )
                continue
            consistent.append(chunk)

        # Reuse stays inside each workspace, exactly as in the pipeline.
        by_workspace: dict[uuid.UUID, list[StaleChunk]] = defaultdict(list)
        for chunk in consistent:
            by_workspace[chunk.workspace_id].append(chunk)
        vectors: dict[tuple[uuid.UUID, str], StoredEmbedding] = {}
        async with self._uow_factory() as uow:
            for workspace_id, chunks in by_workspace.items():
                found = await uow.embeddings.find_reusable(
                    REINDEX_SYSTEM,
                    workspace_id=workspace_id,
                    input_hashes={chunk.embedding_input_sha256 for chunk in chunks},
                    space=space,
                )
                for input_hash, stored in found.items():
                    vectors[(workspace_id, input_hash)] = StoredEmbedding(
                        vector=to_indexable_vector(stored.vector, space),
                        embedded_at=stored.embedded_at,
                    )
        reused = sum(
            1
            for chunk in consistent
            if (chunk.workspace_id, chunk.embedding_input_sha256) in vectors
        )

        # What is left is sent once per distinct input. Computing a vector once
        # for two tenants' identical text is not the side channel that reuse
        # guards against: no tenant can observe this operator-run batch.
        pending: dict[str, StaleChunk] = {}
        for chunk in consistent:
            if (chunk.workspace_id, chunk.embedding_input_sha256) not in vectors:
                pending.setdefault(chunk.embedding_input_sha256, chunk)
        items = list(pending.values())
        batches = plan_batches(
            [chunk.token_count for chunk in items],
            max_items=max(1, self._provider.max_batch_size),
            max_tokens=max(1, self._provider.max_batch_tokens),
        )
        fresh: dict[str, StoredEmbedding] = {}
        for batch in batches:
            group = [items[index] for index in batch]
            raw = await self._provider.embed_documents(
                [build_embedding_input(chunk.heading_path, chunk.content) for chunk in group]
            )
            validated = to_indexable_vectors(raw, expected=len(group), space=space)
            embedded_at = self._clock.now()
            for chunk, vector in zip(group, validated, strict=True):
                fresh[chunk.embedding_input_sha256] = StoredEmbedding(
                    vector=vector, embedded_at=embedded_at
                )

        updates = [
            EmbeddingUpdate(
                chunk_id=chunk.chunk_id,
                embedding_input_sha256=chunk.embedding_input_sha256,
                vector=stored.vector,
                embedded_at=stored.embedded_at,
            )
            for chunk in consistent
            for stored in (
                vectors.get((chunk.workspace_id, chunk.embedding_input_sha256))
                or fresh[chunk.embedding_input_sha256],
            )
        ]
        async with self._uow_factory() as uow:
            updated = await uow.embeddings.update_embeddings(REINDEX_SYSTEM, updates, space=space)
            await uow.commit()

        return {
            "scanned": len(stale),
            "updated": updated,
            "reused": reused,
            "embedded": len(items),
            "requests": len(batches),
            "superseded": len(updates) - updated,
            "inconsistent": inconsistent,
        }
