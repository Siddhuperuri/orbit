"""Time as a dependency.

Everything that reasons about "now" -- token expiry, issued-at timestamps --
takes a `Clock` rather than calling `datetime.now()` directly. That is what lets
a test set a token's expiry to one second in the past without sleeping, and it
removes an entire class of flaky time-based test.

Database-generated timestamps (`server_default=func.now()`) are a separate,
deliberate choice (see infrastructure/db/models/base.py) and are not routed
through this: a clock-skewed application host must not be able to write a
timestamp that disagrees with the database's own ordering. This module is for
values the *application* reasons about before anything reaches a query --
principally, "has this token expired yet?"
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """The real clock. Used everywhere except tests."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock that advances only when told to.

    Lets a test express "issue a token, then let it expire" as a value, not as
    a sleep -- deterministic and instant.
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._current = start or datetime.now(UTC)

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        self._current += delta
