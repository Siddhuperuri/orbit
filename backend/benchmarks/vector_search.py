"""Dense-retrieval benchmark: recall and latency of ORBIT's real search query.

    uv run python -m benchmarks.vector_search --chunks 100000

What it measures, against the production SQL (`SqlSearchRepository`), on a
synthetic corpus loaded into the `chunks` table with the production schema and
HNSW index:

* **HNSW build** time and size, and incremental insert throughput with the
  index in place (what ingestion pays per chunk).
* **recall@k** against exact brute-force neighbours computed in numpy, and
  **latency** (p50/p95/p99, including the round trip), for every combination
  of `hnsw.ef_search` and iterative scan mode, for workspaces of four sizes --
  from one holding half the corpus down to one holding 0.2% of it. The small
  ones are the selective-filter risk ADR-0005 names.
* The **plan** PostgreSQL chooses for each workspace size.

Why synthetic vectors: CI and this benchmark have no provider credentials, and
the fake provider's hashed word counts are sparse and nothing like real
embeddings. Vectors are drawn around shared cluster centroids -- every tenant
writes about the same topics, which is exactly what makes a tenant filter
hard for a graph index -- with within-cluster cosine similarity around 0.5.
Absolute recall on real embeddings will differ; the *relative* effect of
ef_search, iterative scan, and filter selectivity is what this establishes.

It writes to the database it is pointed at, drops and rebuilds
`ix_chunks_embedding_hnsw`, and removes its own rows afterwards. It refuses a
database whose name does not end in `_test` or `_bench` unless forced. Never
run it while the integration suite is using the same database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import numpy as np
from pgvector import Vector as PgVector
from pgvector.asyncpg import register_vector
from sqlalchemy import event
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from orbit.core.ids import new_uuid7
from orbit.domain.access import AccessContext, Role
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.retrieval import SearchFilters
from orbit.infrastructure.db.repositories.search import SqlSearchRepository

#: asyncpg ships no type information; its connection is typed as `Any` here
#: rather than scattering ignores (pyproject's mypy override covers the import).
Connection = Any

DIMENSIONS = 1536
SPACE = EmbeddingSpace(model="bench-synthetic-v1", dimensions=DIMENSIONS)
CHUNKS_PER_DOCUMENT = 50
CLUSTERS = 256
INDEX_NAME = "ix_chunks_embedding_hnsw"
BENCH_PREFIX = "bench-vector-"
ALL = SearchFilters()

#: (tier, share of the corpus per workspace, number of workspaces).
TIERS: tuple[tuple[str, float, int], ...] = (
    ("large", 0.50, 1),
    ("medium", 0.10, 1),
    ("small", 0.01, 36),
    ("tiny", 0.002, 20),
)


def _write(line: str = "") -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _normalize(vectors: np.ndarray) -> np.ndarray:
    normalized: np.ndarray = vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)
    return normalized.astype(np.float32)


@dataclass
class Workspace:
    id: uuid.UUID
    tier: str
    rows: np.ndarray  # indices into the corpus


@dataclass
class Corpus:
    vectors: np.ndarray
    chunk_ids: list[uuid.UUID]
    centroids: np.ndarray
    workspaces: list[Workspace]


def build_corpus(rng: np.random.Generator, size: int) -> Corpus:
    centroids = _normalize(rng.standard_normal((CLUSTERS, DIMENSIONS)))
    vectors = np.empty((size, DIMENSIONS), dtype=np.float32)
    # Noise with the same expected norm as a centroid: two chunks of one
    # cluster sit near cosine 0.5, two of different clusters near 0.
    scale = 1.0 / np.sqrt(DIMENSIONS)
    for start in range(0, size, 10_000):
        stop = min(size, start + 10_000)
        assignment = rng.integers(0, CLUSTERS, stop - start)
        noise = rng.standard_normal((stop - start, DIMENSIONS)).astype(np.float32) * scale
        vectors[start:stop] = _normalize(centroids[assignment] + noise)

    order = rng.permutation(size)
    workspaces: list[Workspace] = []
    cursor = 0
    for tier, share, count in TIERS:
        per_workspace = max(CHUNKS_PER_DOCUMENT, int(size * share))
        for _ in range(count):
            rows = order[cursor : cursor + per_workspace]
            if len(rows) == 0:
                break
            workspaces.append(Workspace(id=new_uuid7(), tier=tier, rows=rows))
            cursor += len(rows)
    if cursor < size:  # rounding remainder joins the large workspace
        workspaces[0].rows = np.concatenate([workspaces[0].rows, order[cursor:]])
    return Corpus(
        vectors=vectors,
        chunk_ids=[new_uuid7() for _ in range(size)],
        centroids=centroids,
        workspaces=workspaces,
    )


def build_queries(
    rng: np.random.Generator, corpus: Corpus, workspace: Workspace, count: int
) -> np.ndarray:
    """Half near one of the workspace's own passages (an answer exists), half
    near a random topic (the nearest answers may be weak)."""
    own = corpus.vectors[rng.choice(workspace.rows, count // 2)]
    topics = corpus.centroids[rng.integers(0, CLUSTERS, count - count // 2)]
    anchors = np.concatenate([own, topics])
    noise = rng.standard_normal(anchors.shape).astype(np.float32) * (0.5 / np.sqrt(DIMENSIONS))
    return _normalize(anchors + noise)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


async def load(conn: Connection, corpus: Corpus) -> dict[str, float]:
    now = datetime.now(UTC)
    user_id = new_uuid7()
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, full_name) VALUES ($1, $2, 'x', 'Bench')",
        user_id,
        f"{BENCH_PREFIX}{user_id}@example.test",
    )
    await conn.executemany(
        "INSERT INTO workspaces (id, name, slug, created_by_user_id) VALUES ($1, $2, $3, $4)",
        [
            (w.id, f"Bench {w.tier}", f"{BENCH_PREFIX}{w.id.hex[:20]}", user_id)
            for w in corpus.workspaces
        ],
    )

    documents: list[tuple[Any, ...]] = []
    versions: list[tuple[Any, ...]] = []
    chunk_version: dict[int, tuple[uuid.UUID, uuid.UUID, uuid.UUID, int]] = {}
    for workspace in corpus.workspaces:
        for offset in range(0, len(workspace.rows), CHUNKS_PER_DOCUMENT):
            rows = workspace.rows[offset : offset + CHUNKS_PER_DOCUMENT]
            document_id, version_id = new_uuid7(), new_uuid7()
            digest = uuid.uuid4().hex * 2
            documents.append((document_id, workspace.id, f"Bench document {offset}"))
            versions.append(
                (
                    version_id, document_id, workspace.id, 1, True,
                    f"bench/{digest}", digest, 1024, "text/plain", "bench.txt",
                    "ready", len(rows), now,
                )
            )  # fmt: skip
            for ordinal, row in enumerate(rows):
                chunk_version[int(row)] = (workspace.id, document_id, version_id, ordinal)

    await conn.copy_records_to_table(
        "documents", records=documents, columns=["id", "workspace_id", "title"]
    )
    await conn.copy_records_to_table(
        "document_versions",
        records=versions,
        columns=[
            "id", "document_id", "workspace_id", "version_number", "is_current",
            "storage_key", "content_sha256", "byte_size", "content_type",
            "original_filename", "status", "chunk_count", "processed_at",
        ],
    )  # fmt: skip

    columns = [
        "id", "workspace_id", "document_id", "document_version_id", "ordinal", "content",
        "token_count", "char_start", "char_end", "content_sha256", "embedding",
        "embedding_model", "embedding_dimensions", "embedding_input_sha256", "embedded_at",
        "chunker_version",
    ]  # fmt: skip
    started = time.perf_counter()
    batch: list[tuple[Any, ...]] = []
    for row, vector in enumerate(corpus.vectors):
        workspace_id, document_id, version_id, ordinal = chunk_version[row]
        digest = f"{row:064x}"
        batch.append(
            (
                corpus.chunk_ids[row], workspace_id, document_id, version_id, ordinal,
                f"bench passage {row}", 3, 0, 16, digest, vector, SPACE.model,
                SPACE.dimensions, digest, now, "bench",
            )
        )  # fmt: skip
        if len(batch) == 5000:  # noqa: PLR2004
            await conn.copy_records_to_table("chunks", records=batch, columns=columns)
            batch = []
    if batch:
        await conn.copy_records_to_table("chunks", records=batch, columns=columns)
    return {"bulk_load_seconds": round(time.perf_counter() - started, 1)}


async def rebuild_index(conn: Connection, definition: str, workers: int) -> dict[str, Any]:
    """Serial by default. A parallel HNSW build shares `maintenance_work_mem`
    through dynamic shared memory, and Docker gives a container a 64 MB
    `/dev/shm`: the build fails with "could not resize shared memory segment"
    unless the container's `shm_size` exceeds it."""
    await conn.execute("SET maintenance_work_mem = '1GB'")
    await conn.execute(f"SET max_parallel_maintenance_workers = {int(workers)}")
    started = time.perf_counter()
    await conn.execute(definition)
    seconds = time.perf_counter() - started
    await conn.execute("ANALYZE chunks")
    size = await conn.fetchval(f"SELECT pg_relation_size('{INDEX_NAME}'::regclass)")
    table = await conn.fetchval("SELECT pg_total_relation_size('chunks'::regclass)")
    return {
        "index_build_seconds": round(seconds, 1),
        "index_bytes": int(size),
        "table_total_bytes": int(table),
        "maintenance_work_mem": "1GB",
        "parallel_maintenance_workers": workers,
    }


async def measure_incremental_inserts(
    conn: Connection, corpus: Corpus, rng: np.random.Generator, rows: int
) -> dict[str, float]:
    """Insert rows with the index in place, in the pipeline's statement size,
    inside a transaction that is rolled back."""
    workspace = corpus.workspaces[0]
    document_id, version_id = await conn.fetchrow(
        "SELECT document_id, id FROM document_versions WHERE workspace_id = $1 LIMIT 1",
        workspace.id,
    ) or (None, None)
    vectors = _normalize(rng.standard_normal((rows, DIMENSIONS)))
    now = datetime.now(UTC)
    transaction = conn.transaction()
    await transaction.start()
    try:
        started = time.perf_counter()
        for start in range(0, rows, 250):
            await conn.executemany(
                "INSERT INTO chunks (id, workspace_id, document_id, document_version_id,"
                " ordinal, content, token_count, char_start, char_end, content_sha256,"
                " embedding, embedding_model, embedding_dimensions, embedding_input_sha256,"
                " embedded_at, chunker_version) VALUES"
                " ($1,$2,$3,$4,$5,'x',1,0,1,$6,$7,$8,$9,$6,$10,'bench')",
                [
                    (
                        new_uuid7(), workspace.id, document_id, version_id, 100_000 + i,
                        f"{i:064x}", vectors[i], SPACE.model, DIMENSIONS, now,
                    )
                    for i in range(start, min(rows, start + 250))
                ],
            )  # fmt: skip
        seconds = time.perf_counter() - started
    finally:
        await transaction.rollback()
    return {"incremental_insert_rows_per_second": round(rows / seconds, 1)}


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


@dataclass
class Result:
    tier: str
    workspace_chunks: int
    plan: str
    iterative_scan: str
    ef_search: int
    k: int
    queries: int
    recall_mean: float
    recall_min: float
    short_results: int
    #: Wall clock at the client: the whole search transaction, round trips
    #: included. On this host that includes a Windows-to-WSL network hop.
    client_p50_ms: float
    client_p95_ms: float
    client_p99_ms: float
    #: `EXPLAIN ANALYZE` execution time for the same statement and settings:
    #: what the database itself spends, independent of the client's network.
    server_p50_ms: float
    server_p95_ms: float
    server_p99_ms: float


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(percentile / 100 * (len(ordered) - 1)))
    return round(ordered[index], 2)


def _plan_kind(plan_text: str) -> str:
    if INDEX_NAME in plan_text:
        return "hnsw"
    if "ix_chunks_workspace_id" in plan_text:
        return "exact (workspace btree + sort)"
    return "exact (sequential scan + sort)"


class _StatementCapture:
    """Records the SQL the repository sends, so the identical statement can be
    run under `EXPLAIN ANALYZE`."""

    def __init__(self) -> None:
        self.statement: str | None = None
        self.params: Any = None

    def __call__(
        self, _c: object, _cur: object, statement: str, params: object, *_: object
    ) -> None:
        if "<=>" in statement and self.statement is None:
            self.statement, self.params = statement, params


def _with_vector(params: Any, vector: Sequence[float]) -> Any:  # noqa: ANN401 -- driver params
    rendered = PgVector(vector).to_text()
    replaced = [
        rendered if isinstance(value, str) and value.startswith("[") else value for value in params
    ]
    return tuple(replaced) if isinstance(params, tuple) else replaced


async def run_configuration(  # noqa: PLR0913 -- one benchmark cell
    engine: AsyncEngine,
    corpus: Corpus,
    workspace: Workspace,
    queries: np.ndarray,
    *,
    k: int,
    ef_search: int,
    mode: str,
) -> Result:
    ctx = AccessContext(user_id=uuid.uuid4(), workspace_id=workspace.id, role=Role.VIEWER)
    subset = corpus.vectors[workspace.rows]
    truth = np.argsort(-(subset @ queries.T), axis=0)[:k].T
    recalls: list[float] = []
    client: list[float] = []
    server: list[float] = []
    plans: set[str] = set()
    short = 0
    capture = _StatementCapture()
    async with AsyncSession(engine) as session:
        repository = SqlSearchRepository(session, iterative_scan=mode)
        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        try:
            for warmup in queries[:5]:
                async with session.begin():
                    await repository.semantic_candidates(
                        ctx, warmup.tolist(), space=SPACE, filters=ALL, limit=k, ef_search=ef_search
                    )
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture)
        assert capture.statement is not None  # noqa: S101

        for query_array, expected_rows in zip(queries, truth, strict=True):
            query = query_array.tolist()
            started = time.perf_counter()
            async with session.begin():
                hits = await repository.semantic_candidates(
                    ctx, query, space=SPACE, filters=ALL, limit=k, ef_search=ef_search
                )
            client.append((time.perf_counter() - started) * 1000)
            expected = {corpus.chunk_ids[int(workspace.rows[row])] for row in expected_rows}
            recalls.append(len(expected & {hit.chunk_id for hit in hits}) / k)
            short += len(hits) < k

            async with session.begin():
                raw = await session.connection()
                await raw.exec_driver_sql(
                    "SELECT set_config('hnsw.ef_search', $1, true),"
                    " set_config('hnsw.iterative_scan', $2, true)",
                    (str(ef_search), mode),
                )
                explained = await raw.exec_driver_sql(
                    f"EXPLAIN (ANALYZE, FORMAT JSON) {capture.statement}",
                    _with_vector(capture.params, query),
                )
                document = explained.scalar_one()
                plan = document[0] if isinstance(document, list) else json.loads(document)[0]
                server.append(float(plan["Execution Time"]))
                plans.add(_plan_kind(json.dumps(plan)))
    return Result(
        tier=workspace.tier,
        workspace_chunks=len(workspace.rows),
        plan=" / ".join(sorted(plans)),
        iterative_scan=mode,
        ef_search=ef_search,
        k=k,
        queries=len(queries),
        recall_mean=round(statistics.fmean(recalls), 4),
        recall_min=round(min(recalls), 2),
        short_results=short,
        client_p50_ms=_percentile(client, 50),
        client_p95_ms=_percentile(client, 95),
        client_p99_ms=_percentile(client, 99),
        server_p50_ms=_percentile(server, 50),
        server_p95_ms=_percentile(server, 95),
        server_p99_ms=_percentile(server, 99),
    )


async def round_trip_floor(engine: AsyncEngine, samples: int = 50) -> float:
    """The same transaction shape with a trivial query: what every search pays
    before the database does any vector work."""
    timings: list[float] = []
    async with AsyncSession(engine) as session:
        for _ in range(samples):
            started = time.perf_counter()
            async with session.begin():
                raw = await session.connection()
                await raw.exec_driver_sql("SELECT set_config('hnsw.ef_search', '40', true)")
                await raw.exec_driver_sql("SELECT 1")
            timings.append((time.perf_counter() - started) * 1000)
    return _percentile(timings, 50)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class Report:
    started_at: str
    environment: dict[str, Any]
    corpus: dict[str, Any]
    build: dict[str, Any] = field(default_factory=dict)
    results: list[Result] = field(default_factory=list)


async def cleanup(conn: Connection) -> None:
    await conn.execute("DELETE FROM workspaces WHERE slug LIKE $1", f"{BENCH_PREFIX}%")
    await conn.execute("DELETE FROM users WHERE email LIKE $1", f"{BENCH_PREFIX}%")


async def main(args: argparse.Namespace) -> Report:
    url = make_url(args.database_url)
    database = url.database or ""
    if not database.endswith(("_test", "_bench")) and not args.force:
        msg = f"Refusing to benchmark against {database!r}; use a *_test or *_bench database."
        raise SystemExit(msg)
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)

    rng = np.random.default_rng(args.seed)
    _write(f"Generating {args.chunks:,} synthetic {DIMENSIONS}-d vectors ...")
    corpus = build_corpus(rng, args.chunks)

    conn = await asyncpg.connect(dsn)
    await register_vector(conn)
    engine = create_async_engine(args.database_url, pool_size=1, max_overflow=0)
    report = Report(
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        environment={
            "postgres": await conn.fetchval("SHOW server_version"),
            "pgvector": await conn.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ),
            "shared_buffers": await conn.fetchval("SHOW shared_buffers"),
            "client": f"{platform.system()} {platform.machine()}",
            "max_worker_processes": await conn.fetchval("SHOW max_worker_processes"),
        },
        corpus={
            "chunks": args.chunks,
            "dimensions": DIMENSIONS,
            "clusters": CLUSTERS,
            "workspaces": {tier: count for tier, _, count in TIERS},
            "seed": args.seed,
        },
    )
    definition = await conn.fetchval(f"SELECT pg_get_indexdef('{INDEX_NAME}'::regclass)")
    try:
        await cleanup(conn)
        await conn.execute(f"DROP INDEX {INDEX_NAME}")
        _write("Loading (index dropped for the bulk load) ...")
        report.build.update(await load(conn, corpus))
        _write(f"Building {INDEX_NAME} ...")
        report.build.update(await rebuild_index(conn, definition, args.parallel_workers))
        report.build.update(
            await measure_incremental_inserts(conn, corpus, rng, args.incremental_rows)
        )
        report.environment["round_trip_floor_p50_ms"] = await round_trip_floor(engine)
        _write(json.dumps(report.build))
        _write(json.dumps(report.environment))

        chosen = {tier: next(w for w in corpus.workspaces if w.tier == tier) for tier, *_ in TIERS}
        for workspace in chosen.values():
            queries = build_queries(rng, corpus, workspace, args.queries)
            for mode in ("off", "relaxed_order"):
                for ef_search in args.ef_search:
                    result = await run_configuration(
                        engine,
                        corpus,
                        workspace,
                        queries,
                        k=args.k,
                        ef_search=ef_search,
                        mode=mode,
                    )
                    report.results.append(result)
                    _write(json.dumps(asdict(result)))
    finally:
        # The index must come back even if the run failed half way.
        exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", INDEX_NAME)
        if not args.keep:
            await cleanup(conn)
        if not exists:
            # Session defaults: whatever made the timed build fail must not
            # also stop the restore.
            await conn.execute("RESET max_parallel_maintenance_workers")
            await conn.execute("RESET maintenance_work_mem")
            await conn.execute(definition)
        await conn.close()
        await engine.dispose()
    return report


def _markdown(report: Report) -> str:
    lines = [
        "| Workspace | Chunks | Plan | Iterative scan | ef_search | recall@k | min recall"
        " | short | server p50 | server p95 | client p50 | client p95 |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        f"| {r.tier} | {r.workspace_chunks:,} | {r.plan} | {r.iterative_scan} | {r.ef_search}"
        f" | {r.recall_mean:.3f} | {r.recall_min:.2f} | {r.short_results}"
        f" | {r.server_p50_ms} | {r.server_p95_ms} | {r.client_p50_ms} | {r.client_p95_ms} |"
        for r in report.results
    )
    return "\n".join(lines)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ORBIT_BENCH_DATABASE_URL")
        or os.environ.get("ORBIT_TEST_DATABASE_URL"),
    )
    parser.add_argument("--chunks", type=int, default=100_000)
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--ef-search", type=int, nargs="+", default=[40, 100, 200])
    parser.add_argument("--parallel-workers", type=int, default=0)
    parser.add_argument("--incremental-rows", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="leave the synthetic rows in place")
    parser.add_argument("--force", action="store_true", help="allow any database name")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error(
            "--database-url or ORBIT_BENCH_DATABASE_URL / ORBIT_TEST_DATABASE_URL is required"
        )
    return args


if __name__ == "__main__":
    arguments = parse_args(sys.argv[1:])
    outcome = asyncio.run(main(arguments))
    _write()
    _write(_markdown(outcome))
    if arguments.output:
        arguments.output.write_text(json.dumps(asdict(outcome), indent=2) + "\n", encoding="utf-8")
