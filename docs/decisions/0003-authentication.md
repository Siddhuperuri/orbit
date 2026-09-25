# 0003 — Argon2id passwords, short-lived access tokens, rotating refresh tokens

- **Status:** Accepted; token *transport* amended by [ADR-0009](0009-single-origin-cookie-transport.md)
- **Date:** 2026-09-09

> **Amendment.** ADR-0009 moves the access token from client memory into an
> `HttpOnly` cookie on a single origin. The Argon2id, TTL, rotation, and
> reuse-detection decisions below are unchanged; only the transport described
> under "Access token" is superseded.

## Context

ORBIT stores private document corpora. A session compromise exposes them, so the
session design has to assume that tokens will leak — through a browser
extension, a shared machine, an XSS bug not yet found, or a log that captured
more than it should.

Two mechanisms are in tension: stateless JWTs are cheap to verify but cannot be
revoked, while opaque session tokens are revocable but require a lookup on every
request.

## Decision

**Passwords: Argon2id** via `argon2-cffi`. Memory-hard, resistant to GPU and
ASIC attack in a way PBKDF2 and bcrypt are not. Parameters are configuration,
and the stored hash encodes them, so cost can be raised over time and existing
hashes are transparently re-hashed on the next successful login.

**Access token: JWT, HS256, 15-minute TTL**, held in memory by the client only.
Stateless verification, so the hot path needs no database round trip. The short
TTL is what bounds the damage of a leak — an unrevocable credential is
acceptable only when it expires quickly.

**Refresh token: opaque**, 256 bits from `secrets.token_urlsafe`, stored
**hashed** (SHA-256) in Postgres. Delivered as
`HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`. Never readable by
JavaScript, never in `localStorage`.

**Rotation with reuse detection.** Every refresh issues a new token and marks
the presented one consumed. Tokens belong to a *family* originating at login.
Presenting an already-consumed token is only possible if it was captured, so
that event **revokes the entire family** and forces re-authentication. This is
the mechanism that turns a stolen refresh token from indefinite access into a
detectable, self-limiting incident.

## Alternatives considered

**Long-lived JWT with no refresh.** Rejected: no revocation, and logout becomes
a client-side fiction.

**JWT stored in `localStorage`.** Rejected: readable by any script on the
origin, which makes every XSS bug a full account takeover.

**Server-side sessions only** (opaque token, database lookup per request). The
simplest correct design, and genuinely defensible. Rejected because every
authenticated request would take a database round trip before doing any work;
the hybrid obtains the same revocation properties at the session boundary, at
the cost of a 15-minute staleness window.

**bcrypt.** Rejected: 72-byte input truncation and no memory hardness.

**Delegating to an external identity provider** (Auth0, Clerk, Cognito). A
serious option that removes this entire surface. Rejected for now: it introduces
a hard external dependency and per-user cost, for a mechanism that is
well-understood and testable. Revisit when SSO/SAML is required — that is the
point at which building it here genuinely stops being sensible.

## Consequences

- A revoked or logged-out session can still make authenticated requests for up
  to 15 minutes. Accepted and documented. Operations that must take effect
  immediately — password change, membership removal, forced logout — additionally
  bump a per-user token epoch that access-token validation checks.
- Refresh requires a database write, so it is rate-limited per user and per IP.
- Reuse detection produces false positives when a client races two refreshes
  (double-submit, offline retry). The user is logged out rather than the event
  being ignored: a spurious re-login is a far cheaper failure than a silently
  tolerated stolen token.
- Refresh token rows need periodic pruning; expired families are removed by a
  scheduled task.
- The secret key is a single point of compromise for access tokens. It comes
  from configuration, never from source, and rotating it invalidates all
  outstanding access tokens — which is the intended emergency lever.
