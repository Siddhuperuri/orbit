# API documentation

OpenAPI is generated from the FastAPI application and served at `/docs`
(interactive) and `/openapi.json` (schema) in non-production environments.

This directory holds what the generated schema cannot express:

- `conventions.md` — pagination, filtering, sorting, and error-envelope contracts (M1)
- `errors.md` — the machine-readable error code catalogue (M1)
- `versioning.md` — how `/api/v1` evolves and what constitutes a breaking change (M1)

Not yet written; added with the endpoints they describe.

## Search

`POST /api/v1/workspaces/{workspace_id}/search` with
`{"query": "...", "limit": 10, "mode": "hybrid" | "lexical" | "semantic", "document_ids": [...]}`.
`POST` so the query text stays out of access logs. Each result is structured:
`document`, `version`, `chunk`, `location` (pages, heading path, character
offsets), `relevance` (fused score, and each retriever's rank and native score),
`rank`, and `matched_by`. The response's `retrievers` and `degraded` fields
state which retrievers actually ranked the results. Ranking:
[ADR-0021](../decisions/0021-hybrid-search-baseline.md).

## Conversations and answers

Under `/api/v1/workspaces/{workspace_id}/conversations`. A conversation is
private to the user who created it; anyone else -- including other members of
the workspace -- gets `404`. Every route requires `chat:use`.

| Method | Path | |
|---|---|---|
| `POST` | `` | Create. `{"title": "..."}` (optional). `201`. |
| `GET` | `` | The caller's conversations, newest first; keyset-paginated. |
| `GET` | `/{id}` | One conversation. |
| `DELETE` | `/{id}` | Soft delete. `204`. |
| `GET` | `/{id}/messages?after=&limit=` | Messages in thread order, with citations. Pass `next_after` back as `after`. |
| `POST` | `/{id}/messages` | Ask. `{"question": "...", "document_ids": [...]}`. `201` with the assistant message. |
| `POST` | `/{id}/messages/stream` | Ask, answered as Server-Sent Events. |

An assistant message carries `status` (`complete` \| `partial` \| `failed`),
`grounding` (`grounded` \| `uncited` \| `insufficient_evidence` \|
`no_evidence` -- anything but `grounded` must be rendered as weaker),
`stop_reason`, `failure` (`stage` + `code`), `usage`, `metrics`, and
`citations`. Each citation has the `handle` used inline in `content` (`[S1]`),
`document`, `version`, `chunk`, `location` (pages, heading path, offsets), and a
`snippet` -- all copied from the retrieved chunk's record, never from model
output ([ADR-0006](../decisions/0006-bound-citations.md)).

**Streaming.** Events: `started`, `retrieval`, `delta` (repeated), then exactly
one `done` (the full message) or `error` (`code`, `message`, `request_id`,
`retryable`, `retry_after_seconds`, and the partial `answer` if one was
recorded). `delta` text is provisional; replace it with the `done` message,
whose citations are resolved and from which any unresolvable handle has been
removed. Failures before generation -- `404`, `409 ANSWER_IN_PROGRESS`, `422`,
`429`, `503 RETRIEVAL_FAILED` -- are ordinary HTTP errors, not events.
Closing the stream cancels generation; what was produced is kept as `partial`.

**Failure codes.** `RETRIEVAL_FAILED` (evidence could not be fetched; the model
was not called), `GENERATION_FAILED`, `GENERATION_TIMEOUT`,
`GENERATION_RATE_LIMITED` (with `Retry-After`), `CONVERSATION_UNAVAILABLE`,
`ANSWER_IN_PROGRESS`. Design: [ADR-0022](../decisions/0022-grounded-question-answering.md).

## Documents, folders and tags

Under `/api/v1/workspaces/{workspace_id}`. Design and rationale:
[ADR-0023](../decisions/0023-document-organization.md).

- `GET /documents` — server-side filters `q` (title substring), `status`, `folder_id` /
  `unfiled`, `tag_id` (repeatable, all-of), `archive` (`active` | `archived`), and
  `sort` (`created_desc` | `created_asc` | `updated_desc` | `title_asc` | `title_desc`).
  Keyset-paginated; a cursor is only valid with the sort it was issued for.
- `PATCH /documents/{id}` — rename and/or move. Requires `expected_version`; `folder_id`
  is read by *presence* (absent leaves it, `null` unfiles). `409` on a stale version.
- `POST /documents/{id}/archive` and `/restore` — idempotent.
- `PUT|DELETE /documents/{id}/tags/{tag_id}` — idempotent attach/detach.
- `GET /documents/{id}/versions` (newest first, `before`/`next_before`),
  `GET …/versions/{version_id}/download`, and `GET /documents/{id}/content` (the indexed
  passages, overlap-free, paged by `after`/`next_after`).
- `GET|POST /folders`, `PATCH|DELETE /folders/{id}` — a folder can be deleted only when it
  holds no documents (archived included) and no subfolders: `409 FOLDER_NOT_EMPTY`.
- `GET|POST /tags`, `PATCH|DELETE /tags/{id}` — colours are a closed palette.
- `GET /api/v1/meta` — includes `uploads` (`max_bytes`, `extensions`) so clients can state
  the limits before sending anything.

A version's `processing_stage` is present only while it is pending or processing; there is
no percentage, because none exists to report.
