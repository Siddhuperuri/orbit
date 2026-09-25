# 0001 — Modular monolith with two runtime entrypoints

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

ORBIT spans authentication, workspace management, document ingestion, an
asynchronous processing pipeline, vector retrieval, and RAG-based question
answering. Those are distinct concerns, and the reflex in this space is to
deploy them as separate services.

The dominant data flow contradicts that reflex. Uploading a document writes a
document row, a job row, and (later) chunk rows and embedding rows that must all
agree. A document is not "READY" unless its chunks exist and are indexed. Split
across services, that invariant becomes a distributed transaction or a
saga — real engineering cost, paid permanently, to solve a problem we do not have.

At the same time, document parsing has a genuinely different runtime profile
from serving HTTP: it is long-running, CPU-bound, memory-hungry, and processes
untrusted input. It must not share a process with request handling.

## Decision

A single Python package, `orbit`, deployed as **two containers running the same
image with different entrypoints**:

- `api` — the FastAPI application, serving HTTP.
- `worker` — the Celery worker, consuming the processing queue.

Internal boundaries are enforced by a machine-checked import contract
(`backend/.importlinter`, run in CI), giving the module isolation that
microservices are usually adopted to obtain, without the network between them:

```
composition  →  api  →  { application, infrastructure }  →  domain  →  core
```

`application` and `infrastructure` are siblings that may not import each other.
`application` depends on ports declared in `domain`; `infrastructure` implements
them; only `composition` — the wiring root — knows about both.

## Alternatives considered

**Separate services per bounded context (auth, documents, search, chat).**
Rejected. It converts in-process function calls into network calls with partial
failure, adds four deployment pipelines, and forces eventual consistency onto
the document→chunk invariant, all before the first user exists.

**Single process running both HTTP and background work** (e.g. FastAPI
`BackgroundTasks`, or an in-process thread pool). Rejected. A large or
adversarial PDF would consume request-handler memory and CPU, and a deploy or
crash would lose in-flight work with no queue to recover from. Section 15 of the
engineering brief requires asynchronous processing.

**A monolith without enforced boundaries.** Rejected. "We will be disciplined"
is not a control. Layering that is not verified by CI degrades, and by the time
it is noticed the cost of repair exceeds the cost of the contract.

## Consequences

- API and worker scale independently, but always deploy together at the same
  version. Schema changes must therefore be backward-compatible across a
  rolling deploy — expand/contract migrations, never a breaking rename in one step.
- One image is built and one dependency set is resolved. The worker image
  carries FastAPI it does not use; a few megabytes is a fair price for
  guaranteeing the two processes never diverge.
- Violating a layer boundary fails CI with a named contract, so the reviewer
  discussion is about whether the design is right rather than whether the rule exists.
- If a component ever genuinely needs independent scaling or an independent
  release cadence, the enforced boundary is already the seam to extract along.
  This decision defers that split; it does not preclude it.
