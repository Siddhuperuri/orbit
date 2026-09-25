"""Rate-limit policy for credential, account-lifecycle, and upload endpoints.

`AuthRateLimitGuard` is a generic account+IP fixed-window guard; it lives
under `auth/` because credential endpoints were the first callers, and
`documents/upload_document.py` reuses it unchanged rather than duplicating the
same two-limit logic under a different name.

Policy lives here, in the application layer, rather than in a route decorator
or middleware, for two reasons that are not stylistic:

1. The keys are **domain** values -- an email address, a client address -- and
   the per-account key is only knowable after parsing the request body. A
   middleware that has not parsed the body cannot compute it.
2. A limit expressed in a decorator is a limit that a new caller of the same
   use case silently skips. Expressed here, every path into `LoginUser` is
   limited, including one added next year by someone who never read this file.

The two-limit shape (per account, per address) is argued in
`domain/ports/rate_limiter.py`; this module is the binding of that shape to
concrete keys and configured limits.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from orbit.core.config import Settings
from orbit.domain.errors import RateLimitedError
from orbit.domain.ports.audit import AuditAction, AuditEvent, AuditSink
from orbit.domain.ports.rate_limiter import RateLimiter, RateLimitRule


def _account_key(scope: str, identity: str) -> str:
    """Hash the identity into the key rather than embedding it.

    `identity` is whatever names the account at this point in the flow: a
    submitted email address before authentication, a user id after it.

    It is hashed because Redis keys turn up in `SLOWLOG`, `MONITOR`, keyspace
    dumps, and any debugging session -- none of which should become a list of
    every email address that has attempted to sign in. The hash is stable, so
    the limit still tracks the account; it simply does not spell it out.
    """
    digest = hashlib.sha256(identity.strip().lower().encode("utf-8")).hexdigest()
    return f"{scope}:account:{digest}"


def _ip_key(scope: str, client_ip: str) -> str:
    return f"{scope}:ip:{client_ip}"


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """One scope's limits, resolved from settings."""

    scope: str
    per_account: RateLimitRule
    per_ip: RateLimitRule

    @staticmethod
    def for_login(settings: Settings) -> RateLimitPolicy:
        window = settings.login_rate_limit_window_seconds
        return RateLimitPolicy(
            scope="login",
            per_account=RateLimitRule(
                limit=settings.login_rate_limit_per_account, window_seconds=window
            ),
            per_ip=RateLimitRule(limit=settings.login_rate_limit_per_ip, window_seconds=window),
        )

    @staticmethod
    def for_account_email(settings: Settings, scope: str) -> RateLimitPolicy:
        window = settings.account_email_rate_limit_window_seconds
        return RateLimitPolicy(
            scope=scope,
            per_account=RateLimitRule(
                limit=settings.account_email_rate_limit_per_account, window_seconds=window
            ),
            per_ip=RateLimitRule(
                limit=settings.account_email_rate_limit_per_ip, window_seconds=window
            ),
        )

    @staticmethod
    def for_upload(settings: Settings) -> RateLimitPolicy:
        window = settings.upload_rate_limit_window_seconds
        return RateLimitPolicy(
            scope="upload",
            per_account=RateLimitRule(
                limit=settings.upload_rate_limit_per_account, window_seconds=window
            ),
            per_ip=RateLimitRule(limit=settings.upload_rate_limit_per_ip, window_seconds=window),
        )

    @staticmethod
    def for_search(settings: Settings) -> RateLimitPolicy:
        """Searches: each one costs a paid embedding call and two index scans.

        A separate scope from `chat` rather than a shared one, because the two
        have genuinely different shapes: a person refining a query issues a
        burst of searches, and charging those against the same budget as their
        questions would lock them out of chat for typing quickly.
        """
        window = settings.search_rate_limit_window_seconds
        return RateLimitPolicy(
            scope="search",
            per_account=RateLimitRule(
                limit=settings.search_rate_limit_per_account, window_seconds=window
            ),
            per_ip=RateLimitRule(limit=settings.search_rate_limit_per_ip, window_seconds=window),
        )

    @staticmethod
    def for_chat(settings: Settings) -> RateLimitPolicy:
        """Questions: each one costs a retrieval and a language-model call."""
        window = settings.chat_rate_limit_window_seconds
        return RateLimitPolicy(
            scope="chat",
            per_account=RateLimitRule(
                limit=settings.chat_rate_limit_per_account, window_seconds=window
            ),
            per_ip=RateLimitRule(limit=settings.chat_rate_limit_per_ip, window_seconds=window),
        )


class AuthRateLimitGuard:
    """Applies a policy and raises `RateLimitedError` when either limit trips."""

    def __init__(self, limiter: RateLimiter, policy: RateLimitPolicy, audit: AuditSink) -> None:
        self._limiter = limiter
        self._policy = policy
        self._audit = audit

    async def check(self, *, identity: str, client_ip: str | None) -> None:
        """Record an attempt and reject if either limit is exhausted.

        Both limits are recorded even when the first one already rejects. That
        is intentional: skipping the second on rejection would let an attacker
        who has tripped the cheap per-account limit continue to consume the
        per-IP budget for free, and would make the two counters disagree about
        how many attempts actually happened.
        """
        account = await self._limiter.check(
            _account_key(self._policy.scope, identity), self._policy.per_account
        )
        ip = (
            await self._limiter.check(_ip_key(self._policy.scope, client_ip), self._policy.per_ip)
            if client_ip
            else None
        )

        checked = [d for d in (account, ip) if d is not None]
        rejections = [d for d in checked if not d.allowed]
        if not rejections:
            return

        # The longest wait of the tripped limits: telling the caller the
        # shorter one would invite a retry that is rejected again.
        retry_after = max(d.retry_after_seconds for d in rejections)

        # Audited here rather than at the call sites, so a scope added later
        # cannot forget to record it. `which` distinguishes a targeted attack
        # on one account from a spray across many from one host -- the two need
        # different responses, and the counter alone cannot tell them apart.
        await self._audit.record(
            AuditEvent(
                action=AuditAction.RATE_LIMIT_EXCEEDED,
                # `identity` is an address pre-authentication and a user id
                # after it; only the former belongs in `actor_email`, and
                # putting a uuid there would poison queries that group by it.
                actor_email=identity if "@" in identity else None,
                client_ip=client_ip,
                metadata={
                    "scope": self._policy.scope,
                    "which": "account" if not account.allowed else "ip",
                    "retry_after_seconds": retry_after,
                },
            )
        )

        msg = "Too many attempts. Try again later."
        raise RateLimitedError(msg, retry_after_seconds=retry_after, scope=self._policy.scope)

    async def clear(self, *, identity: str) -> None:
        """Forget an account's attempts after a success.

        Only the account counter is cleared, never the per-IP one. One
        successful login must not reset the address budget, or an attacker
        with a single valid account could launder unlimited guesses against
        every other account from the same host.
        """
        await self._limiter.reset(_account_key(self._policy.scope, identity))
