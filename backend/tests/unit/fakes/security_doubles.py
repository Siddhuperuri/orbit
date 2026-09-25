"""Test doubles for the security ports.

Both are recording fakes rather than mocks: assertions are written against
what was *recorded*, not against which method was called, so a test breaks
when behaviour changes and not when an implementation is refactored.
"""

from __future__ import annotations

from collections import defaultdict

from orbit.domain.ports.audit import AuditAction, AuditEvent
from orbit.domain.ports.email import EmailDeliveryError, EmailMessage
from orbit.domain.ports.rate_limiter import RateLimitDecision, RateLimitRule


class RecordingAuditSink:
    """Collects events in memory.

    Never raises, matching the port's contract -- a test that made an audit
    write fail loudly would be testing a behaviour production must not have.
    """

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def actions(self) -> list[AuditAction]:
        return [event.action for event in self.events]

    def count(self, action: AuditAction) -> int:
        return sum(1 for event in self.events if event.action is action)


class InMemoryRateLimiter:
    """A fixed-window counter with no clock.

    Windows do not expire here: a test that needed one to expire would be
    testing Redis's `EXPIRE`, not ORBIT's policy. `reset` is the only way a
    counter goes back to zero, which is exactly the behaviour the login flow
    depends on.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = defaultdict(int)

    async def check(self, key: str, rule: RateLimitRule) -> RateLimitDecision:
        self.counts[key] += 1
        attempts = self.counts[key]
        return RateLimitDecision(
            allowed=attempts <= rule.limit,
            remaining=max(0, rule.limit - attempts),
            retry_after_seconds=rule.window_seconds,
        )

    async def reset(self, key: str) -> None:
        self.counts.pop(key, None)


class UnavailableRateLimiter:
    """Stands in for a Redis outage: every check fails open.

    Mirrors `RedisRateLimiter`'s documented behaviour so a test can assert
    that authentication still works when the limiter's backend is down.
    """

    async def check(self, key: str, rule: RateLimitRule) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=rule.limit, retry_after_seconds=0)

    async def reset(self, key: str) -> None:
        return


class CapturingEmailSender:
    """Accepts every message and keeps it.

    This is a *test* double, not the `fake` provider the architecture refuses
    to ship: it exists only inside the suite, where the assertion that a
    message was produced is the point.
    """

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def last_body(self) -> str:
        return self.sent[-1].text_body


class FailingEmailSender:
    """Always raises, for asserting that delivery failure is not fatal."""

    def __init__(self) -> None:
        self.attempts = 0

    async def send(self, message: EmailMessage) -> None:
        self.attempts += 1
        msg = "Simulated provider outage."
        raise EmailDeliveryError(msg)
