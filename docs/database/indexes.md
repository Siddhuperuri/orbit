# Indexes

Every index below is justified by a named query. Blind indexing costs write
throughput and memory on every insert, so an index without a query behind it is
a defect, not a precaution.

Four of these are asserted by `EXPLAIN` in
[`tests/integration/test_transactions_and_queries.py`](../../backend/tests/integration/test_transactions_and_queries.py):
an index that *exists* is not an index that is *used*, and a mismatched sort
order or a wrapped column silently produces a correct answer via a sequential
scan.

---

## The ones that carry the product

| Index | Query it serves | Why this shape |
|---|---|---|
| `ix_documents_workspace_id_created_at_id` | Keyset-paginated document list — the most frequent query in the product | Column order matches `ORDER BY created_at DESC, id DESC` exactly; any other ordering sorts on disk instead of walking the index. Partial on `deleted_at IS NULL` because deleted rows are never listed. |
| `ix_chunks_embedding_hnsw` | Dense retrieval (ADR-0005) | HNSW builds incrementally, which suits documents arriving continuously; IVFFlat would need retraining as the corpus grows. `vector_cosine_ops` matches how embeddings are normalised. |
| `ix_chunks_search_vector` | Lexical retrieval | GIN over a **generated** `tsvector` column, so the index cannot drift from the text it indexes. |
| `ix_chunks_workspace_id_embedding_input_sha256` | The tenant pre-filter both retrievers apply, and embedding reuse (vectors looked up by input hash *within* a workspace) | HNSW cannot include a scalar column, so without a btree led by `workspace_id` the mandatory tenant predicate degrades to a scan. For a small workspace the planner uses it for an exact search instead of HNSW (measured in [vector-search.md](../benchmarks/vector-search.md)). |
| `ix_chunks_embedding_space` | Index coverage and the re-index sweep: chunks per embedding space, and chunks outside the active one | Two low-cardinality columns; B-tree deduplication keeps it a small fraction of the table. |
| `uq_users_email_lower` | Login | Functional and unique. Queries must compare `lower(email)`; comparing the raw column would scan on every login. Partial on live rows so a deleted account does not permanently reserve its address. |
| `ix_workspace_members_user_id_workspace_id` | Resolving an `AccessContext` | Issued on every authenticated request, so it is the hottest lookup in the system. |
| `ix_document_versions_workspace_id_status` | The processing status poll | Partial on `('pending','processing')`: ready and failed rows dominate and are never polled, so the index stays small and hot. |

## Uniqueness that enforces a rule

| Index | Rule |
|---|---|
| `uq_document_versions_current` | At most one current version per document. Also the index for the `documents → current version` join. |
| `uq_document_versions_workspace_content_current` | Deduplication, scoped to workspace and to *current* content (see [schema.md](schema.md)). |
| `uq_folders_parent_name` | No two folders with the same name under one parent. **`NULLS NOT DISTINCT` is essential** — without it PostgreSQL treats every `NULL` parent as unique, so any number of root folders could share a name. |
| `uq_workspaces_slug_lower` | Slugs appear in URLs, so uniqueness is global and case-insensitive. |
| `uq_refresh_tokens_token_hash` | A collision would let one token authenticate as another. |

## Known risk: HNSW under a selective filter

The single most important measurement in the system.

Both retrievers apply `workspace_id` **inside** the query (ADR-0004). An HNSW
scan beneath a selective pre-filter can under-return: it walks its graph, finds
too few rows that also satisfy the filter, and returns a short list rather than
the true top-k.

pgvector's **iterative index scan** (0.8+) is the mitigation, which is why
`tests/integration/test_infrastructure.py` asserts the installed extension
version rather than assuming it.

**Measured** ([vector-search.md](../benchmarks/vector-search.md), 100,000
synthetic chunks):

- Without iterative scan, recall@10 in a 50,000-chunk workspace was 0.942 at
  `ef_search` 40 and 0.987 at 100. With `relaxed_order` it was 1.000 at every
  setting. Search uses `relaxed_order`.
- Workspaces of 1,000 chunks or fewer never reached HNSW: the planner chose
  `ix_chunks_workspace_id_embedding_input_sha256` and an exact sort (recall
  1.000, p95 under 10 ms).
- `ef_search` 200 made the planner abandon HNSW for a sequential scan (166 ms
  instead of 2.5 ms). The default is 100; re-measure before raising it.
- `tests/integration/test_vector_index.py` asserts that the production
  statement *can* use the HNSW index. As with the other plan tests, the
  cheaper alternatives are disabled, because on a tiny table the planner is
  right not to use it.

## Deliberately absent

| Not indexed | Why |
|---|---|
| `documents.title` | Title search goes through the lexical retriever, not a `LIKE` scan. A `pg_trgm` index is the answer if a cheap prefix search is ever needed. |
| `chunks.content` | Indexed via `search_vector`; a second index on the raw text would double write cost for nothing. |
| `users.created_at` | No query orders users by creation date. |
| Anything on `document_tags` beyond its keys | The composite primary key serves document→tags; `ix_document_tags_workspace_id_tag_id` serves tags→documents. |

## Full inventory

| Table | Index | Columns | Kind |
|---|---|---|---|
| `audit_logs` | `ix_audit_logs_actor_user_id_created_at` | actor_user_id, created_at DESC | - |
| `audit_logs` | `ix_audit_logs_request_id` | request_id | partial |
| `audit_logs` | `ix_audit_logs_resource_type_resource_id` | resource_type, resource_id | - |
| `audit_logs` | `ix_audit_logs_workspace_id_created_at` | workspace_id, created_at DESC | - |
| `chunks` | `ix_chunks_document_id` | document_id | - |
| `chunks` | `ix_chunks_embedding_hnsw` | embedding | hnsw |
| `chunks` | `ix_chunks_embedding_space` | embedding_model, embedding_dimensions | - |
| `chunks` | `ix_chunks_search_vector` | search_vector | gin |
| `chunks` | `ix_chunks_workspace_id_embedding_input_sha256` | workspace_id, embedding_input_sha256 | - |
| `conversations` | `ix_conversations_workspace_id_user_id_created_at` | workspace_id, user_id, created_at DESC | partial |
| `document_processing_jobs` | `ix_jobs_document_version_id` | document_version_id | - |
| `document_processing_jobs` | `ix_jobs_queued_scheduled_for` | scheduled_for | partial (`queued`) |
| `document_processing_jobs` | `ix_jobs_running_lease_expires_at` | lease_expires_at | partial (`running`) |
| `document_processing_jobs` | `uq_jobs_active_per_version` | document_version_id | unique, partial (`queued`, `running`) |
| `document_tags` | `ix_document_tags_workspace_id_tag_id` | workspace_id, tag_id | - |
| `document_versions` | `ix_document_versions_storage_key` | storage_key | - |
| `document_versions` | `ix_document_versions_workspace_id_status` | workspace_id, status | partial |
| `document_versions` | `uq_document_versions_current` | document_id | unique partial |
| `document_versions` | `uq_document_versions_workspace_content_current` | workspace_id, content_sha256 | unique partial |
| `documents` | `ix_documents_workspace_id_created_at_id` | workspace_id, created_at DESC, id DESC | partial |
| `documents` | `ix_documents_workspace_id_folder_id` | workspace_id, folder_id | partial |
| `folders` | `ix_folders_workspace_id_parent_folder_id` | workspace_id, parent_folder_id | partial |
| `folders` | `uq_folders_parent_name` | workspace_id, parent_folder_id, lower(name) | unique partial |
| `message_citations` | `ix_message_citations_message_id` | message_id | - |
| `message_citations` | `ix_message_citations_workspace_id_document_id` | workspace_id, document_id | - |
| `refresh_tokens` | `ix_refresh_tokens_family_id` | family_id | - |
| `refresh_tokens` | `ix_refresh_tokens_user_id_expires_at` | user_id, expires_at | - |
| `tags` | `uq_tags_workspace_id_name_lower` | workspace_id, lower(name) | unique |
| `users` | `uq_users_email_lower` | lower(email) | unique partial |
| `workspace_members` | `ix_workspace_members_owners` | workspace_id | partial |
| `workspace_members` | `ix_workspace_members_user_id_workspace_id` | user_id, workspace_id | - |
| `workspaces` | `uq_workspaces_slug_lower` | lower(slug) | unique partial |

Total: 30 indexes
