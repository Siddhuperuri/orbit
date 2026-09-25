"""Correlation context: what gets attached to a log record, and what must not leak."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from orbit.core.config import LogFormat
from orbit.core.logging import (
    begin_request_context,
    bind_correlation,
    clear_correlation,
    configure_logging,
    current_correlation,
    get_logger,
    operation,
)
from tests.conftest import build_settings
from tests.unit.observability.helpers import parse_log_lines

_SRC = Path(__file__).resolve().parents[3] / "src" / "orbit"


@pytest.fixture(autouse=True)
def _clean() -> None:
    clear_correlation()


def _emit(capsys: pytest.CaptureFixture[str], message: str = "test.event") -> dict[str, object]:
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="DEBUG"))
    get_logger("orbit.test").info(message)
    (record,) = parse_log_lines(capsys.readouterr().err)
    return record


def test_every_correlation_field_reaches_the_record(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="INFO"))
    bind_correlation(
        request_id="req",
        user_id="usr",
        workspace_id="ws",
        document_id="doc",
        job_id="job",
        operation="documents.upload_document",
    )
    get_logger("orbit.test").info("test.event")
    (record,) = parse_log_lines(capsys.readouterr().err)
    assert {k: record[k] for k in ("request_id", "user_id", "workspace_id", "document_id")} == {
        "request_id": "req",
        "user_id": "usr",
        "workspace_id": "ws",
        "document_id": "doc",
    }
    assert record["job_id"] == "job"
    assert record["operation"] == "documents.upload_document"


def test_unrecognised_keys_are_not_bound(capsys: pytest.CaptureFixture[str]) -> None:
    """A typo (`workspaceid`) must not create a parallel field no dashboard queries."""
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="INFO"))
    bind_correlation(workspaceid="ws", request_id="req")
    get_logger("orbit.test").info("test.event")
    (record,) = parse_log_lines(capsys.readouterr().err)
    assert "workspaceid" not in record
    assert record["request_id"] == "req"


def test_service_identity_is_on_every_record(capsys: pytest.CaptureFixture[str]) -> None:
    record = _emit(capsys)
    assert {"service", "environment", "version", "timestamp", "level"} <= set(record)


class TestOperation:
    def test_names_the_unit_of_work_inside_the_block_only(self) -> None:
        with operation("search.execute"):
            assert current_correlation()["operation"] == "search.execute"
        assert "operation" not in current_correlation()

    def test_nesting_restores_the_outer_operation(self) -> None:
        bind_correlation(operation="conversations.ask")
        with operation("search.execute"):
            assert current_correlation()["operation"] == "search.execute"
        assert current_correlation()["operation"] == "conversations.ask"

    def test_restores_even_when_the_block_raises(self) -> None:
        with pytest.raises(RuntimeError), operation("x"):
            raise RuntimeError
        assert "operation" not in current_correlation()


class TestRequestScopedFields:
    async def test_a_value_bound_in_a_child_task_is_visible_to_the_parent(self) -> None:
        """The reason the access-log middleware can name the user and workspace.

        Downstream `BaseHTTPMiddleware` runs the application in a child task,
        whose contextvars are a *copy*: structlog's own bindings made there are
        lost to the parent. The shared field dict is what carries them back.
        """
        fields = begin_request_context("req-1")

        async def handler() -> None:
            bind_correlation(user_id="usr", workspace_id="ws")

        await asyncio.create_task(handler())

        assert fields["user_id"] == "usr"
        assert fields["workspace_id"] == "ws"
        assert fields["request_id"] == "req-1"
        # ...while structlog's own context, as predicted, did not travel back.
        assert "user_id" not in current_correlation()

    def test_a_new_request_starts_with_no_fields_from_the_last(self) -> None:
        first = begin_request_context("req-1")
        bind_correlation(user_id="usr")
        second = begin_request_context("req-2")
        assert second == {"request_id": "req-2"}
        assert first["user_id"] == "usr"

    def test_clear_detaches_the_shared_fields(self) -> None:
        fields = begin_request_context("req-1")
        clear_correlation()
        bind_correlation(user_id="late")
        assert "user_id" not in fields


def test_secrets_and_document_text_never_reach_a_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="INFO"))
    get_logger("orbit.test").info(
        "test.event",
        password="hunter2",
        authorization="Bearer abc",
        api_key="sk-live",
        text="the confidential contract text",
        content="the confidential contract text",
        chunk_text="the confidential contract text",
        nested={"token": "t", "safe": "ok"},
    )
    raw = capsys.readouterr().err
    for secret in ("hunter2", "Bearer abc", "sk-live", "confidential contract", '"t"'):
        assert secret not in raw
    (record,) = parse_log_lines(raw)
    assert json.dumps(record["nested"]) == '{"token": "[redacted]", "safe": "ok"}'


def test_no_log_call_shadows_a_correlation_field() -> None:
    """`operation` is bound in context (the unit of work). A call that passes it as
    a keyword *replaces* the bound value on that record -- silently attributing
    a line to the wrong operation. Structural rule, checked over the source."""
    reserved = {"operation"}
    log_methods = {"debug", "info", "warning", "error", "exception", "critical"}
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in log_methods
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"logger", "log"}
            ):
                for keyword in node.keywords:
                    if keyword.arg in reserved:
                        offenders.append(f"{path.relative_to(_SRC)}:{node.lineno} {keyword.arg}=")
    assert not offenders, "\n".join(offenders)
