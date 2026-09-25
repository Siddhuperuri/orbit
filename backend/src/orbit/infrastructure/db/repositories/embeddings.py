"""The vector index: reuse, verification, coverage, and in-place re-embedding.

Vectors cross this boundary as pgvector's text form in both directions. The
binary codec would be smaller on the wire, but it cannot be combined with the
SQLAlchemy column type's own text bind processor on one connection, and the
volumes here -- a batch of reuse lookups, a re-index batch -- are bounded by
the callers, not by corpus size.
"""

from __future__ import annotations

import uuid
from array import array
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from pgvector import Vector as PgVector
from sqlalchemy import and_, bindparam, func, not_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import String

from orbit.domain.access import SystemContext
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.ports.embeddings import (
    EmbeddingUpdate,
    IndexCoverage,
    SpaceUsage,
    StaleChunk,
    StoredEmbedding,
)
from orbit.infrastructure.db.models import Chunk as ChunkRow
from orbit.infrastructure.db.models import Document as DocumentRow
from orbit.infrastructure.db.models import DocumentVersion as VersionRow

#: Hashes per reuse lookup. Bounds both the `IN` list and how many vectors one
#: result set carries back.
_REUSE_LOOKUP_BATCH = 500


def to_float32(value: Any) -> array[float]:  # noqa: ANN401 -- a driver value
    """pgvector's result processor returns a numpy float32 array; the domain
    holds plain `array('f')`, so numpy stays an infrastructure detail."""
    compact = array("f")
    compact.frombytes(value.astype("float32").tobytes())
    return compact


def _live_chunks() -> Any:  # noqa: ANN401 -- a SQLAlchemy join
    """Chunks retrieval could return: current versions of undeleted documents."""
    return ChunkRow.__table__.join(VersionRow, VersionRow.id == ChunkRow.document_version_id).join(
        DocumentRow, DocumentRow.id == ChunkRow.document_id
    )


def _live() -> Any:  # noqa: ANN401 -- a SQLAlchemy predicate
    return and_(VersionRow.is_current.is_(True), DocumentRow.deleted_at.is_(None))


def _in_space(space: EmbeddingSpace) -> Any:  # noqa: ANN401 -- a SQLAlchemy predicate
    return and_(
        ChunkRow.embedding_model == space.model,
        ChunkRow.embedding_dimensions == space.dimensions,
    )


class SqlEmbeddingIndexRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def column_dimensions(self, system: SystemContext) -> int | None:
        del system
        # For `vector(n)` the type modifier *is* n; an untyped column has -1.
        typmod = (
            await self._session.execute(
                text(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding' "
                    "AND NOT attisdropped"
                )
            )
        ).scalar_one_or_none()
        return typmod if typmod is not None and typmod > 0 else None

    async def find_reusable(
        self,
        system: SystemContext,
        *,
        workspace_id: uuid.UUID,
        input_hashes: Collection[str],
        space: EmbeddingSpace,
    ) -> Mapping[str, StoredEmbedding]:
        del system
        wanted = sorted(set(input_hashes))
        found: dict[str, StoredEmbedding] = {}
        for start in range(0, len(wanted), _REUSE_LOOKUP_BATCH):
            batch = wanted[start : start + _REUSE_LOOKUP_BATCH]
            rows = await self._session.execute(
                select(ChunkRow.embedding_input_sha256, ChunkRow.embedding, ChunkRow.embedded_at)
                .where(
                    ChunkRow.workspace_id == workspace_id,
                    ChunkRow.embedding_input_sha256.in_(batch),
                    _in_space(space),
                )
                .distinct(ChunkRow.embedding_input_sha256)
                .order_by(ChunkRow.embedding_input_sha256, ChunkRow.embedded_at.desc())
            )
            for input_hash, vector, embedded_at in rows:
                found[input_hash] = StoredEmbedding(
                    vector=to_float32(vector), embedded_at=embedded_at
                )
        return found

    async def count_indexed(
        self, system: SystemContext, version_id: uuid.UUID, *, space: EmbeddingSpace
    ) -> int:
        del system
        return (
            await self._session.execute(
                select(func.count())
                .select_from(ChunkRow)
                .where(ChunkRow.document_version_id == version_id, _in_space(space))
            )
        ).scalar_one()

    async def coverage(self, system: SystemContext, *, space: EmbeddingSpace) -> IndexCoverage:
        column_dimensions = await self.column_dimensions(system)
        spaces = await self._session.execute(
            select(
                ChunkRow.embedding_model,
                ChunkRow.embedding_dimensions,
                func.count(),
                func.count(ChunkRow.document_version_id.distinct()),
                func.min(ChunkRow.embedded_at),
                func.max(ChunkRow.embedded_at),
            )
            .select_from(_live_chunks())
            .where(_live())
            .group_by(ChunkRow.embedding_model, ChunkRow.embedding_dimensions)
            .order_by(ChunkRow.embedding_model, ChunkRow.embedding_dimensions)
        )
        chunkers = await self._session.execute(
            select(ChunkRow.chunker_version, func.count())
            .select_from(_live_chunks())
            .where(_live())
            .group_by(ChunkRow.chunker_version)
        )
        return IndexCoverage(
            active=space,
            column_dimensions=column_dimensions,
            spaces=tuple(
                SpaceUsage(
                    model=model,
                    dimensions=dimensions,
                    chunks=chunks,
                    versions=versions,
                    oldest_embedded_at=oldest,
                    newest_embedded_at=newest,
                )
                for model, dimensions, chunks, versions, oldest, newest in spaces
            ),
            chunker_versions=dict(chunkers.tuples().all()),
        )

    async def find_stale(
        self,
        system: SystemContext,
        *,
        space: EmbeddingSpace,
        after: uuid.UUID | None,
        limit: int,
    ) -> Sequence[StaleChunk]:
        del system
        query = (
            select(
                ChunkRow.id,
                ChunkRow.workspace_id,
                ChunkRow.content,
                ChunkRow.heading_path,
                ChunkRow.token_count,
                ChunkRow.embedding_input_sha256,
            )
            .select_from(_live_chunks())
            .where(_live(), not_(_in_space(space)))
            .order_by(ChunkRow.id)
            .limit(limit)
        )
        if after is not None:
            query = query.where(ChunkRow.id > after)
        rows = await self._session.execute(query)
        return [
            StaleChunk(
                chunk_id=chunk_id,
                workspace_id=workspace_id,
                content=content,
                heading_path=heading_path,
                token_count=token_count,
                embedding_input_sha256=input_hash,
            )
            for chunk_id, workspace_id, content, heading_path, token_count, input_hash in rows
        ]

    async def update_embeddings(
        self,
        system: SystemContext,
        updates: Sequence[EmbeddingUpdate],
        *,
        space: EmbeddingSpace,
    ) -> int:
        del system
        if not updates:
            return 0
        # One statement for the batch. The per-row condition is the safety
        # property: a chunk whose input changed since it was read (a reprocess
        # replaced it) or that another run already moved is left alone.
        statement = text(
            """
            UPDATE chunks AS c
            SET embedding = u.embedding::vector,
                embedding_model = :model,
                embedding_dimensions = :dimensions,
                embedded_at = u.embedded_at
            FROM unnest(:ids, :hashes, :vectors, :embedded_at)
                AS u(id, input_hash, embedding, embedded_at)
            WHERE c.id = u.id
              AND c.embedding_input_sha256 = u.input_hash
              AND NOT (c.embedding_model = :model AND c.embedding_dimensions = :dimensions)
            RETURNING c.id
            """
        ).bindparams(
            bindparam("ids", type_=ARRAY(UUID(as_uuid=True))),
            bindparam("hashes", type_=ARRAY(String())),
            bindparam("vectors", type_=ARRAY(String())),
            bindparam("embedded_at", type_=ARRAY(TIMESTAMP(timezone=True))),
        )
        result = await self._session.execute(
            statement,
            {
                "ids": [update.chunk_id for update in updates],
                "hashes": [update.embedding_input_sha256 for update in updates],
                "vectors": [PgVector(update.vector).to_text() for update in updates],
                "embedded_at": [update.embedded_at for update in updates],
                "model": space.model,
                "dimensions": space.dimensions,
            },
        )
        return len(result.scalars().all())
