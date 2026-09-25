"""Logging configuration must survive actually being used.

This exists because of a real defect: ``add_logger_name`` was paired with a
factory producing loggers that have no ``.name``. Nothing failed at
configuration time, and nothing failed on the happy path -- it raised
``AttributeError`` the first time an error was logged, which is precisely when
logging must work.

So these tests emit records at every level, in both output formats, rather than
asserting that configuration returned without raising.
"""

from __future__ import annotations

import json
import logging

import pytest

from orbit.core.config import LogFormat
from orbit.core.logging import (
    bind_correlation,
    clear_correlation,
    configure_logging,
    get_logger,
)
from tests.conftest import build_settings


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    """Keep configuration changes from leaking between tests."""
    clear_correlation()


@pytest.mark.parametrize("log_format", [LogFormat.CONSOLE, LogFormat.JSON])
def test_every_level_emits_without_raising(log_format: LogFormat) -> None:
    configure_logging(build_settings(log_format=log_format, log_level="DEBUG"))
    logger = get_logger("orbit.test")

    logger.debug("test.debug", detail="value")
    logger.info("test.info", count=1)
    logger.warning("test.warning")
    logger.error("test.error", error_code="SOMETHING")


@pytest.mark.parametrize("log_format", [LogFormat.CONSOLE, LogFormat.JSON])
def test_exception_logging_works(log_format: LogFormat) -> None:
    """The path that the original defect broke."""
    configure_logging(build_settings(log_format=log_format))
    logger = get_logger("orbit.test")

    try:
        raise RuntimeError("boom")  # noqa: TRY301 -- deliberately raised to be logged
    except RuntimeError:
        logger.exception("test.unhandled")


@pytest.mark.parametrize("log_format", [LogFormat.CONSOLE, LogFormat.JSON])
def test_bound_loggers_emit(log_format: LogFormat) -> None:
    # `logger.bind(...)` is used in the error handlers; a bound logger takes a
    # different code path through structlog than an unbound one.
    configure_logging(build_settings(log_format=log_format))
    logger = get_logger("orbit.test").bind(error_code="NOT_FOUND", document_id="d1")
    logger.info("test.bound")
    logger.error("test.bound_error")


def test_output_is_json_when_configured(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="INFO"))
    get_logger("orbit.test").info("test.json", count=3)

    line = capsys.readouterr().err.strip().splitlines()[-1]
    record = json.loads(line)
    assert record["event"] == "test.json"
    assert record["count"] == 3
    assert record["service"] and record["environment"] and record["version"]
    assert record["logger"] == "orbit.test", "add_logger_name must resolve a name"


def test_correlation_ids_appear_on_records(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="INFO"))
    bind_correlation(request_id="01JB2X8N4K7QF3TVWZ9M5PDCRA", document_id="doc_1")
    get_logger("orbit.test").info("test.correlated")

    record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert record["request_id"] == "01JB2X8N4K7QF3TVWZ9M5PDCRA"
    assert record["document_id"] == "doc_1"


def test_unrecognised_correlation_keys_are_dropped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="INFO"))
    # A typo must not silently create a field no dashboard queries.
    bind_correlation(reqest_id="typo", request_id="01JB2X8N4K7QF3TVWZ9M5PDCRA")
    get_logger("orbit.test").info("test.typo")

    record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert "reqest_id" not in record
    assert record["request_id"] == "01JB2X8N4K7QF3TVWZ9M5PDCRA"


def test_secrets_are_redacted_in_real_output(capsys: pytest.CaptureFixture[str]) -> None:
    """End-to-end check that redaction survives the full processor chain."""
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="INFO"))
    get_logger("orbit.test").info("test.secret", password="hunter2", api_key="sk-live-1")

    output = capsys.readouterr().err
    assert "hunter2" not in output
    assert "sk-live-1" not in output
    assert "[redacted]" in output


def test_third_party_stdlib_logs_are_captured(capsys: pytest.CaptureFixture[str]) -> None:
    """A library logging through stdlib must be structured and redacted too."""
    configure_logging(build_settings(log_format=LogFormat.JSON))
    logging.getLogger("some.third.party").warning("legacy message")

    record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert record["event"] == "legacy message"
    assert record["level"] == "warning"
