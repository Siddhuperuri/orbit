# Security review — authentication and authorization

- **Date:** 2026-09-10
- **Scope:** M4 — the authentication and authorization module
- **Reviewer:** implementing engineer (self-review; not an independent assessment)
- **Verdict:** the module is **complete as designed and unverified against real
  infrastructure.** See [§6](#6-what-is-not-verified) before treating any part
  of this as production-ready.

This is a review of what was built, not a summary of it. The design rationale
lives in [ADR-0003](../decisions/0003-authentication.md),
[ADR-0004](../decisions/0004-authorization-in-repositories.md),
[ADR-0009](../decisions/0009-single-origin-cookie-transport.md),
[ADR-0017](../decisions/0017-rate-limiting.md), and
[ADR-0018](../decisions/0018-account-lifecycle.md); the current posture is in
[docs/architecture/security.md](../architecture/security.md).

---

## 1. What was reviewed

| Area | Artefacts |
|---|---|
| Credential handling | `core/security.py`, `core/validation.py` |
| Token issue and validation | `core/tokens.py`, `application/auth/session.py`, `authenticate_access_token.py` |
| Session lifecycle | `login_user.py`, `refresh_session.py`, `logout_session.py` |
| Account recovery | `password_reset.py`, `email_verification.py`, `account_tokens.py`, `account_mail.py` |
| Abuse controls | `rate_limits.py`, `infrastructure/cache/rate_limiter.py` |
| Transport | `api/cookies.py`, `api/middleware/csrf.py`, `api/middleware/security_headers.py` |
| Authorization | `domain/access.py`, `application/access.py`, every workspace-scoped repository |
| Audit | `domain/ports/audit.py`, `infrastructure/audit.py`, every producer |
| Configuration | `core/config.py` startup validation |

Method: line-by-line reading of each path an attacker reaches, against the
eight scenarios the module was required to defend, plus the failure modes each
dependency introduces. Every finding below was reproduced by a test before
being fixed, and those tests are in the suite.

---

## 2. Findings, fixed

Nine issues were found and fixed during this review. Three of them were live
defects that would have shipped.

### 2.1 `X-Forwarded-For` was trusted unconditionally — **High**

`get_client_ip` read the header whenever present. The header is client-supplied,
so an attacker rotating it obtained a **fresh per-IP rate-limit bucket on every
request** — defeating that limit against precisely the distributed attacker it
exists to stop, and leaving only the per-account limit, which credential
stuffing never trips. The same value is written into audit records, so the trail
could be poisoned with arbitrary addresses.

**Fixed:** the header is consulted only when `ORBIT_TRUST_PROXY_HEADERS` is
explicitly enabled. The default is off, and the default *is* the control — a
deployment that forgets to configure a proxy degrades to "every client shares
the proxy's address", which is wrong in the safe direction and visible the first
time a limit trips for everyone at once. `tests/api/test_client_ip.py`.

### 2.2 Registration was unmetered — **Medium**

Every call hashes a password with Argon2id (64 MiB, ~50 ms) *before* anything
else. Unmetered, that is a CPU and memory exhaustion sink costing the attacker
one HTTP request. It was also the unmetered form of the one enumeration oracle
ORBIT does expose: a duplicate address returns `409`.

**Fixed:** the same per-account and per-IP guard as password reset, evaluated
before the hash.

### 2.3 A password reset did not terminate refresh tokens — **Medium**

`set_password` bumps `users.token_epoch`, which kills outstanding *access*
tokens. Refresh tokens carry no epoch. A stolen refresh token would therefore
have kept minting fresh access tokens straight through the reset intended to
stop it — the reset would have looked successful and recovered nothing.

**Fixed:** `refresh_tokens.revoke_all_for_user` is called in the same
transaction. Both halves are required; either alone is a hole.

### 2.4 Transparent hash upgrade issued a dead session — **Medium**

When Argon2 parameters are raised, login re-encodes the password. That path
called `set_password`, which bumps the token epoch — while the access token for
this very login was being minted from the epoch already read. The user would
have "logged in" successfully and been rejected on their next request, and only
after a parameter change, making it the kind of defect that appears months after
the code was written.

**Fixed:** `upgrade_password_hash` re-encodes without touching the epoch. The
two operations are now separate methods with docstrings explaining why merging
them is a bug rather than a simplification.

### 2.5 Reuse detection over-reported compromises — **Medium**

The refresh path treated "already consumed" and "already revoked" identically
and recorded both as `auth.refresh_token_reuse_detected`. Only the first is
evidence of theft. The second is the *expected aftermath* of the first — the
legitimate client retrying with a token it had no way of knowing was dead — so
one compromise was reported as many, which is how an alert becomes noise and
then becomes ignored.

**Fixed:** only a consumed token is audited as reuse; a revoked one logs at INFO
and is rejected without a claim it cannot support.

### 2.6 Audit actions were declared with no producer — **Medium**

`MEMBER_ADDED`, `MEMBER_ROLE_CHANGED`, `MEMBER_REMOVED`, `WORKSPACE_DELETED`,
and `PERMISSION_DENIED` existed in the enum and were emitted by nothing. An
audit trail that silently omits membership changes is worse than none: it
invites the reader to conclude nothing happened.

**Fixed:** all five are wired. `PERMISSION_DENIED` is recorded in the exception
handler — the single place every 403 already passes through — so no future use
case can forget it. Role changes record both the old and new role, because only
the pair distinguishes an escalation from a demotion when the trail is read
later.

### 2.7 CSRF rejection bypassed the error envelope — **Low**

The middleware returned a bare `403` with no body, the one failure in the API a
client could not parse like every other. **Fixed:** it builds the standard
envelope with code `CSRF_ORIGIN_MISMATCH`, and does not echo the rejected origin
back into the response.

### 2.8 `202` responses carried the body `null` — **Low**

FastAPI serialised the handler's `None`. A client could have come to depend on
it. **Fixed** with an explicit response class.

### 2.9 The test override helper bound stubs incorrectly — **Low, test-only**

`app.dependency_overrides[...] = lambda stub=stub: stub` declares a parameter
that FastAPI introspects as a request field, so the bound stub never reached the
route — silently, because the route still received *a* stub of the right class.
Any assertion about what a route passed to a use case was therefore vacuous.
**Fixed** with a zero-parameter closure. This one is worth recording precisely
because it made tests pass for the wrong reason.

---

## 3. Scenarios exercised

The eight required scenarios, and where each is tested. `tests/unit/test_security.py`
runs them against **real use cases** on an in-memory `UnitOfWork` rather than
stubs: a stub that returns `NotFoundError` proves nothing about whether the
repository would have returned the row.

| Scenario | Coverage |
|---|---|
| Missing authentication | Empty token, deleted account, deactivated account; every protected route rejects before any use case runs |
| Expired sessions | Access token past TTL on an injected clock; expired refresh token; token from before a password change; logout; replay after rotation |
| Invalid credentials | Wrong password; unknown address; deactivated account with the correct password; **the error type, code, and message are asserted identical** for unknown-account and wrong-password |
| Malformed tokens | Empty, non-JWT, two-segment, garbage payload, SQL-injection string, null byte, 10 000 characters, wrong signing key, `alg: none`, valid signature naming a nonexistent user — all rejected as 401, never as a crash |
| Cross-user document access | Another workspace's document id presented with a legitimate context, for read, list, rename, and delete — plus a control proving the id genuinely resolves for its own workspace |
| Cross-workspace access | Non-member, member of a different workspace, invented id, and access ending immediately on removal |
| Privilege escalation | Viewer mutating documents; member inviting; member promoting themselves (and the role asserted unchanged afterwards); admin deleting the workspace, paired with the rename they *are* allowed |
| Brute force | Limit trips; applies to the correct password too; cleared on success; audited; **asserted to be checked before Argon2id runs** |

Plus, beyond the required list: token purpose separation both directions, single
use, supersession, hash-only storage, delivery-failure containment, audit
redaction, and the rate limiter's fail-open behaviour.

**Suite state:** 557 tests pass; ruff, `mypy --strict`, and all four
import-linter contracts are clean.

---

## 4. Properties asserted, not assumed

Several tests exist to pin a property that is easy to break while refactoring
and impossible to notice from a passing happy path:

- The rate limit is checked **before** password verification. If it moved after,
  every rejected attempt would burn an Argon2id hash and the defence would
  become the amplifier for a CPU-exhaustion attack.
- A forged `AccessContext` is inert **because it cannot be obtained**, not
  because repositories re-check it. The test targets `ResolveAccessContext`, the
  actual gate. An earlier version of this test targeted the repository, which
  succeeds by design — it was testing the wrong thing and was rewritten.
- **No audit record anywhere in a full session carries a credential.** The test
  scans the whole trail for the password, a wrong password, the access token,
  and the reset token, rather than checking one field of one event.
- Cross-tenant misses raise `NotFoundError`, never `PermissionDeniedError`. A
  403 would confirm the resource exists and leak it across the boundary.

---

## 5. Accepted risks

Each is a decision, with the condition that would change it.

| Risk | Why accepted | Revisit when |
|---|---|---|
| **Registration discloses that an address is registered** (`409`) | A user whose signup fails has to be told why. Now metered, so it cannot be queried in bulk. Password reset — where the same disclosure is far more damaging — is designed not to make it | A silent-registration flow becomes viable, i.e. a real mail provider exists |
| **Password reset has a timing side channel** — the known-address branch does database writes and an email send; the unknown branch does neither | Bounded by 3 requests/hour/account and 10/hour/IP, which makes statistical sampling impractical | The send moves off the request path (a queued outbox), which also fixes it |
| **Rate limiting fails open on a Redis outage** | Failing closed converts a cache outage into a total authentication outage, including for the operators fixing it. Argon2id still costs per attempt; `/readyz` and an ERROR log make the window visible | Never — but the visibility is what makes it defensible, so alerting on `ratelimit.backend_unavailable` is required before deployment |
| **Access tokens remain valid up to 15 minutes** unless the epoch is bumped | The epoch closes the window for every event that matters (password change, reset, account deletion) | A per-request revocation list is justified by a real requirement |
| **No MFA** | Out of scope for this module | Before handling data whose compromise is not recoverable by password reset |
| **Email verification is not enforced** | Enforcing it makes mail deliverability a hard dependency of authentication and locks out users over spam filtering | A feature exists whose abuse actually requires a proven address |
| **`account_tokens` is not pruned on a schedule** | Expired rows are unredeemable, so this is hygiene rather than exposure | The worker gains a periodic task |

---

## 6. What is **not** verified

Stated plainly, because the difference between "the tests pass" and "this works"
is exactly this list.

**Docker has not started at any point during this milestone** — the host needs a
reboot to complete a WSL installation. Consequently:

- **110 integration tests have never run.** Everything in §3 is verified against
  the in-memory `UnitOfWork` fake. The fake satisfies the same Protocol and
  `mypy --strict` catches drift in its *shape*, but it cannot catch a SQL
  predicate that is wrong. The tenant-isolation and IDOR guarantees in
  particular rest on `WHERE workspace_id = :ctx` clauses **no test has executed
  against PostgreSQL.**
- Migration `0002_account_tokens` has **never been applied to a database**, in
  either direction. Its `downgrade` drops the enum type explicitly to survive a
  rollback-then-roll-forward; that is reasoned, not observed.
- The unique index on `account_tokens.token_hash`, the `purpose` filter, and the
  compare-and-set `consume` have not been exercised under real concurrency.
- `RedisRateLimiter` has never counted against a real Redis. Its fail-open path
  *was* observed live (an ERROR log during a boot smoke test with Redis down),
  but the counting path was not.
- No container image has been built.

**Verified offline:** ruff, `mypy --strict`, import-linter (4/4), 557 tests, and
a live `uvicorn` boot serving `/healthz` (200), the new routes in the OpenAPI
document, `401` on `verify-email/resend` without a session, and the rate limiter
failing open loudly with Redis unavailable.

**This module must not be considered production-ready until the integration
suite has run against a real PostgreSQL and Redis.** The most likely place for a
remaining defect is a repository predicate, which is precisely what the offline
suite cannot reach.

---

## 7. Required before deployment

1. Run the integration suite against real PostgreSQL and Redis; run
   `0001 → 0002 → downgrade → upgrade` against a clean database.
2. Configure a real `EmailSender`. Until then password reset is non-functional —
   loudly, by design, but non-functional.
3. Alert on `ratelimit.backend_unavailable`, `audit.write_failed`, and
   `auth.refresh_token_reuse_detected`. The first two are the failure modes that
   are only acceptable because they are visible; the third is the one event that
   is evidence of an actual compromise.
4. Set `ORBIT_TRUST_PROXY_HEADERS=true` **only** once the edge proxy is
   confirmed to overwrite `X-Forwarded-For` rather than append to it.
5. Schedule `account_tokens` pruning.
6. Have someone who did not write this code review it. This document is a
   self-review, and a self-review finds the bugs its author already knows how to
   look for.
