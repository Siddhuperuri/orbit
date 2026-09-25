# Vector search

Recall and latency of ORBIT's dense search query (`SqlSearchRepository.semantic_candidates`, named `nearest_chunks` when this was measured)
against the production schema and HNSW index. Raw results:
[`data/vector-search-2026-09-17.json`](data/vector-search-2026-09-17.json).

**Reproduce:** `npm run bench:vector` (against a `*_test` or `*_bench` database;
it drops and restores `ix_chunks_embedding_hnsw` and removes its rows).

---

## Method

| | |
|---|---|
| Date | 2026-09-17 |
| Database | PostgreSQL 17.11, pgvector 0.8.6, `pgvector/pgvector:pg17` in Docker Engine in WSL2 (8 GB VM), `shared_buffers` 128 MB (container default) |
| Client | Windows 11, Python 3.11, asyncpg through SQLAlchemy — the production code path |
| Corpus | 100,000 chunks × 1,536 dimensions, 256 shared topic clusters (within-cluster cosine ≈ 0.5), 50 chunks per document |
| Tenants | 1 workspace with 50,000 chunks, 1 with 10,000, 36 with 1,000, 20 with 200 |
| Index | `hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)`, the migration's definition |
| Queries | 100 per workspace size: half near one of the workspace's own passages, half near a random topic |
| k | 10 |
| Ground truth | Exact cosine top-10 within the workspace, computed in numpy |
| Grid | `hnsw.ef_search` ∈ {40, 100, 200} × `hnsw.iterative_scan` ∈ {off, relaxed_order} |
| Server time | `EXPLAIN ANALYZE` execution time of the identical statement and settings |

**Why synthetic vectors.** No provider credentials are available to CI or to this
run, and the fake provider's hashed word counts are sparse and unlike real
embeddings. Clustered vectors shared across tenants reproduce the property that
makes filtered ANN hard — every tenant writes about the same topics, so a
query's graph neighbourhood is mostly other tenants' chunks. Absolute recall on
real embeddings will differ. **Re-run against real `text-embedding-3-small`
vectors before production sizing.**

## Results

Server execution time in milliseconds. "short" counts queries that returned
fewer than k rows.

| Workspace | Chunks | Iterative scan | ef_search | Plan chosen | recall@10 | worst query | short | p50 | p95 | p99 |
|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|
| large | 50,000 | off | 40 | HNSW | 0.942 | 0.0 | 0 | 0.80 | 1.29 | 1.52 |
| large | 50,000 | off | 100 | HNSW | 0.987 | 0.0 | 0 | 1.05 | 2.70 | 3.35 |
| large | 50,000 | off | 200 | **sequential scan** | 1.000 | 1.0 | 0 | 166.28 | 187.49 | 221.82 |
| large | 50,000 | relaxed_order | 40 | HNSW | **1.000** | 1.0 | 0 | 2.55 | 4.69 | 6.22 |
| large | 50,000 | relaxed_order | **100** | HNSW | **1.000** | 1.0 | 0 | 2.52 | 4.79 | 5.61 |
| large | 50,000 | relaxed_order | 200 | **sequential scan** | 1.000 | 1.0 | 0 | 188.32 | 206.92 | 217.65 |
| medium | 10,000 | off | 40 | HNSW | 1.000 | 1.0 | 0 | 2.51 | 5.01 | 5.75 |
| medium | 10,000 | off | 100 | HNSW | 1.000 | 1.0 | 0 | 2.38 | 4.28 | 4.74 |
| medium | 10,000 | off | 200 | **sequential scan** | 1.000 | 1.0 | 0 | 37.79 | 43.46 | 48.12 |
| medium | 10,000 | relaxed_order | 40 | HNSW | 1.000 | 1.0 | 0 | 2.63 | 4.52 | 5.22 |
| medium | 10,000 | relaxed_order | **100** | HNSW | 1.000 | 1.0 | 0 | 2.65 | 4.45 | 4.61 |
| medium | 10,000 | relaxed_order | 200 | **sequential scan** | 1.000 | 1.0 | 0 | 36.21 | 43.12 | 47.34 |
| small | 1,000 | any | any | exact (workspace B-tree + sort) | 1.000 | 1.0 | 0 | 7.4–7.6 | 9.0–9.7 | 9.6–10.8 |
| tiny | 200 | any | any | exact (workspace B-tree + sort) | 1.000 | 1.0 | 0 | 2.1–3.0 | 3.2–3.7 | 3.4–4.1 |

### Build and ingestion

| Measurement | Value |
|---|---|
| Bulk load of 100,000 rows (`COPY`, no index) | 32.6 s |
| HNSW build, serial, `maintenance_work_mem` 1 GB | 123.3 s |
| HNSW index size | 781 MB (≈ 8.2 KB per chunk) |
| `chunks` total size (heap + TOAST + all indexes) | 2.47 GB |
| Inserts with the index in place, 250-row statements | 109.5 rows/s (client round trips included) |

## Findings

1. **Iterative scan is required for correctness under the tenant filter.** With
   it off, the 50,000-chunk workspace reached only 0.942 recall@10 at
   `ef_search` 40 and 0.987 at 100, and in both cases at least one query
   returned none of its true neighbours. The HNSW scan hands back a fixed
   candidate list and the workspace predicate throws half of it away. With
   `relaxed_order`, recall is 1.000 at every setting, for about 1.5–2 ms more at
   p50. **Production uses `relaxed_order`.**

2. **Raising `ef_search` can make search 65× slower.** At 200, pgvector's cost
   estimate rose far enough that the planner chose a sequential scan with a
   sort over 50,000 wide rows: 166 ms instead of 2.5 ms, with no recall to gain.
   The switch happened between 100 and 200 at this corpus size; where it
   happens depends on table statistics. **Default `ORBIT_VECTOR_SEARCH_EF_SEARCH`
   is 100.** Do not raise it without re-running this benchmark on the real
   corpus.

3. **Small workspaces never touch HNSW, and that is correct.** At 1,000 chunks
   and below, the planner uses the workspace B-tree and sorts exactly: perfect
   recall in under 10 ms. The selective-filter failure ADR-0005 warned about
   (a graph scan running out of matching candidates) did not occur here,
   because the planner never sent a selective query to the graph.

4. **Memory, not CPU, is the scaling constraint.** The index is ≈ 8.2 KB per
   chunk — 781 MB for 100,000 — against a 128 MB `shared_buffers`. Latencies
   above relied on the OS page cache. Size the database host so the HNSW index
   fits in memory: roughly 8 GB per million chunks at 1,536 dimensions.
   `halfvec` indexing would roughly halve that; it is not measured yet.

5. **Ingestion pays about 9 ms per chunk for index maintenance** (upper bound;
   the figure includes round trips through the WSL relay). A 200-chunk document
   spends about 2 s in the index transaction; the 20,000-chunk ceiling about
   3 minutes, inside the 15-minute task limit.

6. **A parallel HNSW build fails in the default container.** Parallel builds
   share `maintenance_work_mem` through `/dev/shm`, which Docker limits to
   64 MB: "could not resize shared memory segment". Any migration that builds
   or rebuilds this index on a large table needs `shm_size` raised, or a serial
   build.

## Caveats

- **Client-side latency on this host is not meaningful.** Every search
  transaction measured about 50 ms at the client even when the server spent
  2 ms, and several configurations measured 100–450 ms with no matching
  server-side change. The measured floor for a trivial transaction was 2.4 ms,
  so the gap is specific to these statements; a ~40 ms stall is consistent with
  delayed ACKs on the Windows→WSL port relay when a 30 KB vector parameter is
  sent. That is a hypothesis, not a measurement. Client columns are in the raw
  JSON; the tables here report server time only.
- One run, one seed, one host. Differences under 1 ms are noise.
- Synthetic, clustered vectors (see Method).
- The corpus is 100,000 chunks. Findings 2 and 4 are the ones most likely to
  change with size.
