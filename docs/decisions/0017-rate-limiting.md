# 0017 — Rate limiting for credential and account-lifecycle endpoints

- **Status:** Accepted
- **Date:** 2026-09-10
- **Relates to:** [ADR-0003](0003-authentication.md) (authentication),
  [ADR-0015](0015-observability-strategy.md) (what may be logged)

## Context

Brute-forcing a password is not a clever attack; it is a patient one. Argon2id
makes a single guess expensive — roughly 50 ms of CPU and 64 MiB of memory —
but expensive-per-guess is not the same as bounded-in-total. An attacker with a
week and a modest botnet does not care how slow one attempt is; they care how
many attempts they are allowed.

Three endpoints are exposed to unauthenticated volume:

| Endpoint | What an attacker gains |
|---|---|
| `POST /auth/login` | Credential guessing, credential stuffing, account enumeration by timing |
| `POST /auth/password-reset` | An outbound email to any address, on demand |
| `POST /auth/verify-email/resend` | The same, authenticated but still unmetered |

The last two matter for a reason that is easy to miss: each accepted request
costs an email to an address the *caller* chose. An unmetered reset endpoint is
a free mail-bombing service that arrives with ORBIT's sending reputation
attached to every message.

## Decision

### Two independent limits, both applied

Every credential check is guarded by a **per-account** limit and a **per-client-
address** limit. Either one tripping rejects the request.

- **Per account** stops one host spraying many passwords at one victim.
- **Per address** stops one host spraying one password at many victims
  (credential stuffing), which a per-account limit never sees — each account
  gets exactly one attempt, so no account counter ever moves.

Neither is sufficient alone. A per-IP limit is trivially defeated by a botnet;
a per-account limit is trivially defeated by rotating targets. Together they
force an attacker to be both distributed *and* slow, which is the property
worth buying.

Both counters are recorded even when the first already rejects. Skipping the
second would let an attacker who has exhausted the cheap per-account budget
continue consuming the per-IP budget for free.

### Fixed window, in Redis

A fixed window, not a sliding one. The trade is explicit: a fixed window
permits up to `2 × limit` attempts across a window boundary. That burst is
acceptable because the limits are small in absolute terms, and it buys an
implementation that is one round trip, O(1) memory per key, and obviously
correct — where a sliding log stores every attempt timestamp and a sliding
window counter needs two keys plus interpolation.

`INCR` then `EXPIRE` in one pipeline, with `EXPIRE` set unconditionally rather
than only on creation. Setting it every time costs nothing and removes the
failure mode where a crash between the two commands leaves a counter with no
TTL — which would lock an account out permanently.

Redis, not PostgreSQL: a brute-force run is high-volume, worthless, and
short-lived, which is precisely the data that must not compete for durable
write throughput with real traffic.

### Keys are hashed

The per-account key is `sha256(identity)`, not the address itself. Redis keys
turn up in `SLOWLOG`, `MONITOR`, keyspace dumps, and any debugging session, and
none of those should become a list of every address that has attempted to sign
in. The hash is stable, so the limit still tracks the account; it simply does
not spell it out.

### The limiter fails **open**

If Redis is unreachable, `check` returns "allowed" and logs
`ratelimit.backend_unavailable` at ERROR.

This is the uncomfortable half of the decision and it is deliberate. Failing
closed would turn a Redis outage into a **total authentication outage** —
nobody can sign in, including the operators trying to fix it. Failing open
degrades brute-force protection for the duration of the outage, which is a
smaller and bounded harm.

It is only defensible because it is *visible*: it logs at ERROR, `/readyz`
already reports Redis down, and Argon2id remains the cost that makes each
attempt expensive regardless. A silent fail-open would not be acceptable.

### Checked before the password is verified

The limit is evaluated before any database read and before Argon2id runs. If
it ran after verification, every rejected attempt would still burn the most
expensive operation in the process — turning the brute-force defence into the
amplifier for a CPU-exhaustion attack. A test pins this ordering.

### Cleared on success, per account only

A successful login clears the *account* counter, so a user who mistypes twice
before succeeding is not left one typo from a lockout for the rest of the
window. The per-IP counter is never cleared: one valid account must not become
a way to launder unlimited guesses against every other account from the same
host.

### Policy lives in the application layer

The guard sits in `application/auth/rate_limits.py`, not in a route decorator
or middleware, for two reasons that are not stylistic:

1. The per-account key is a domain value only knowable after the request body
   is parsed. Middleware that has not parsed the body cannot compute it.
2. A limit expressed in a decorator is a limit a new caller of the same use
   case silently skips. Expressed in the use case, every path into `LoginUser`
   is limited — including one added next year by someone who never read this
   ADR.

## Consequences

**Accepted costs**

- A user who exhausts the budget is locked out for the remainder of the window,
  *including with the correct password*. This is intended: a limit that lets
  correct passwords through stops only incorrect guesses, which is precisely
  the guess an attacker eventually makes.
- The `429` on password reset does reveal that a given address was asked about
  recently — but only to someone already submitting that address, and only
  within the window. That is a materially smaller disclosure than an unmetered
  send, and the alternative (silently dropping over-limit requests) leaves a
  real user with no signal at all.
- A Redis outage removes the protection until it is restored.

**What this does not cover**

- No global request-rate limit. That belongs at the reverse proxy or CDN, which
  can shed load before it reaches an application worker at all.
- No progressive backoff or account lockout escalation. A fixed window is the
  simpler mechanism and the one whose failure modes are fully understood; if
  operational data shows it insufficient, the `RateLimiter` port is where a
  smarter implementation goes without touching a use case.
- No CAPTCHA. It is a product decision with accessibility consequences, not a
  backend one.

## Alternatives considered

| Option | Why not |
|---|---|
| Sliding window log | Exact, but stores every attempt timestamp per key — unbounded memory under exactly the attack it defends against. |
| Sliding window counter | Removes the boundary burst at the cost of two keys and interpolation. The burst is not the binding constraint here. |
| Token bucket | Better for smoothing sustained API traffic; more state and more tuning than a login counter needs. |
| PostgreSQL counters | Durable, and therefore wrong: brute-force traffic would compete for write throughput with real work. |
| Fail closed on Redis loss | Converts a cache outage into an authentication outage. Rejected. |
| Middleware or decorator | Cannot see the parsed body; silently skipped by any new caller of the use case. |

## Migration path

`RateLimiter` is a port (`domain/ports/rate_limiter.py`) with two methods. A
sliding-window or token-bucket implementation, or one backed by a managed
service, is a new adapter and a container binding — no use case changes. The
`RateLimitDecision` already carries `retry_after_seconds`, so a smarter
implementation can return a more accurate delay without a contract change.
