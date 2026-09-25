# Contributing to ORBIT

## Setup

Prerequisites: **Docker Desktop (running)**, **Node 20.11+**, **Python 3.11 or 3.12**,
and **[uv](https://docs.astral.sh/uv/)**. No `make` is required — every task is
an npm script.

```bash
git clone <repository-url> orbit
cd orbit
cp .env.example .env      # then replace every placeholder — see below
npm run setup             # installs backend and frontend dependencies
npm run infra:up          # PostgreSQL, Redis, MinIO
npm run verify            # the full gate
```

Generate the signing key rather than inventing one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Full variable reference: [docs/operations/environment.md](docs/operations/environment.md).

## Running it

Three processes. Run each in its own terminal:

```bash
npm run dev:api
```

```bash
npm run dev:worker
```

```bash
npm run dev:web
```

Then open **http://localhost:3000**. The landing page reports whether the
frontend can reach the API, which distinguishes a broken proxy from a stopped
backend — two failures that look identical in a browser's network tab.

Everything is served from **one origin**. The browser only ever talks to
`localhost:3000`; `/api/*` is proxied to the backend by a Next.js rewrite in
development and by a reverse proxy in production. This is not cosmetic — it is
what allows both auth tokens to be `HttpOnly` cookies
([ADR-0009](docs/decisions/0009-single-origin-cookie-transport.md)). **Never
point the frontend at an absolute API origin.**

### No AI credentials needed

`ORBIT_AI_PROVIDER=fake` is the default and a fully supported mode. Upload,
processing, retrieval, chat, and citations all work offline with no API key.
Answer *quality* needs a real provider; correctness does not
([ADR-0007](docs/decisions/0007-ai-provider-ports.md)).

## The verification gate

`npm run verify` is the contract. CI runs the same script names, so a gate that
passes locally and fails in CI means an environment difference, not a different
command.

| Step | Command |
|---|---|
| Backend format | `npm run backend:format:check` |
| Backend lint | `npm run backend:lint:check` |
| Backend types (mypy strict) | `npm run backend:typecheck` |
| **Architecture contracts** | `npm run backend:arch` |
| Backend tests | `npm run backend:test` |
| Frontend format | `npm run web:format:check` |
| Frontend lint | `npm run web:lint` |
| Frontend types | `npm run web:typecheck` |
| Frontend build | `npm run web:build` |

`npm run fix` applies formatting and auto-fixable lint across both halves.

Integration tests need infrastructure running:

```bash
npm run infra:up && npm run backend:test:integration
```

## Architectural rules that are enforced, not suggested

`npm run backend:arch` fails the build if any of these is violated:

```
composition → api → { application, infrastructure } → domain → core
```

- **`application` and `infrastructure` cannot import each other.** Use cases
  depend on ports declared in `domain`; adapters implement them.
- **`api` cannot import `infrastructure` at all.** This is what makes "no
  database queries in route handlers" structurally impossible rather than a
  review convention.
- **`domain` imports nothing but `core`.** No framework, no driver, no HTTP.

When a contract fails, the fix is almost never to edit `.importlinter`. It is to
introduce a port. If you genuinely believe the contract is wrong, change it in a
commit of its own with the reasoning in the message.

Read [docs/architecture/overview.md](docs/architecture/overview.md) first, then
the ADR for whatever you are touching.

## Before adding a dependency

Section 23 of the engineering brief, in practice. Answer these in the pull
request description:

1. Can the existing stack already do this?
2. Is it maintained, and what is its transitive footprint?
3. What does it cost at runtime — bundle size, cold start, memory?
4. What is the licence?
5. What is the exit path if it is abandoned?

Two libraries solving the same problem is a defect, not a preference. If a
dependency is added for a future milestone rather than for code that exists now,
it is premature — add it with the code that uses it.

## Code standards

**Python.** Type hints everywhere; `mypy --strict` passes; ruff selects 20 rule
groups including `S` (security), `ASYNC` (blocking calls inside `async def`),
and `DTZ` (no naive datetimes). Never silence a rule without a `noqa` that
carries a reason.

**TypeScript.** `strict` plus `noUncheckedIndexedAccess`, `noUnusedLocals`, and
`noImplicitReturns`. `any` is a lint warning, not a habit. Runtime validation at
external boundaries only — the error envelope and form input — because types
generated from the API are already the source of truth for responses.

**Comments explain *why*.** `// set loading to true` is noise. A comment
earns its place by recording a business rule, a security decision, a performance
trade-off, or a non-obvious workaround. If a reviewer would ask "why is this
like that?", answer it in the code.

**Errors are never swallowed.** Every `except` either converts to a domain
error, logs with context and re-raises, or handles the case with a comment
saying why that is correct.

## Tests

| Layer | Location | Needs |
|---|---|---|
| Unit | `backend/tests/unit` | nothing |
| Integration | `backend/tests/integration` | `npm run infra:up` |
| API | `backend/tests/api` | nothing (in-process client) |
| E2E | `e2e/` | full stack (from M7) |

Standing rules:

- **Failure cases are mandatory.** A resource with only happy-path coverage is
  incomplete.
- **Every tenant-scoped resource needs a cross-tenant denial test.**
- **The suite runs with no AI credentials and no network.** CI has no provider
  keys, which is what keeps this true.
- **Tests are never deleted or skipped to make a build pass.** If a test is
  wrong, fix the test and say why in the commit.

Bugs get a regression test that fails before the fix. Two of the defects found
while building this foundation — a logging pipeline that raised only when
logging an error, and a CORS variable that made the app unstartable from its own
documented `.env` — were silent on the happy path. That is the normal shape of a
real bug.

## Migrations

```bash
npm run backend:migration -- "add documents table"   # autogenerate
npm run backend:migrate                              # apply
```

**Always read the generated migration before committing it.** Autogenerate
misses server defaults, constraint renames, and index changes, and will happily
propose dropping a table it does not know about.

Migrations are **expand/contract**. Every migration must be backward-compatible
with the release before it, because during a rolling deploy both versions run
against the same schema. Never rename or drop a column in the same release that
stops using it.

## Commits and pull requests

- One logical change per commit. A refactor and a behaviour change belong in
  separate commits.
- Never disable lint, type checking, or a test to make a build pass. If
  something must be suppressed, the suppression carries a written reason.
- Never commit `.env`, credentials, or keys. CI fails the build if a dotenv file
  becomes tracked and scans full branch history for secrets.
- A feature is done when it is implemented, typed, validated, authorized,
  tested, documented, and the whole gate passes — not when it compiles.

## Security

Never commit secrets. Report vulnerabilities privately rather than in a public
issue; see [docs/security/](docs/security/README.md). Treat every uploaded file
and every LLM response as untrusted input — both are attacker-influenced, and
both are handled as data rather than as instructions.
