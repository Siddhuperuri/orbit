"""Response details shared by every adapter that speaks the OpenAI HTTP API."""

from __future__ import annotations

import httpx


def error_code(response: httpx.Response) -> str | None:
    """`error.code` from an error body, if there is one."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


def retry_after_seconds(response: httpx.Response) -> float | None:
    """`Retry-After` in seconds, if the provider sent a numeric one.

    The HTTP-date form is not honoured: providers of this API send seconds,
    and a misparsed date is worse than the retry policy's own backoff.
    """
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
