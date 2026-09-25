"""Rate limiting port.

Brute-forcing a password is not a clever attack; it is a patient one. The only
thing that stops it is making each attempt cost something, and the only thing
that makes an attempt cost something is a limit that survives the attacker
changing IP, changing account, or simply waiting.

Two independent limits are therefore applied to every credential check, and
either one tripping is enough to reject:

* **Per account** -- stops one host spraying many passwords at one victim.
* **Per client address** -- stops one host spraying one password at many
  victims (credential stuffing), which a per-account limit alone never sees.

Neither is sufficient alone. A per-IP limit is trivially defeated by a botnet;
a per-account limit is trivially defeated by rotating targets. Together they
force an attacker to be both distributed *and* slow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """How many attempts, over what window."""

    #: Attempts permitted inside one window.
    limit: int
    #: Window length in seconds.
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            msg = "A rate limit of zero would deny every request."
            raise ValueError(msg)
        if self.window_seconds < 1:
            msg = "window_seconds must be positive."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    #: Attempts left in the current window. Never negative.
    remaining: int
    #: Seconds until the window resets. Sent as `Retry-After` when rejected.
    retry_after_seconds: int


class RateLimiter(Protocol):
    """Counts attempts against a key.

    The counter is deliberately *not* the database. A brute-force attempt is
    high-volume, worthless, and short-lived -- exactly the data that should
    never touch durable storage, where it would compete for write throughput
    with the traffic that matters.
    """

    async def check(self, key: str, rule: RateLimitRule) -> RateLimitDecision:
        """Record an attempt against `key` and report whether it is permitted.

        Recording and checking are one operation on purpose: splitting them
        leaves a window in which many concurrent requests all read the same
        under-limit count and all proceed.
        """
        ...

    async def reset(self, key: str) -> None:
        """Clear the counter for `key`.

        Called after a *successful* authentication so that a user who
        mistyped their password four times is not left one typo away from a
        lockout for the rest of the window.
        """
        ...
