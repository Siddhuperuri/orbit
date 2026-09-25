# 0016 — TanStack Query owns application data; Server Components own the shell

- **Status:** Accepted
- **Date:** 2026-09-09

## Context

Next.js App Router offers two data-fetching models, and choosing "both, wherever
it feels natural" is how a frontend ends up with two competing caches, two
loading conventions, and mutations that update one but not the other.

ORBIT's data has properties that decide this:

- **It changes without the user acting.** A document moves `PENDING →
  PROCESSING → READY` while they watch. That requires polling and live cache
  updates.
- **It is mutated constantly.** Upload, rename, tag, move, delete. Each must
  update several views at once.
- **It streams.** Chat answers arrive token by token, followed by citations.
- **It is private.** Every page is behind authentication, so there is **no SEO
  benefit** — the single strongest argument for server rendering does not apply.
- **It is read repeatedly across views.** The same document appears in a list, a
  detail pane, and a citation. One cache should serve all three.

## Decision

**TanStack Query is the single owner of all application data.** React Server
Components render the shell: layouts, navigation, static chrome, and the
authenticated-route boundary. Server Components do not fetch documents, search
results, or conversations.

The boundary is stated as a rule, not a feeling:

> If the data can change while the user is looking at it, or the user can
> change it, it belongs to TanStack Query.

Everything that follows depends on that single ownership:

- **Query keys are hierarchical and centrally declared** —
  `['workspaces', id, 'documents', filters]` — so an upload can invalidate
  exactly the affected lists rather than resetting the cache.
- **Polling is scoped and self-terminating.** Documents in a non-terminal state
  are polled with backoff; polling stops when every document reaches `READY` or
  `FAILED`. A blanket interval on the whole list is a permanent background load
  on the API for every open tab.
- **Mutations are optimistic where the outcome is predictable** (rename,
  tagging) and pessimistic where it is not (upload, delete), with rollback on
  failure.
- **Streaming chat bypasses the query cache** during generation and writes the
  finished message into it on completion. `EventSource` cannot issue `POST`, so
  streaming uses `fetch` with a `ReadableStream` reader.

### Types come from the API, not from a parallel definition

TypeScript types are **generated from the FastAPI OpenAPI schema**
(`openapi-typescript`) and committed. Hand-writing an interface that mirrors a
Pydantic model creates two sources of truth that drift silently; generation makes
a backend contract change a compile error in CI.

Zod is used where runtime validation genuinely earns its cost — **form input**
(with React Hook Form) and the **error envelope** — not as a hand-maintained
mirror of every response schema. Validating a response the compiler already
describes, from an API in the same repository, is ceremony.

### There is no BFF layer

Because ADR-0009 puts the browser and API on one origin with `HttpOnly` cookies,
the browser calls `/api/v1/...` directly. Next.js Route Handlers are not used to
proxy authenticated requests: there is no secret for a server layer to hold, and
adding one would put the Node process in the path of every upload and every
streamed token.

## Alternatives considered

**Server Components as the primary fetch layer, with Server Actions for
mutations.** The Next.js-idiomatic answer. Rejected: refreshing a Server
Component tree to reflect one document's status change re-renders and re-fetches
far more than changed; there is no client cache to serve the same document to
three views; and polling means repeatedly re-rendering server trees. The model
suits content-driven pages, which ORBIT does not have.

**SWR.** Very close in capability and lighter. Rejected narrowly: TanStack
Query's mutation lifecycle, cache invalidation, and devtools are materially
stronger for an app whose defining characteristic is frequent mutation. The
brief also specifies it, and no better production argument was found to deviate.

**Redux Toolkit / RTK Query.** Rejected: ORBIT has very little genuine *client*
state. Almost everything is server state with a cache, and a global store adds a
layer that mostly restates the cache.

**Plain `fetch` in `useEffect`.** Rejected: re-implements caching,
deduplication, retries, and race-condition handling, badly.

**WebSockets for chat streaming.** Rejected: a stateful bidirectional connection
to solve a unidirectional problem, and it complicates load balancing and
authentication. Server-Sent Events over the existing HTTP path is sufficient and
reconnects natively.

## Consequences

- Query keys are a shared, versioned contract. They live in one module per
  feature, never inline at call sites, because an inline key that differs by one
  character is a cache miss nobody notices.
- Because auth is a cookie, a `401` can happen on any request. A single global
  handler in the query client responds to the `AUTHENTICATION_REQUIRED` code
  (ADR-0014) by clearing the cache and redirecting to login — implemented once,
  not per query.
- Type generation is a build step. Drift between backend and frontend is caught
  in CI, and regenerating is part of the definition of done for any API change.
- Initial page render fetches on the client, so authenticated pages need real
  skeleton states. This is required by section 20 of the brief regardless.
- ~~Server Components still handle the auth gate, so an unauthenticated user is
  redirected before any application JavaScript loads.~~ **Wrong; see the amendment
  below.**

## Amendment (M7): the authentication gate is client-side

**Date:** 2026-09-19

Building the shell showed the last consequence above cannot hold. The auth cookies are
scoped `Path=/api` and `Path=/api/v1/auth` (ADR-0009, `api/cookies.py`), so a browser
attaches neither to a *page* request. A Server Component (or Next middleware) rendering
`/workspaces/…` therefore cannot see whether anyone is signed in, and "forward the
incoming cookie header" from ADR-0009 forwards nothing.

The gate is a client component (`AuthGate`) that resolves `GET /auth/me` and redirects
to `/auth/login?next=…` on failure. This is safe because the property that mattered was
never *where the redirect happens*: every page's HTML is chrome only, all data is
fetched afterwards, and the API authorises each request. What is lost is the redirect
before any application JavaScript loads — an experience difference, not a security one.

Widening the cookie `Path` to `/` to enable a server-side gate would send credentials on
every static-asset request and is not recommended. A non-secret "session hint" cookie
would allow an early redirect; it was not added, because a stale hint is a worse failure
than a brief loading state.

Also confirmed against the running backend: **the typed client works as decided** —
generated types compile-check every call, and the SSE stream (`POST`, `fetch` +
`ReadableStream`) is read incrementally through the development proxy.
