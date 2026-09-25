# 0018 — Password reset and email verification

- **Status:** Accepted
- **Date:** 2026-09-10
- **Relates to:** [ADR-0003](0003-authentication.md) (token handling),
  [ADR-0017](0017-rate-limiting.md) (limits on these endpoints)

## Context

Password reset is the most dangerous feature in an authentication system. It is
an unauthenticated endpoint whose entire purpose is to grant access to an
account, and every design decision in it is a decision about how an attacker
takes over accounts. Email verification shares the same machinery — a hashed,
single-use, expiring secret mailed to an address — and differs only in what
redeeming one does.

Two constraints shaped this from the start:

1. **The reset request must not disclose whether an account exists.** A form
   that says "no account with that email" is an unauthenticated
   account-enumeration oracle. No rate limit helps: an attacker needs one
   request per address.
2. **ORBIT has no mail provider yet.** The obvious shortcut — a "fake" sender
   for development — is the single most dangerous thing this feature could
   ship with.

## Decision

### One table, two purposes

`account_tokens` serves both flows, with a `purpose` column
(`password_reset` | `email_verification`) that is part of the **lookup
predicate**, not merely stored. `get_by_hash(hash, purpose=…)` means a token
issued for one flow does not exist as far as the other flow's query is
concerned.

This matters concretely: a verification link is long-lived (3 days) and
unauthenticated. Without the purpose predicate, it would *be* a password-reset
link. Two separate tables would have duplicated the issue/consume/expire logic
and, with it, the opportunity to get that logic subtly different in one of them.

### Tokens are stored only as SHA-256 hashes

256 bits of `secrets.token_urlsafe` entropy, stored as a SHA-256 hash, exactly
as for refresh tokens (ADR-0003). SHA-256 rather than Argon2id because the
value already carries uniform high entropy — there is no low-entropy secret for
a memory-hard function to protect. The hash's only job is making a database
read insufficient to redeem anything.

### Reset tokens live 30 minutes; verification tokens live 3 days

A reset link is a bearer credential sitting in an inbox, so the window in which
a compromised mailbox yields an account takeover is measured in minutes. A
verification link grants access to nothing — it only asserts control of an
address — so the cost of a longer window is low and the cost of expiring before
someone checks a rarely-read inbox is high.

### Issuing supersedes

Issuing a token invalidates every outstanding token of the same purpose for
that user. Without this, every reset email ever sent stays live until its own
expiry, so an old message recovered from a compromised mailbox still takes over
the account.

### Redemption is compare-and-set

`consume` is `UPDATE … WHERE consumed_at IS NULL … RETURNING`, returning
whether it matched. Two requests racing with the same token both read it as
unspent; only one `UPDATE` matches, and the loser is rejected rather than both
succeeding.

### One error for every failure mode

Unknown, expired, already spent, wrong purpose — all produce the same
`BadRequestError` with the same message. Distinguishing them tells an attacker
holding a guessed or stale token which part to change.

### A completed reset terminates every session — both kinds

This is the part most often got wrong, and it has two halves:

- `set_password` bumps `users.token_epoch`, which invalidates every outstanding
  **access token** on its next use (ADR-0003).
- `refresh_tokens.revoke_all_for_user` invalidates every outstanding **refresh
  token**.

The second is not redundant. A refresh token carries no epoch, so bumping the
epoch alone would leave a stolen refresh token minting fresh access tokens
straight through the reset that was meant to stop it. Resetting a password
because someone stole your session is worthless if their session survives the
reset.

### The request endpoint is a black box

`RequestPasswordReset.execute` returns `None` for every input, always. The
unknown-address branch is a silent no-op — **audited**, so an operator
reviewing the trail sees the attempt even though the caller cannot. The HTTP
layer returns `202 Accepted` with an empty body in both cases.

Delivery failures are caught and logged, never surfaced. This is the subtle
part: the unknown-address branch cannot fail, so a propagated delivery error
would itself mean "this address is known".

### There is no fake email sender

`EmailProvider` has exactly two members and neither is `fake`:

- **`unconfigured`** (default) — every send raises `EmailDeliveryError`. Any
  flow depending on real delivery fails visibly the first time it is exercised
  rather than appearing to work.
- **`console`** — writes the message to the log so a developer can copy the
  link locally. **Refused at startup in production**, because a one-time
  account link in a log stream is a credential in a log stream.

A sender that accepts a message and silently drops it makes a broken reset flow
look healthy in every environment where nobody checks an inbox — and the
environment where someone finally does is production, during a lockout.

### Links point at the frontend

`{token}` is substituted into a frontend URL; the frontend posts the token back
to the API. The token therefore never appears in an API URL that a reverse
proxy, access log, or `Referer` header would record. Startup validates that
each template contains `{token}`, and production additionally requires `https`.

### Email verification is not enforced at login

`users.email_verified_at` records the fact; nothing gates sign-in on it.

Gating login would lock a user out of an account they legitimately created
because a message went to spam, and would make mail deliverability a hard
dependency of authentication — with no mail provider configured, nobody could
sign in at all. The gate belongs on the specific actions where an unverified
address is genuinely dangerous, decided per feature.

The confirmation endpoint is deliberately **unauthenticated**: the link is
followed from a mail client, which may not be the browser holding the session.
It returns no session — an emailed link must never be a login credential.

The resend endpoint *does* require a session, and takes the user id from that
session, never from the request. Accepting one in the body would be an IDOR
with an outbound-email amplifier attached.

## Consequences

**Accepted costs**

- ORBIT cannot send mail until a provider is configured. Password reset is
  therefore non-functional in a fresh deployment, and that is the intended
  failure mode: loud and obvious rather than silent.
- A `429` on the reset endpoint reveals that an address was asked about
  recently. See ADR-0017 for why that trade is accepted.
- An expired or superseded link produces an error a legitimate user may find
  unhelpful, because the message cannot say *which* of the four reasons applies.

**Open items**

- No real provider adapter (SES, Postmark, SMTP) yet. `EmailSender` exists so
  that decision does not reach into either flow.
- No delivery retry or outbox. A failed send is lost and the user must request
  another; a durable outbox is worth revisiting once a provider exists.
- `account_tokens` needs periodic pruning of expired rows. `prune_expired` is
  implemented; scheduling it is a worker concern and is not yet wired.
- Password *change* by an authenticated user (as distinct from reset) is not
  yet implemented; `AuditAction.PASSWORD_CHANGED` is reserved for it.

## Alternatives considered

| Option | Why not |
|---|---|
| Separate tables per flow | Duplicates issue/consume/expire logic, and with it the chance of divergence. |
| Signed stateless tokens (JWT) | Cannot be revoked or marked spent without server state, which defeats single use — the state has to exist anyway. |
| Argon2id on the token hash | The value already has 256 bits of uniform entropy; a memory-hard function protects nothing here and costs a hash per redemption attempt. |
| "No account with that email" response | An unauthenticated enumeration oracle. |
| A `fake` email provider | Makes a broken reset flow indistinguishable from a working one. |
| Verification enforced at login | Makes mail deliverability a hard dependency of authentication, and locks out legitimate users over spam filtering. |
| Reset link pointing at the API | Puts the token in a URL that proxies and access logs record. |
