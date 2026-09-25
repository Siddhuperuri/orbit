# 0014 — Typed domain errors mapped once to a stable error envelope

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

Sections 8 and 12 of the engineering brief require a consistent,
machine-readable error contract that never leaks stack traces, SQL, filesystem
paths, or internal architecture, and forbid silently swallowing exceptions.

The failure mode this prevents is specific and common: `raise HTTPException(404,
"Document not found")` written inside a use case. It couples business logic to
HTTP, makes the use case untestable without a web framework, and puts
user-visible copy in the middle of domain code. Repeat it thirty times and the
API has thirty slightly different error shapes.

## Decision

### Errors are typed in the domain and carry a stable code

```python
class OrbitError(Exception):
    code: ClassVar[str]           # stable, machine-readable, part of the API contract
    http_status: ClassVar[int]    # advisory; the mapping layer decides
    def __init__(self, message: str, **context: object) -> None: ...
```

Concrete errors are declared per concern — `DocumentNotFound`,
`WorkspaceAccessDenied`, `UnsupportedContentType`, `UploadTooLarge`,
`EmbeddingProviderUnavailable`, `RefreshTokenReused`. Each has a code that is
**part of the public API contract** and does not change once released.

`http_status` lives next to the error rather than in a lookup table so that
adding an error cannot silently default to 500. But the layer that owns the HTTP
response is `api`, not `domain` — the attribute is advisory, and `domain` never
imports anything HTTP.

### One handler, one envelope

A single exception handler translates every `OrbitError` into:

```json
{
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "The requested document could not be found.",
    "request_id": "01JB2X8N4K7QF3TVWZ9M5PDCRA",
    "details": null
  }
}
```

`details` carries field-level validation errors and nothing else. Anything
unmapped becomes `INTERNAL_ERROR` with a generic message; the exception, its
traceback, and its context are logged server-side against the same `request_id`,
so support can find the incident from what the user saw without the user ever
seeing the internals.

**The `message` is written for a user. The `code` is written for a client.**
Clients branch on `code`; messages may be reworded or localised freely.

### The three categories, and what each means

| Category | Example | Status | Retryable |
|---|---|---|---|
| **Client error** — the request is wrong | `UNSUPPORTED_CONTENT_TYPE` | 4xx | No, not as sent |
| **Transient dependency failure** — the request is fine | `EMBEDDING_PROVIDER_UNAVAILABLE` | 503 + `Retry-After` | **Yes** |
| **Defect** — we are wrong | `INTERNAL_ERROR` | 500 | Unknown |

This distinction is not cosmetic. It is the same classification the worker uses
to decide whether to retry a job (ADR-0002): transient failures retry with
backoff, client errors fail permanently and immediately, defects fail and page
someone. Sharing one taxonomy between the HTTP layer and the queue means a
provider outage behaves consistently in both.

### Not-found versus forbidden

Requesting a resource in a workspace the caller cannot access returns
**404, not 403**. A 403 confirms the resource exists, which leaks membership and
document existence across tenants. 403 is reserved for the case where the caller
provably has access to the workspace but lacks the specific permission — where
the caller already knows the resource exists.

### Exceptions are never swallowed

`except: pass` and bare `except Exception` without a re-raise fail lint (ruff
`TRY`/`BLE`). Every `except` clause must convert to a domain error, log with
context and re-raise, or handle the case with a comment saying why that is
correct.

## Alternatives considered

**`HTTPException` throughout.** Rejected: couples domain to HTTP and makes use
cases untestable without a framework.

**RFC 9457 `application/problem+json`.** A real standard with real tooling.
Rejected narrowly: its `type` URI adds ceremony over a short stable `code`, and
its flat member namespace mixes protocol and application fields. The envelope
here is a strict subset in spirit and could be migrated to it additively.

**Returning result objects (`Result[T, E]`) instead of raising.** Explicit
error paths, no invisible control flow. Rejected: Python has no ergonomic
support for it, every call site grows unwrapping boilerplate, and it fights the
language and its libraries.

**A single generic error message for all failures.** Maximum information
hiding. Rejected: the client cannot distinguish "retry this" from "fix your
request", so every failure degrades to the same dead end.

## Consequences

- Error codes are a **versioned API contract**. Adding one is backward
  compatible; renaming or repurposing one is breaking and requires a version
  bump. The catalogue lives in `docs/api/errors.md`.
- Every error path is testable without HTTP: use cases raise domain errors, and
  the mapping is tested separately and exhaustively — a test enumerates every
  `OrbitError` subclass and asserts each has a unique code and an explicit
  status, so a new error cannot ship unmapped.
- `request_id` appears in every response, including successes, so a user can
  quote it from any screen.
- Structured error context (`document_id`, `workspace_id`) is attached at raise
  time for logs and deliberately **not** serialised into `details`, which stays
  reserved for validation.
- The frontend has a single place that parses this envelope, so retry, toast,
  and inline-field behaviour is decided from `code` rather than from string
  matching on messages.
