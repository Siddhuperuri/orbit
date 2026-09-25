# 0011 — Object storage abstraction, proxied upload, presigned download

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

Section 16 of the engineering brief requires that uploads be treated as hostile
input: size, MIME type, extension, filename, and content all validated, with
generated storage keys rather than user-supplied paths.

That requirement collides with the conventional way to make uploads scale.
**Presigned PUT** hands the browser a URL and takes the API out of the data
path — but the object then exists before the application has seen a single byte,
so no server-side validation can happen *before* untrusted content is durable.

There is a second problem that is easy to miss: **object storage and PostgreSQL
cannot participate in one transaction.** Whatever the design, there is a window
where one has committed and the other has not, and the ordering determines which
inconsistency is possible.

## Decision

### The port

```python
class ObjectStorage(Protocol):
    async def put_stream(self, key: StorageKey, stream: AsyncIterator[bytes],
                         content_type: str, content_length: int) -> StoredObject: ...
    async def open_stream(self, key: StorageKey) -> AsyncIterator[bytes]: ...
    async def presigned_get_url(self, key: StorageKey, ttl: timedelta,
                                download_filename: str) -> str: ...
    async def delete(self, key: StorageKey) -> None: ...
    async def exists(self, key: StorageKey) -> bool: ...
```

Streams, never `bytes`. A signature taking `bytes` guarantees that some caller
eventually loads a 50 MiB document into memory, and that N concurrent uploads
become an out-of-memory kill.

MinIO locally, AWS S3 in production, through **one boto3-backed adapter**. MinIO
is S3-API-compatible, so the adapter differs only by endpoint URL and
addressing style. boto3 is synchronous and is called through a thread offload
rather than adding `aioboto3`; the calls are I/O-bound and few, and the
dependency is not worth its weight.

### Storage keys contain no user input

```
workspaces/{workspace_id}/documents/{document_id}/{content_sha256}
```

All three components are server-generated. The original filename is stored as a
**database column**, never as a path segment, and is returned to the user only
through the `Content-Disposition` of a presigned download. Path traversal is not
mitigated — it is unrepresentable.

### Uploads are proxied and validated while streaming

The request streams through the API, which as bytes pass:

1. counts them, aborting the instant the configured limit is exceeded — the
   declared `Content-Length` is a hint, never a control;
2. computes SHA-256 for deduplication and integrity;
3. sniffs the leading bytes to determine the real content type, and aborts if it
   contradicts the claimed type or is unsupported;
4. writes to object storage.

A `%PDF-` header is checked, not the `.pdf` extension and not the browser's
`Content-Type`. Both of the latter are attacker-controlled.

### Ordering: storage first, database second

Storage write commits, then the database transaction creating the document row
and its processing job commits.

This ordering is deliberate. The two possible failures are not symmetric:

| Order | Failure | Result |
|---|---|---|
| Storage → DB | DB commit fails | **Orphaned object.** Invisible to users, reclaimed by a sweep. Harmless. |
| DB → storage | Storage write fails | **Document row with no bytes.** Visible, broken, and the pipeline fails on it. |

An orphan costs storage; a dangling row costs correctness. A scheduled sweep
deletes objects with no referencing row after a grace period well beyond the
longest plausible in-flight upload.

### Downloads are presigned, after authorization

The API authorizes the request, then returns a short-lived (60 s) presigned
`GET` URL. The API is not in the download data path, but access control is still
enforced by the application, not by the bucket. The bucket denies all anonymous
access.

## Alternatives considered

**Presigned PUT direct from the browser.** The scalable answer, and the intended
migration path. Rejected for v1: content becomes durable before any validation,
so the bucket must be treated as holding unvalidated data, requiring a staging
prefix, a promotion step, lifecycle expiry for abandoned uploads, and bucket
CORS. That is the right complexity to add when upload bandwidth actually
constrains the API — and the `ObjectStorage` port plus worker-side revalidation
means adding it later is additive, not a rewrite.

**Multipart upload with client-side chunking.** Necessary above ~100 MiB.
Rejected at a 50 MiB ceiling: real complexity (part tracking, resumption,
abandoned-upload cleanup) for a limit we do not have.

**Storing documents in PostgreSQL as `bytea` or large objects.** Rejected:
bloats the database and its backups, makes point-in-time recovery slow and
expensive, and gives up cheap durable storage for no benefit.

**`aioboto3`.** Rejected: an additional dependency tracking botocore internals,
to avoid a thread offload on a handful of I/O-bound calls.

**Trusting the browser's `Content-Type`.** Rejected — it is attacker-controlled.
It is recorded for diagnostics and never used for a decision.

## Consequences

- The API is in the upload data path, so upload concurrency consumes API
  workers. Request timeouts and body-size limits are set with that in mind, and
  upload concurrency is a metric to watch.
- Deduplication by `content_sha256` within a workspace means re-uploading an
  identical file returns the existing document instead of reprocessing it —
  saving both embedding cost and user confusion. Deduplication is
  **workspace-scoped**: a shared global namespace would leak the existence of
  another tenant's document through a hash collision check.
- The orphan sweep is a scheduled job that must exist before launch, or storage
  cost grows silently with every failed upload.
- Validation while streaming is necessarily partial — only leading bytes are
  seen before the write commits. The worker revalidates the complete object
  before parsing. **The upload check is a fast rejection, not the security
  boundary.**
- Presigned URLs are bearer capabilities. TTL is kept short, they are never
  logged, and they are minted per request rather than cached.

## Implementation notes (added when M4's document storage subsystem was built)

Three decisions below refine this ADR rather than reverse it — each is a
consequence of building the design above against a real streaming pipeline,
recorded here so the "why" is not lost the next time someone reads the key
format and wonders why it does not match the object-storage literature they
have seen.

### The storage key uses a version id, not the content hash

The key actually implemented is
`workspaces/{workspace_id}/documents/{document_id}/{version_id}`, not
`.../{content_sha256}` as the Decision section above illustrates. The reason
is ordering: `content_sha256` is only known once the upload stream has been
**fully read** (it is a running hash over every chunk), but the key must exist
**before the first byte is written** — storage has no operation that lets a
key be chosen after the fact without a copy-and-delete, which trades one
network round trip for another for no benefit. `document_id` and `version_id`
are UUIDv7s minted by the application before the stream is touched, satisfying
the same requirement the hash-based key was illustrating: every component is
server-generated, unpredictable, and workspace/document-scoped. Path traversal
remains unrepresentable for the same reason as before — nothing user-supplied
reaches the key.

### Uploads are a raw request body, not `multipart/form-data`

FastAPI's conventional `UploadFile` (`File(...)`) is backed by Starlette's own
multipart parser, which fully consumes the request body into a
`SpooledTemporaryFile` **before a route handler runs at all**. By the time a
handler saw an `UploadFile`, "abort the instant the configured limit is
exceeded" would already be moot — the oversized body would already be fully
received. The upload and add-version endpoints therefore read
`Request.stream()` directly: every chunk that arrives off the wire reaches the
validating wrapper (`core/uploads.py`) before anything else touches it, which
is what makes the live byte-counter an actual control. Upload metadata
(`filename`, `title`, `folder_id`) travels as query parameters, since there is
no form body left to carry it.

### True multipart to S3, at the real 5 MiB part minimum

`ObjectStorageClient.put_stream` buffers only up to one part (8 MiB) at a
time, uploading each part as it fills via S3's own multipart API
(`create_multipart_upload` / `upload_part` / `complete_multipart_upload`), and
aborting the multipart upload if the source stream fails partway through — an
abandoned multipart upload otherwise sits in the bucket, invisible to a normal
listing, billed indefinitely. An upload that never crosses one part is written
with a single `put_object`, since multipart's coordination overhead buys
nothing below that size. Every non-final part must be at least 5 MiB per the
S3 API; this is a genuine floor, not a tunable, and it is what the test suite
(`tests/unit/test_s3_adapter.py`, against moto) exercises directly rather than
around.

### Upload has its own rate limit, reusing ADR-0017's mechanism

Bounded per request by `ORBIT_MAX_UPLOAD_BYTES`, but an unlimited *rate* of
even small, valid uploads is still a resource-exhaustion path — each one costs
a streaming hash, an S3 write, and a database row. `UploadDocument` and
`AddDocumentVersion` reuse the same account+IP fixed-window guard
[ADR-0017](0017-rate-limiting.md) introduced for authentication, checked
before a single byte of the body is read.

### Storage lifecycle, restated as states

1. **Written** — object exists in storage, no database row yet (a request in
   flight; normally sub-second).
2. **Referenced** — object exists, `document_versions` row exists,
   `status = pending` (awaiting the future processing pipeline).
3. **Superseded** — a later version became current; the object is retained (a
   restore is possible) but is no longer reachable through `GET`.
4. **Soft-deleted** — the owning document's `deleted_at` is set; the object is
   retained, reclaimed only by a future hard-delete sweep (not yet
   implemented; today, deletion is soft only, matching every other entity in
   ORBIT).
5. **Orphaned** — object exists, no row references it (state 1 that never
   reached state 2, or a deduplication race's loser whose own cleanup delete
   itself failed). Reclaimed by `SweepOrphanedStorage`
   (`application/documents/sweep_orphaned_storage.py`), which lists a
   workspace's prefix, checks each key against
   `exists_by_storage_key` (backed by `ix_document_versions_storage_key`), and
   deletes what nothing references, after a grace period long enough that no
   in-flight upload is ever caught in it.

`SweepOrphanedStorage` is implemented and unit-tested as a plain callable, not
yet wired to a schedule — no Celery Beat configuration exists in this codebase
to wire it to. Until it is, orphans cost storage, not correctness.
