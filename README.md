# ORBIT

An intelligent knowledge and document platform. Upload documents into a
workspace, and ask questions that are answered from their contents with
citations that resolve to the exact source passage.

> **Status: in development.** M0–M4 are implemented: identity and workspaces,
> document upload, and the asynchronous processing pipeline (parse → normalize →
> chunk → embed → index). Retrieval and chat do not exist yet. See
> [Roadmap](#roadmap) for what exists today and what does not. ORBIT is not
> production-ready and is not described as such anywhere in this repository
> until the engineering audit in `docs/operations/` says otherwise, with
> evidence.

---

## Architecture at a glance

A modular monolith: one Python package deployed as two processes, plus a Next.js
frontend.

```
              ┌──────────────────────────────┐
  browser ───▶│ reverse proxy — ONE origin   │   /     → web
              │ TLS termination              │   /api/* → api
              └───────┬──────────────┬───────┘
                      │              │
            ┌─────────▼────┐   ┌─────▼─────────┐      ┌──────────────┐
            │ web (Next)   │   │ api (FastAPI) │─────▶│ Redis        │
            │ shell only   │   │               │ jobs │ broker+cache │
            └──────────────┘   └──────┬────────┘      └──────┬───────┘
                                      │                      │ consume
                                      │          ┌───────────▼────────┐
                                      │          │ worker (Celery)    │
                                      │          │ parse→chunk→embed  │
                                      │          └───────────┬────────┘
                                      │                      │
                          ┌───────────▼──────────────────────▼────────┐
                          │ PostgreSQL + pgvector  │  S3 / MinIO      │
                          │ source of truth        │  document bytes  │
                          └───────────────────────────────────────────┘
```

A single origin is load-bearing, not cosmetic: it is what lets both auth tokens
be `HttpOnly` cookies that JavaScript cannot read
([ADR-0009](docs/decisions/0009-single-origin-cookie-transport.md)).

Internal dependency direction, enforced in CI by `import-linter`:

```
composition → api → { application, infrastructure } → domain → core
```

`application` and `infrastructure` may not import each other. `application`
depends on ports declared in `domain`; `infrastructure` implements them; only
`composition` knows both. A violation fails the build with a named contract.

### Where the design is written down

| Document | Covers |
|---|---|
| [docs/architecture/overview.md](docs/architecture/overview.md) | **Start here** — the map |
| [system.md](docs/architecture/system.md) | Topology, environments, testing, observability, failure behaviour |
| [backend.md](docs/architecture/backend.md) | Layering, API contract, database, storage, background work |
| [frontend.md](docs/architecture/frontend.md) | Feature organisation, data fetching, design system, accessibility |
| [data-flow.md](docs/architecture/data-flow.md) | Upload, processing state machine, search, ask, deletion |
| [ai-pipeline.md](docs/architecture/ai-pipeline.md) | Provider ports, retrieval, context, citation binding |
| [security.md](docs/architecture/security.md) | Threat model, authn, authz, hostile input, known gaps |
| [failure-modes.md](docs/operations/failure-modes.md) | What each dependency failure does, what keeps working, and the runbook for each |
| [docs/decisions/](docs/decisions/README.md) | 24 ADRs — why each choice was made, and what was rejected |

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| [Docker Desktop](https://docs.docker.com/desktop/) | Engine 24+ | Must be **running** — PostgreSQL, Redis, and MinIO all come from Compose. |
| [uv](https://docs.astral.sh/uv/) | 0.12+ | Manages the Python environment and lockfile. |
| Python | 3.11 or 3.12 | `uv` provisions one if absent. |
| Node.js | 20.11+ | Frontend build and the task runner. `.nvmrc` pins the tested version. |

No `make` required. Every task is an npm script.

---

## Local setup

```bash
git clone <repository-url> orbit
cd orbit
```

**1. Configuration.** `.env` is gitignored and must never be committed.

```bash
cp .env.example .env
```

Replace every placeholder. Generate the signing key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Compose refuses to start if `POSTGRES_PASSWORD`, `MINIO_ROOT_USER`, or
`MINIO_ROOT_PASSWORD` are unset, so a half-configured environment fails
immediately rather than booting with defaults. Full variable reference:
[docs/operations/environment.md](docs/operations/environment.md).

**2. Install dependencies.**

```bash
npm run setup
```

**3. Start infrastructure.**

```bash
npm run infra:up
```

Starts PostgreSQL (with `vector`, `pg_trgm`, and `uuid-ossp` created), Redis with
append-only persistence, and MinIO, then provisions the documents bucket with
anonymous access denied. The command waits for health checks, so it returns only
once the stack is genuinely usable.

**4. Verify.**

```bash
npm run verify
```

**5. Apply migrations**, then run the processes, each in its own terminal:

```bash
npm run backend:migrate
```

```bash
npm run dev:api
```

```bash
npm run dev:worker
```

```bash
npm run dev:beat
```

```bash
npm run dev:web
```

Open **http://localhost:3000** and create an account. *Account → About* shows the
API's build identity, which distinguishes a broken proxy from a stopped backend.

The frontend's API types are **generated** from the backend's OpenAPI schema. After
changing a route or schema, run `npm run web:api-types` and commit the result; CI fails
if it is stale.

| Surface | URL |
|---|---|
| Application | http://localhost:3000 |
| API docs (non-production) | http://localhost:8000/docs |
| Liveness | http://localhost:8000/healthz |
| Readiness | http://localhost:8000/readyz |
| MinIO console | http://localhost:9001 |

### Everything runs from one origin

The browser only ever talks to `localhost:3000`; `/api/*` is proxied to the
backend. This is load-bearing rather than cosmetic — it is what allows both auth
tokens to be `HttpOnly` cookies that JavaScript cannot read
([ADR-0009](docs/decisions/0009-single-origin-cookie-transport.md)). Pointing the
frontend at an absolute API origin is a bug, not a configuration choice.

### Running the whole stack in Docker

```bash
npm run stack:up
```

Builds and runs the API, worker, and frontend as containers alongside the
infrastructure. Slower to iterate on than the host processes above, and the
closest local match to production.

### Watching a document process

Uploading returns as soon as the bytes are stored; processing happens in the
worker. Poll the document's processing status:

```
GET /api/v1/workspaces/{workspace_id}/documents/{document_id}/processing
```

It reports `pending`, `processing`, `ready`, or `failed` with a reason written
for the uploader, plus every attempt made. A failed document can be retried with
`POST …/documents/{document_id}/reprocess`. How the pipeline survives dead
workers, lost messages, and outages:
[ADR-0019](docs/decisions/0019-asynchronous-processing-pipeline.md).

### No AI credentials required

ORBIT ships a deterministic, offline AI provider and uses it by default
(`ORBIT_AI_PROVIDER=fake`). Upload, processing, retrieval, chat, and citations
all work with no API key and no network. Answer *quality* requires a real
provider; correctness does not
([ADR-0007](docs/decisions/0007-ai-provider-ports.md)).

To use a real model, set `ORBIT_AI_PROVIDER=openai`, `ORBIT_OPENAI_API_KEY`, and
`ORBIT_OPENAI_BASE_URL`. Any OpenAI-compatible endpoint works: OpenAI itself, or
Google Gemini with a Google AI Studio key (the settings are in `.env.example`
and [environment.md](docs/operations/environment.md#using-google-gemini)).

---

## Commands

| Command | Purpose |
|---|---|
| `npm run verify` | **The gate.** Backend format, lint, types, architecture contracts, tests; frontend format, lint, types, build. |
| `npm run fix` | Apply formatting and auto-fixable lint across both halves. |
| `npm run setup` | Install backend and frontend dependencies. |
| `npm run dev:api` / `dev:worker` / `dev:web` | Run a process on the host with reload. |
| `npm run dev:beat` | Scheduler for the processing recovery sweep ([worker.md](docs/operations/worker.md)). |
| `npm run worker:recover` | Run one recovery sweep now: abandoned jobs and lost messages. |
| `npm run infra:up` / `infra:down` / `infra:reset` | Local infrastructure. |
| `npm run stack:up` / `stack:down` | Full stack in Docker, including the app services. |
| `npm run infra:psql` | `psql` against the development database. |
| `npm run backend:test` | Unit and API tests (no infrastructure needed). |
| `npm run backend:test:integration` | Integration tests (requires `infra:up`). |
| `npm run backend:arch` | Architecture contracts only. |
| `npm run backend:migrate` | Apply migrations. |

CI runs the same script names, so a gate that passes locally and fails in CI
indicates an environment difference rather than a different command.

---

## Repository layout

```
backend/                 Python package `orbit` — API and worker entrypoints
  src/orbit/
    api/                 HTTP only: routing, request/response schemas, middleware
    application/         Use cases; transaction boundaries
    domain/              Entities, pure business logic, ports (Protocols)
    infrastructure/      Adapters: db, storage, queue, ai, parsing, chunking
    core/                Configuration, logging, security primitives
    composition/         Wiring root — the only layer that knows every other
  alembic/               Migrations
  tests/                 unit / integration / api
web/                     Next.js frontend, feature-oriented
e2e/                     Playwright end-to-end journeys
docker/                  Container definitions and database bootstrap
docs/                    Architecture, API, database, decisions, operations, security
```

---

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| **M0** | Foundation: repository, tooling, contracts, Compose stack, CI, ADRs | ✅ Complete |
| **A1** | Architecture discovery: 6 architecture documents, 16 ADRs | ✅ Complete |
| **M1** | Foundation: config + validation, structured logging, request IDs, error envelope, health probes, DI container, Alembic, frontend scaffold, Docker images | ✅ Complete |
| **M2** | Identity and tenancy: users, workspaces, roles, token rotation, authorization | ✅ Implemented |
| **M3** | Documents: object storage, validated upload, listing, versions, download, folders, tags, archive, text viewer | ✅ Implemented — awaiting review (ADR-0023). Folders and tags are now functional end to end; no E2E suite yet |
| **M4** | Async pipeline: durable jobs, leases, retries, parsing, chunking, embedding, recovery | ✅ Implemented — awaiting review |
| **M5** | Retrieval: hybrid search, RRF fusion, reranking port, benchmarks | ✅ Implemented — awaiting review (ADR-0020, ADR-0021). Semantic quality not yet measured with a real embedding provider |
| **M6** | RAG chat: context construction, grounded answers, bound citations | Not started |
| **M7** | Frontend: full application surface, accessibility, E2E coverage | ✅ Implemented — awaiting review. Every screen verified against the running backend and audited with axe in both themes; **no automated E2E suite yet**, and lists are not virtualised ([known gaps](docs/architecture/frontend.md#10-known-gaps)) |
| **M8** | Hardening: rate limiting, audit log, metrics, operations docs, engineering audit | Not started |

---

## Security

Report vulnerabilities privately; see [`docs/security/`](docs/security/).
Never commit `.env`, credentials, or private keys — CI fails the build if a
dotenv file becomes tracked, and scans full branch history for secrets.
