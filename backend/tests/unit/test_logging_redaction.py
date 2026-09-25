"""Log redaction.

Redaction is structural rather than a habit, so it is tested as a control. If
these tests fail, credentials are reaching logs.
"""

from __future__ import annotations

from orbit.core.logging import _redact_sensitive


def redact(event: dict[str, object]) -> dict[str, object]:
    return dict(_redact_sensitive(None, "info", event))


def test_redacts_top_level_secrets() -> None:
    result = redact({"event": "login", "password": "hunter2", "user_id": "u1"})
    assert result["password"] == "[redacted]"
    assert result["user_id"] == "u1", "non-sensitive fields must survive"


def test_redacts_case_insensitively() -> None:
    result = redact({"Authorization": "Bearer abc", "API_KEY": "sk-live-123"})
    assert result["Authorization"] == "[redacted]"
    assert result["API_KEY"] == "[redacted]"


def test_redacts_nested_values() -> None:
    # Structured logging encourages nesting; redaction that only looked at the
    # top level would be trivially defeated by a context dict.
    result = redact({"event": "auth", "context": {"refresh_token": "rt_secret", "ip": "1.2.3.4"}})
    context = result["context"]
    assert isinstance(context, dict)
    assert context["refresh_token"] == "[redacted]"
    assert context["ip"] == "1.2.3.4"


def test_redacts_inside_lists() -> None:
    result = redact({"attempts": [{"token": "a"}, {"token": "b"}]})
    attempts = result["attempts"]
    assert isinstance(attempts, list)
    assert all(item["token"] == "[redacted]" for item in attempts)


def test_redacts_document_text() -> None:
    # Document contents must never reach logs -- only length and hash.
    result = redact({"document_id": "d1", "text": "confidential contract terms"})
    assert result["text"] == "[redacted]"
    assert result["document_id"] == "d1"


def test_redacts_presigned_urls() -> None:
    # A presigned URL is a bearer capability; logging one grants access to
    # anyone who can read the logs.
    result = redact({"presigned_url": "https://s3/bucket/key?X-Amz-Signature=deadbeef"})
    assert result["presigned_url"] == "[redacted]"


def test_leaves_ordinary_events_untouched() -> None:
    event = {"event": "http.request", "status_code": 200, "duration_ms": 12.5}
    assert redact(dict(event)) == event
