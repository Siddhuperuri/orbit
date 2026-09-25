# Architecture — start here

ORBIT is an intelligent knowledge and document platform: documents go into a
workspace, and questions are answered from their contents with citations that
resolve to the exact source passage.

This page is a map. The detail lives in the documents below, and the reasoning
behind each significant choice lives in [the ADRs](../decisions/README.md).

## The documents

| Document | Covers |
|---|---|
| **[system.md](system.md)** | Runtime topology, data ownership, local and production environments, testing architecture, observability, failure behaviour, scaling |
| **[backend.md](backend.md)** | Layering and the import contract, request path, API conventions, database access and schema rules, indexes, object storage, background processing, configuration |
| **[frontend.md](frontend.md)** | Feature organisation, the server/client boundary, data access, streaming, design system, required UI states, accessibility, performance |
| **[data-flow.md](data-flow.md)** | Upload, processing state machine, idempotency, failure classification, search, ask, authentication, deletion, correlation |
| **[ai-pipeline.md](ai-pipeline.md)** | Provider ports, the fake provider, ingestion and retrieval sides, context construction, citation binding, provider migration |
| **[security.md](security.md)** | Threat model, authentication, authorization model, hostile file handling, web security, information disclosure, prompt injection, known gaps |

## The shape, in one picture

```
browser ─▶ reverse proxy (single origin) ─┬─▶ web    (Next.js — shell only)
                                          └─▶ api    (FastAPI)
                                                │
                        PostgreSQL + pgvector ◀─┼─▶ Redis ─▶ worker (Celery, prefork)
                        (source of truth)       │                    │
                                                └─▶ S3 / MinIO ◀─────┘
```

One Python package, two processes split by runtime characteristics: the API
serves bounded request work, the worker runs long, CPU-bound, untrusted document
processing under process isolation.

Internal dependency direction, **enforced in CI** by `import-linter`:

```
composition → api → { application, infrastructure } → domain → core
```

`application` and `infrastructure` cannot import each other, and `api` cannot
import `infrastructure` at all. A violation fails the build with the name of the
contract it broke.

## The five decisions that shape everything else

If you read nothing else, read these.

1. **[ADR-0001](../decisions/0001-modular-monolith.md) — Modular monolith.**
   Microservices would turn the document→chunk invariant into a distributed
   transaction. Boundaries are enforced by a machine-checked import contract
   instead of by a network.

2. **[ADR-0004](../decisions/0004-authorization-in-repositories.md) —
   Authorization lives in repository signatures.** Omitting a tenant check is a
   type error, not a data breach. This matters most in vector search, where a
   missing predicate returns another tenant's *most relevant* content.

3. **[ADR-0002](../decisions/0002-celery-prefork-job-queue.md) — Prefork
   workers.** Parsing untrusted PDFs needs SIGKILL-backed time limits and memory
   ceilings. A single-process asyncio worker cannot preempt a blocking parser.

4. **[ADR-0006](../decisions/0006-bound-citations.md) — Citations are resolved,
   not parsed.** There is no code path from generated tokens to citation
   metadata, so a fabricated reference cannot reach a user.

5. **[ADR-0009](../decisions/0009-single-origin-cookie-transport.md) — One
   origin, `HttpOnly` cookies.** No credential is readable by JavaScript, so XSS
   cannot exfiltrate a portable token.

## Reading order

- **New to the codebase:** this page → [system.md](system.md) → [backend.md](backend.md)
- **Working on ingestion:** [data-flow.md](data-flow.md) → ADR-0012, ADR-0013
- **Working on search or chat:** [ai-pipeline.md](ai-pipeline.md) → ADR-0005, ADR-0006
- **Working on the frontend:** [frontend.md](frontend.md) → ADR-0016, ADR-0009
- **Reviewing security:** [security.md](security.md) → ADR-0003, ADR-0004, ADR-0009, ADR-0011
