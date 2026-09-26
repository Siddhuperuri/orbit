"""Response details shared by every adapter that speaks the OpenAI HTTP API."""

from __future__ import annotations

import re
from typing import Any

import httpx

# Google's `google.rpc.RetryInfo.retryDelay`, a protobuf Duration: "41s", "0.5s".
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")


def _error_object(response: httpx.Response) -> dict[str, Any] | None:
    """The `error` object of an error body, if there is one.

    OpenAI sends `{"error": {...}}`; Google's OpenAI-compatible endpoint sends
    the same object wrapped in a one-element list, so that form is unwrapped.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, list) and len(body) == 1:
        body = body[0]
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else None


def error_code(response: httpx.Response) -> str | None:
    """`error.code` from an error body, if there is one."""
    error = _error_object(response)
    code = error.get("code") if error else None
    return code if isinstance(code, str) else None


def retry_after_seconds(response: httpx.Response) -> float | None:
    """How long the provider asked us to wait, in seconds, if it said.

    `Retry-After` first, in its numeric form (the HTTP-date form is not
    honoured: a misparsed date is worse than the retry policy's own backoff).
    Failing that, Google's `RetryInfo` detail in the error body, which is
    where its OpenAI-compatible endpoint puts the delay instead of a header.
    """
    raw = response.headers.get("retry-after")
    if raw is not None:
        try:
            seconds = float(raw)
        except ValueError:
            return None
        return seconds if seconds >= 0 else None
    return _google_retry_delay(response)


def _google_retry_delay(response: httpx.Response) -> float | None:
    error = _error_object(response)
    details = error.get("details") if error else None
    if not isinstance(details, list):
        return None
    for detail in details:
        if not isinstance(detail, dict):
            continue
        kind = detail.get("@type")
        delay = detail.get("retryDelay")
        if (
            isinstance(kind, str)
            and kind.endswith("google.rpc.RetryInfo")
            and isinstance(delay, str)
        ):
            match = _DURATION.match(delay)
            return float(match.group(1)) if match else None
    return None
