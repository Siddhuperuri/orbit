# 0009 — Single-origin deployment and cookie-based token transport

- **Status:** Accepted
- **Date:** 2026-09-09
- **Amends:** [ADR-0003](0003-authentication.md) — revises token *transport* only.
  The Argon2id, TTL, rotation, and reuse-detection decisions in ADR-0003 stand
  unchanged.

## Context

ADR-0003 specified a refresh token in an `HttpOnly; SameSite=Lax` cookie and an
access token "held in memory by the client only", sent as an `Authorization`
header. The M0 scaffold then set `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`
while the frontend runs on port 3000.

Those two decisions are in tension, and the architecture discovery pass caught
it. A separate-origin frontend forces CORS with credentials on every request,
requires the cookie to be widened with an explicit `Domain` attribute in
production (`.orbit.example`, shared by every subdomain), and adds a preflight
round trip to each mutation.

Resolving the tension exposed a second, larger question. If the browser and API
share an origin, is an in-memory access token still the right transport?

The threat that decides it is **XSS**, which is the realistic compromise path
for a document platform that renders user-supplied content:

| | In-memory access token | `HttpOnly` cookie |
|---|---|---|
| XSS reads the credential | **Yes** — it is a JS variable | No |
| XSS exfiltrates it for offline use | **Yes** — usable from anywhere for 15 min, and `/refresh` mints more | No |
| XSS acts as the user | Yes | Yes, but only from the victim's browser, while the page is open |
| Credential survives the tab closing | Yes, at the attacker's server | No |

Both lose to XSS. Only one leaks a portable credential.

## Decision

**One origin.** The browser sees a single origin. `/api/*` reaches the backend;
everything else is served by the frontend.

- **Production:** a reverse proxy / ingress in front of both, terminating TLS.
- **Development:** Next.js `rewrites` proxies `/api/*` to `http://localhost:8000`.

The *contract* is "one origin, `/api/*` routes to the backend"; dev and
production satisfy it with different machinery.

**Both tokens travel as cookies, for browser clients:**

| Cookie | Attributes |
|---|---|
| `orbit_access` | `HttpOnly; Secure; SameSite=Lax; Path=/api` |
| `orbit_refresh` | `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth` |

No `Domain` attribute, so both are host-only — the narrowest possible scope. No
token is ever readable by JavaScript, and there is no refresh interceptor in the
client.

**Programmatic clients** (future CLI, integrations) send
`Authorization: Bearer <access token>`. The authentication dependency reads the
cookie first, then the header. Accepting both does not weaken CSRF posture,
because a cross-site attacker cannot set an `Authorization` header without CORS
consent.

### CSRF

`SameSite=Lax` is the primary control: the browser does not attach these cookies
to cross-site `POST`, `PUT`, `PATCH`, or `DELETE`. Two rules make that
sufficient, and both are enforced rather than assumed:

1. **`GET`, `HEAD`, and `OPTIONS` never mutate state.** Lax *does* send cookies
   on top-level cross-site navigation, so a mutating `GET` would be exploitable.
2. **Mutating requests are rejected unless `Origin` matches the expected host.**
   Defence in depth for older browsers and for any future `SameSite=None` need.

In production, CORS is configured with an **empty** allow-list. Nothing is
cross-origin, so nothing needs permission. `ORBIT_CORS_ALLOWED_ORIGINS` exists
for development and for a deliberately-approved future integration.

## Alternatives considered

**Separate origins with credentialed CORS** (the M0 assumption). Rejected:
preflight on every mutation, a `Domain`-scoped cookie shared with every
subdomain, and CORS misconfiguration becomes a live security control rather than
a non-issue.

**Keep the access token in memory, refresh in a cookie** (ADR-0003 as written).
Rejected: leaks a portable credential to XSS, and requires a client-side refresh
interceptor whose concurrent-request races are a recurring source of bugs.

**Access token in `localStorage`.** Rejected in ADR-0003 and still rejected —
strictly worse than in-memory.

**A BFF that holds tokens server-side in Next.js and exposes only its own
session cookie.** Genuinely strong, and the direction to take if ORBIT ever
needs to hold third-party OAuth tokens. Rejected now: it puts the Node process
in the data path for every request including uploads and SSE, doubles the
session surface, and buys little over `HttpOnly` cookies on a single origin.

**`SameSite=Strict`.** Rejected: it breaks inbound links — following a shared
document URL from email would land the user on a logged-out page. `Lax` plus
the two rules above gives equivalent protection for mutations without that cost.

## Consequences

- **`.env.example` changes:** `NEXT_PUBLIC_API_BASE_URL` becomes the relative
  path `/api`. A frontend that hardcodes an absolute API origin is a bug.
- **Uploads and SSE traverse the dev proxy.** Next.js `rewrites` must stream
  rather than buffer, or chat streaming and 50 MiB uploads break in development
  while working in production. **This is a verification item for M6**, not an
  assumption.
- **Logout must clear cookies server-side** with matching attributes; the client
  cannot delete an `HttpOnly` cookie.
- Server-side rendering can call the API by forwarding the incoming cookie
  header, so a Server Component is not locked out of authenticated data.
- The `Origin` check must tolerate a missing header (some non-browser clients
  omit it) while still rejecting a mismatched one. Missing plus a `Bearer` token
  is acceptable; missing plus a cookie is not.
- CSRF protection now depends on the "`GET` never mutates" rule. That rule is
  tested, not merely documented: a test asserts no route registered for a safe
  method is bound to a mutating use case.

## Amendment (M7): what running the frontend against this design found

**Date:** 2026-09-19

Four things in this ADR were wrong or incomplete once a real browser talked to the real
API through the development proxy.

**1. The refresh interceptor is required.** The Decision says "there is no refresh
interceptor in the client", and the alternatives section cites interceptor races as a
reason to reject the in-memory token. With a 15-minute access token, every request
after expiry answers 401 and the client must call `/auth/refresh`; rotating refresh
tokens with reuse detection (ADR-0003) mean two concurrent refreshes revoke the whole
session. The client therefore does implement one, built around the race rather than
ignoring it: a single in-flight refresh per tab, a Web Lock plus a timestamp across
tabs, and replay of the failed request. Verified live with a 60-second token: two
concurrent requests both received 401, exactly **one** `POST /auth/refresh` followed,
and both were replayed successfully. The cookie transport is still right; the
"no interceptor" claim is not.

**2. The `Origin` check rejected every authenticated write in development.** A Next.js
rewrite cannot preserve `Host` (it sets `changeOrigin: true`), so the API saw
`Host: localhost:8000` against the browser's `Origin: http://localhost:3000` and answered
`CSRF_ORIGIN_MISMATCH` for every cookie-authenticated `POST`/`PATCH`/`DELETE`. Sign-in
worked only because it carries no cookie yet. The middleware now also accepts an `Origin`
that exactly matches an entry in `ORBIT_CORS_ALLOWED_ORIGINS` — the operator's
explicit allow-list, which configuration validation already forbids from containing `*`
and from being non-empty in production, so production behaviour is unchanged. Matching
is by whole `scheme://host:port`, never by host or prefix.

**3. Streaming through the development proxy works.** The verification item from M6 is
closed: server-sent events arrive incrementally (checked with a stand-in emitting one
event per 500 ms: arrivals at 524, 1023, 1536, 2035, 2549, 3064 ms) with no buffering
and no compression.

**4. Large uploads through the development proxy did not work, and now do.** Next's
rewrite proxy defaults to a **10 MiB request-body cap** and a **30-second timeout**. A
20 MiB upload was truncated at 10 MiB and the connection reset — surfacing as a bare
500 with nothing in the API log, because the API never received the request. Both are
now set explicitly in `next.config.ts` (`experimental.proxyClientMaxBodySize` to the
backend's 1 GiB ceiling, so the *backend's* `UPLOAD_TOO_LARGE` is the limit that applies;
`proxyTimeout` to ten minutes). The same 20 MiB upload then completed in 1.1 s.

**For production:** these limits belong to whatever reverse proxy carries `/api/*`, which
must be configured to match — a request-body limit at least the configured
`ORBIT_MAX_UPLOAD_BYTES`, an idle timeout longer than the slowest answer, and response
buffering off for `text/event-stream`. That is deployment configuration, unverified here.
