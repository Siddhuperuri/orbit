# Test coverage report

Generated 2026-09-24 against the working tree, with PostgreSQL, Redis, and MinIO
running. Every number below came from a run on this machine; nothing is
estimated.

## Suite totals

| Layer | Tests | Result | Coverage of its target |
|---|---|---|---|
| Backend unit + API | **1,589 passed**, 1 skipped | green | **85.5%** of `src/orbit` — 10,405 statements, 1,324 missed, branch coverage on |
| Backend integration | **280 passed** | green | the SQL, pgvector, S3 and migration paths |
| Web unit + component | **426 passed** (35 files) | green | **52.0%** statements, 48.9% branches, 40.1% functions, 52.4% lines |
| End-to-end | **32 passed** | green | the deployed stack, in a browser |

The one skip is deliberate and named: a metrics-server port-collision test that
cannot hold on Windows, where two sockets may bind one port. Nothing is
`xfail`ed and nothing is quarantined.

### Reading the backend number honestly

85.5% is the **unit + API** figure, and it is measured with the integration
suite deselected. Those two facts explain almost every low number in the
per-module table below, so the total should not be read as "15% of the backend
is untested".

The SQL repositories sit between 19% and 55% *in that run* because the tests
that exercise them are the integration suite, which the run excludes. Run
`npm run backend:test:coverage:all` with infrastructure up for the combined
figure.

## Modules under 70% (unit + API run)

| Module | Statements | Covered | Where it is actually tested |
|---|---|---|---|
| `composition/worker.py` | 105 | 0% | Celery entry point — exercised by the worker running under E2E, not importable in-process |
| `composition/worker_container.py` | 59 | 0% | as above |
| `composition/asgi.py` | 4 | 0% | the ASGI entry point; every E2E and API test runs the app it builds |
| `db/repositories/documents.py` | 230 | 19% | `tests/integration/test_documents.py`, `test_document_organization.py` |
| `db/repositories/conversations.py` | 111 | 29% | `tests/integration/test_conversations.py` |
| `db/repositories/folders.py` | 77 | 30% | `tests/integration/test_document_organization.py` |
| `db/repositories/memberships.py` | 62 | 31% | `tests/integration/test_identity.py` |
| `db/repositories/processing.py` | 135 | 31% | `tests/integration/test_processing_pipeline.py` |
| `db/repositories/tags.py` | 83 | 32% | `tests/integration/test_document_organization.py` |
| `db/repositories/search.py` | 65 | 33% | `tests/integration/test_hybrid_search.py` |
| `db/repositories/embeddings.py` | 66 | 41% | `tests/integration/test_vector_index.py` |
| `db/repositories/users.py` | 46 | 44% | `tests/integration/test_identity.py` |
| `db/repositories/workspaces.py` | 48 | 44% | `tests/integration/test_tenant_isolation.py` |
| `infrastructure/health.py` | 79 | 64% | partly `test_failure_modes.py`; see gaps below |
| `domain/ports/repositories.py` | 113 | 64% | protocol definitions — the uncovered lines are `...` bodies |

## What the end-to-end layer covers

All 32 pass in ~50 s against a real stack — a production Next build, the real API, a real
Celery worker, real PostgreSQL/pgvector/Redis/MinIO. The only substituted
component is the AI provider (`fake`, ADR-0007).

| Spec | Covers |
|---|---|
| `journey.spec.ts` | register → login → workspace → upload → process → search → open → ask → inspect citation → log out, plus deduplication and the `?next=` round trip |
| `auth.spec.ts` | wrong password, account enumeration (login *and* password reset), duplicate email, client-side validation, open redirect |
| `uploads.spec.ts` | unsupported type, extension that lies about the bytes, empty file, truncated PDF, oversize, cross-tenant upload |
| `security.spec.ts` | IDOR (including an id smuggled into a workspace the caller owns), cross-tenant read/write/delete, search isolation, anonymous access, forged cookie, logout revocation, refresh-token replay, CSRF origin checks, malformed input, brute-force throttling |
| `reliability.spec.ts` | readiness per dependency, duplicate/idempotent reprocess, a document that fails processing reaching a terminal state, an ungrounded answer citing nothing |

Two things it deliberately does **not** do, and where they are covered instead:

- **A lying `Content-Length`.** Playwright recomputes it from the body, so the
  lie never reaches the server. Covered in
  `backend/tests/api/test_documents_upload_router.py`.
- **Taking a dependency away** (storage refusing a write, the database dropping
  mid-transaction). Stopping a container mid-suite would make every other test
  in the run non-deterministic. Covered in
  `backend/tests/integration/test_failure_modes.py`.

## A defect the new tests found

Registering with an address that already exists answered **500, not 409**.

`UnitOfWork.commit()` and `UnitOfWork.flush()` translate a constraint violation
into a domain `ConflictError`, but several repositories called
`session.flush()` directly — deliberately, so a violation is attributable to
their own statement rather than to a later commit. Those calls bypassed the
translation, so the driver's `IntegrityError` escaped, reached the error handler
as an unrecognised exception, and was answered as an internal error. A person
signing up with an address they had already used was told something had gone
wrong on our side, rather than the one thing they could act on.

It had not been caught because the layer that tests registration end to end did
not exist, and the API-level test *injects* a `ConflictError` rather than
letting the real repository raise one:

```python
# tests/api/test_auth_router.py
register.execute.side_effect = ConflictError("An account with that email address already exists.")
```

That test asserts the router's handling of a conflict, which is a real thing to
assert — but it cannot notice that the repository never produces one.

Fixed by routing every repository flush through
`flush_translating_conflicts()` (`infrastructure/db/errors.py`).
`conversations.py` keeps the raw flush, because its handler distinguishes one
constraint from the rest and needs the driver's own error to do it.

The integration tests that asserted `IntegrityError` for these paths were
asserting the leak. They now assert `ConflictError`, and one new test states the
contract directly: the error carries a 409, a user-readable message, and neither
the constraint name nor the SQL.

## What remains genuinely untested

These are gaps, not artefacts of how the run was sliced.

1. **Celery's own execution model.** `composition/worker.py` is covered at 0% by
   the Python suites and only indirectly by E2E. Signal handlers
   (`worker_process_shutdown`, the prefork metrics guard), the memory ceiling,
   and the hard time limit are configuration that no test asserts. A regression
   that stopped enforcing `worker_max_memory_per_child_kb` would pass every
   suite. Worth a dedicated test that boots a worker and asserts the applied
   Celery config.

2. **Prefork behaviour.** Everything in CI and locally runs `--pool=solo`
   because Windows has no `fork(2)`. The prefork pool is what the production
   image uses and what ADR-0002 relies on for enforcing limits against a hostile
   parser. It is exercised by the `compose` CI job only to the extent that the
   stack boots.

3. **Object storage under real failure.** `test_failure_modes.py` covers storage
   errors through moto and a dead endpoint. Not covered: a write that succeeds
   then disappears, partial multipart uploads abandoned mid-flight, and the
   orphan sweep (`sweep_orphaned_storage.py`) against a bucket that genuinely
   diverged from the database.

4. **Database failure mid-transaction.** Connection loss is covered by pointing
   at a closed port. A connection dropped *during* a transaction, and pool
   exhaustion under concurrency, are not — both need a proxy that can sever a
   live socket, which nothing in the suite provides.

5. **Provider timeout and retry at the edges.** Retry policy and circuit
   breaking are unit-tested against fakes. Under E2E the provider is the
   deterministic `fake`, which never times out, so the real client's timeout
   handling (`resilient_llm.py`, `resilient_embeddings.py`) is never exercised
   against a socket that stalls.

6. **Real AI providers.** By design (ADR-0007) no test uses an OpenAI
   credential. The OpenAI adapters are covered only by unit tests against a
   stubbed transport, so a change in the vendor's response shape would not be
   caught here. Retrieval *quality* is likewise not measured by any test: under
   the fake provider, similarity is lexical overlap, so these suites prove the
   pipeline is wired correctly, never that it answers well.

7. **Concurrency.** Optimistic concurrency is tested with two sequential writers
   simulating a race. Genuine parallel load — simultaneous uploads of the same
   content, concurrent reprocess of one document, two members editing one
   folder tree — is not tested at all.

8. **The frontend's uncovered half.** Web coverage is 52% of statements. The
   tested parts are the ones that carry logic: forms and their validation and
   error states, the API client, citations, search parsing, the upload queue,
   the document lifecycle. The uncovered remainder is mostly presentational —
   layout, navigation chrome, settings screens, and page shells — plus two
   areas that do deserve tests: the members/roles screens, and the streaming
   chat transcript's abort and reconnect paths.

9. **Accessibility.** `axe-core` is a dependency of the web package but no test
   runs it. Keyboard traps, focus order, and contrast are unverified beyond the
   ARIA assertions individual component tests happen to make.

10. **Performance and limits.** There are benchmarks (`benchmarks/`) but no test
    asserts a bound. Nothing fails if p95 search latency doubles, if a document
    with 10,000 chunks makes the library page unusable, or if the context
    builder exceeds its token budget on a pathological document.

## Determinism and credentials

- No suite reads a real API key. The backend suites construct settings
  explicitly with `ai_provider=fake` and `_env_file=None`, so a developer's
  `.env` cannot change a result. The E2E stack is started with
  `ORBIT_AI_PROVIDER=fake` and does not forward a key even when one is present.
- The fake embedding provider is feature-hashed and L2-normalised: identical
  text gives an identical vector on every machine, which is what lets the
  retrieval assertions be exact.
- E2E gets its own database, its own Redis databases, and its own ports, all
  reset before the run; per-IP rate-limit counters are flushed with them, since
  their window outlives a run.
