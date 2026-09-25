# Embeddings and the vector index

How ORBIT stores vectors, what guarantees hold about them, what happens when the
embedding model changes, and how to re-index. The decisions behind this are in
[ADR-0020](../decisions/0020-vector-indexing.md); provider abstraction in
[ADR-0007](../decisions/0007-ai-provider-ports.md); measurements in
[`docs/benchmarks/vector-search.md`](../benchmarks/vector-search.md).

---

## 1. What is stored

Vectors live on the `chunks` row they describe. There is no separate embeddings
table (see [schema.md](schema.md)).

| Column | Meaning | Enforced by |
|---|---|---|
| `embedding vector(1536)` | The vector | `NOT NULL`; the column type rejects any other width |
| `embedding_model` | Which model produced it | `NOT NULL`, not blank |
| `embedding_dimensions` | The width of the space | `NOT NULL`; `CHECK (vector_dims(embedding) = embedding_dimensions)` |
| `embedding_input_sha256` | SHA-256 of exactly what was embedded: heading path + `\n\n` + text | `NOT NULL`, 64 chars; one definition in `orbit.domain.embeddings.build_embedding_input` |
| `embedded_at` | When the provider produced the vector (kept when a vector is reused) | `NOT NULL` |
| `document_version_id`, `document_id`, `workspace_id` | Which version of which document, in which tenant | composite foreign keys (a chunk cannot point at another tenant's version) |
| `ordinal`, `char_start`, `char_end`, `page_from`, `page_to` | Which chunk | unique `(document_version_id, ordinal)` |
| `chunker_version` | Which chunking parameters produced the text | `NOT NULL` |

`(embedding_model, embedding_dimensions)` is the **embedding space**. Two vectors
are comparable only if their spaces are equal. The same model at a different
width is a different space.

**Invariant: a chunk row is an indexed chunk.** No row exists without a vector
and a full description of its space. Together with
`ck_document_versions_ready_versions_have_chunks`, a `READY` version always has
at least one chunk, and every one of them is embedded.

## 2. How a vector gets there

```
chunk drafts
  │
  ├─ verify the column can hold the configured width   (catalog query; refuse → defect)
  ├─ collapse identical inputs                          (by embedding_input_sha256)
  ├─ reuse vectors already stored for those inputs      (same workspace, same space)
  ├─ batch the rest                                     (ORBIT_EMBEDDING_BATCH_SIZE and
  │                                                      ORBIT_EMBEDDING_MAX_BATCH_TOKENS)
  ├─ per request: provider call, in-process retry       (ORBIT_EMBEDDING_MAX_RETRIES)
  ├─ validate every vector                              (count, width, finite, non-zero)
  ├─ renew the job lease after each request             (stop at once if it was lost)
  │
  └─ index transaction ──┬─ fenced job completion (still my lease?)
                         ├─ lock version; still current? document not deleted?
                         ├─ delete this version's chunks, insert the new set
                         ├─ verify: rows in the active space == chunks written
                         └─ version PROCESSING → READY   (last write, same transaction)
```

`READY` is written only in the transaction that wrote and verified the complete
index. Nothing before it — a partial set of batches, a failed attempt, a lost
lease — leaves chunks behind.

## 3. Failure behaviour

| Failure | First response | If it persists | Version | Chunks |
|---|---|---|---|---|
| 429, 408, 409, 5xx, timeout, connection error | Retry the *request* in-process, jittered backoff, honouring `Retry-After` up to `ORBIT_EMBEDDING_RETRY_MAX_WAIT_SECONDS` | Attempt recorded as transient; job retried with durable backoff (ADR-0019) | `PENDING` | none written |
| `Retry-After` longer than the in-process cap | Not waited on; worker slot released | Durable job backoff | `PENDING` | none |
| Wrong vector count, NaN, ±inf, zero vector, unparseable body | Treated as an invalid response and retried like an outage | As above; after the last attempt `FAILED` with "a service was unavailable" | `PENDING` → `FAILED` | none |
| Vector of the wrong width | No retry: a configuration defect | `FAILED`, defect, operator detail logged | `FAILED` | none |
| 401/403, 404 (unknown model), 429 `insufficient_quota` | No retry | `FAILED`, defect | `FAILED` | none |
| Configured width ≠ column width | Refused before any provider call | `FAILED`, defect; API readiness probe reports `embedding_schema` down | `FAILED` | none |
| Lease lost mid-embedding | Stops before paying for the next batch; writes nothing | Successor owns the outcome | unchanged | none |
| Crash inside the index transaction | Transaction rolls back | Recovery retries the job | `PROCESSING` → retried | none |

A document that failed because of configuration is fixed by fixing the
configuration and reprocessing it (`POST .../documents/{document_id}/reprocess`); the file
was never at fault, and the user-facing message says so.

## 4. Duplicate processing and idempotency

| Situation | Outcome |
|---|---|
| A message delivered twice | The second claim sees a finished job and does nothing. No provider call. |
| Two workers race for one job | One claims (`FOR UPDATE SKIP LOCKED`); the other skips. |
| A retry after an attempt that embedded some batches and then failed | The earlier batches were never stored, so they are embedded again. The in-process retry layer exists so that a brief outage does not reach this point. |
| Reprocessing an indexed document | Every unchanged input is found in the index and reused: **zero provider calls** (asserted by `test_reprocessing_an_indexed_document_calls_the_provider_zero_times`). |
| The same passages in another file in the same workspace | Reused. |
| Identical inputs within one document | Embedded once. |
| The same text in **another workspace** | **Not reused, by design.** Reuse across tenants would be a side channel: how fast and how cheaply a document indexes would reveal whether another tenant already holds the same text. |
| A vector stored under **another model or width** | Never reused; the lookup is by space. |

## 5. Why a vector cannot attach to the wrong document or version

1. **Pairing is by identity, never by position.** The OpenAI adapter pairs each
   returned vector with its input by the response's `index` field and rejects
   duplicate or missing indexes; the embed stage checks the count; each vector
   is then attached to its chunk by `embedding_input_sha256`.
2. **The vector is stored on the chunk row itself.** There is no second table
   whose foreign key could be wrong; the chunk's version and tenant are enforced
   by composite foreign keys.
3. **Only the current version is indexed.** The index transaction locks the
   version row and refuses (`DOCUMENT_VERSION_SUPERSEDED`) if a newer upload
   demoted it or the document was deleted while the job ran.
4. **Every write is fenced on the lease.** A worker presumed dead cannot index
   over the attempt that replaced it.
5. **Re-embedding is conditional on the input.** The re-index updates a row only
   if it still holds the input hash the vector was computed from.
6. **Recorded width is the real width.** A database `CHECK` ties
   `embedding_dimensions` to `vector_dims(embedding)`.

## 6. When the embedding model changes

### Why it matters

Vectors from different models are not comparable, even at the same width. A
query embedded by model B ranked against documents embedded by model A returns
results that look plausible and are wrong, and nothing downstream can tell.

ORBIT therefore never compares across spaces. Dense search filters on the
**active space** — `ORBIT_EMBEDDING_MODEL` at `ORBIT_EMBEDDING_DIMENSIONS` — so a
chunk from any other space is *excluded* from dense results, never mis-ranked.
Every chunk records its space, so the size of the problem is always a query:

```bash
npm run worker:index-status
```

prints the active space, the column width, chunks and versions per space, their
oldest and newest `embedded_at`, chunker versions, and exits non-zero while any
live chunk is outside the active space.

### Scenario 1 — same width, different model

Examples: a provider's newer model, or switching `ORBIT_AI_PROVIDER` from `fake`
to `openai` at 1536 (the fake has its own model id precisely so this shows up as
stale rather than being mistaken for real vectors).

**Procedure**

1. Deploy the new `ORBIT_EMBEDDING_MODEL` to API and workers. From this moment
   new documents are embedded in the new space, and dense search queries the new
   space. Existing documents stay `READY` but drop out of *dense* results until
   step 2 reaches them.
2. Run the re-index until it reports `complete`:

   ```bash
   npm run worker:reindex
   ```

   It walks live chunks not in the active space, in id order, in batches of
   `ORBIT_EMBEDDING_REINDEX_BATCH_SIZE`, reuses any vector already computed for an
   identical input, embeds the rest through the same provider and retry layer,
   and updates each row in place — same chunk id, same text, same citations,
   document still `READY`. `--max-chunks N` bounds one run.
3. Confirm `npm run worker:index-status` exits 0.

**Properties**

- *Resumable and idempotent.* Progress is the data: a moved chunk is no longer
  stale. Stop it at any time and run it again.
- *Safe under concurrent processing.* A chunk rebuilt by a reprocess or a new
  version while the re-index held its old text is left to the rebuild (reported
  as `superseded`).
- *Integrity-checked.* A chunk whose stored input hash does not match its text is
  never embedded blind; it is counted as `inconsistent` and logged, and the run
  exits non-zero.
- *Rolling deploys are covered.* A worker still on the old model may index a
  document after step 1; it appears as stale and the next run of step 2 moves it.
  Run the re-index after the deploy has fully rolled out.

**Cost and duration.** Tokens ≈ the sum of `token_count` of stale chunks (the
chunker's count errs high). Duration is bounded by the provider's rate limit:
roughly `stale_chunks / ORBIT_EMBEDDING_BATCH_SIZE` requests. Run it off-peak
for a large corpus; the degraded window for dense retrieval is exactly this
duration.

### Scenario 2 — different width

The width is fixed in the column type, so this is a **schema migration**, not a
configuration change. Changing `ORBIT_EMBEDDING_DIMENSIONS` alone is refused
safely: the API's `/readyz` reports `embedding_schema` down, workers refuse to
embed (documents fail as a configuration defect rather than being corrupted),
and `reindex-embeddings` exits with an explanation.

Expand/contract procedure (not automated; do it as reviewed migrations):

1. **Expand.** Migration adds `embedding_next vector(N)` (nullable) and its
   HNSW index built `CONCURRENTLY` outside the migration transaction.
2. **Dual write.** Release the pipeline writing both columns: old model into
   `embedding`, new model into `embedding_next`.
3. **Backfill** `embedding_next` for existing chunks with a re-index pointed at
   the new column.
4. **Switch.** Release dense search reading `embedding_next`; verify recall on
   the benchmark and a sample of real queries.
5. **Contract.** Stop writing the old column; migration drops it and its index,
   renames `embedding_next`, and sets `NOT NULL`.

This keeps dense retrieval fully available throughout, at the cost of paying
for two embeddings per new chunk during the window. HNSW accepts at most 2,000
dimensions for `vector`; wider models need `halfvec` (4,000) or a truncated
`dimensions` parameter.

### Scenario 3 — chunking parameters change

Chunk *text* changes, so this is a reprocess, not a re-embed. Chunks are rebuilt
by the pipeline; any passage whose heading path and text did not change keeps
its vector through reuse. `index-status` reports chunk counts per
`chunker_version`.

### Scenario 4 — the provider changes a model behind the same id

Not detectable from stored metadata. Prefer versioned model ids where the
provider offers them; a scheduled canary (embed fixed reference texts, compare
with stored vectors) is the future mitigation and is not built.

### Why not blue/green in one column

A single `vector(1536)` column holds one vector per chunk, so old and new
vectors cannot coexist for the same chunk — hence the degraded dense window in
scenario 1. The alternatives were weighed in ADR-0020: a second column per
migration (scenario 2's procedure, usable for same-width changes too when a
degraded window is unacceptable), or a separate `chunk_embeddings` table keyed
by space with partial per-space HNSW indexes. The latter makes every model
change a data operation instead of a migration, and is the right move once
ORBIT needs two live spaces at once (per-workspace models, A/B evaluation). It
is not built yet because a partial index keyed by space cannot be matched by a
parameterised query plan, and nothing needs two live spaces today.

## 7. Operator commands

| Command | What it does |
|---|---|
| `npm run worker:index-status` | Coverage report; exit 3 while work remains |
| `npm run worker:reindex` | Move stale chunks into the active space; exit 3 if incomplete or inconsistent |
| `npm run bench:vector` | Recall and latency benchmark against a `*_test`/`*_bench` database |

## 8. Known limits

- **A new version re-embeds its unchanged passages.** Uploading a version
  deletes the previous version's chunks at once (so retrieval never returns text
  the document no longer contains), which removes the vectors reuse would have
  found. Deferring that deletion to the new version's index transaction would
  make a lightly edited document nearly free to re-index; it changes M3's
  version-swap semantics and is left as an explicit decision.
- **Dense retrieval is degraded during a same-width re-index** (scenario 1).
- **Re-index runs should not overlap.** It is safe, but two runs pay for the
  same vectors.
- **Token counts are approximate** (ADR-0013), so `ORBIT_EMBEDDING_MAX_BATCH_TOKENS`
  is a conservative bound, not an exact one.
