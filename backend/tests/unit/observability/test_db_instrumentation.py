"""Database latency, error classification, and slow-query logging.

The cursor events are SQLAlchemy's, not asyncpg's, so a synchronous in-memory
SQLite engine exercises exactly the code that runs against PostgreSQL. What only
PostgreSQL can prove (SQLSTATEs from a real server) is covered by the
classification table below and by the integration suite.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from orbit.core.config import LogFormat
from orbit.core.logging import configure_logging
from orbit.infrastructure.db.instrumentation import (
    error_kind,
    instrument_engine,
    publish_pool_gauges,
    statement_type,
)
from tests.conftest import build_settings
from tests.unit.observability.helpers import sample


@pytest.fixture
def engine() -> Iterator[Engine]:
    sqlite = create_engine("sqlite://")
    yield sqlite
    sqlite.dispose()


class TestStatementType:
    @pytest.mark.parametrize(
        ("statement", "expected"),
        [
            ("SELECT 1", "select"),
            ("  select * from t", "select"),
            ("WITH x AS (SELECT 1) SELECT * FROM x", "select"),
            ("INSERT INTO t VALUES (1)", "insert"),
            ("UPDATE t SET a = 1", "update"),
            ("DELETE FROM t", "delete"),
            ("CREATE TABLE t (a int)", "other"),
            ("", "other"),
        ],
    )
    def test_classifies_by_leading_verb(self, statement: str, expected: str) -> None:
        assert statement_type(statement) == expected

    def test_an_arbitrary_statement_cannot_mint_a_new_label(self) -> None:
        """The verb is checked against a fixed set, never used as-is."""
        assert statement_type("DROP TABLE users") == "other"


class TestErrorKind:
    @pytest.mark.parametrize(
        ("sqlstate", "disconnect", "expected"),
        [
            ("57014", False, "statement_timeout"),
            ("40P01", False, "deadlock"),
            ("40001", False, "deadlock"),
            ("08006", False, "connection"),
            (None, True, "connection"),
            ("42P01", False, "other"),
            (None, False, "other"),
            # A unique violation is the database doing its job: idempotent
            # creates rely on it. Counting it would bury the real failures.
            ("23505", False, None),
            ("23503", False, None),
        ],
    )
    def test_classification(self, sqlstate: str | None, disconnect: bool, expected: str) -> None:
        assert error_kind(sqlstate, is_disconnect=disconnect) == expected


class TestTiming:
    def test_a_statement_is_observed_by_type(self, engine: Engine) -> None:
        instrument_engine(engine, slow_query_threshold_ms=60_000)
        before = sample("orbit_db_query_duration_seconds_count", operation="select", outcome="ok")

        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        after = sample("orbit_db_query_duration_seconds_count", operation="select", outcome="ok")
        assert after == before + 1

    def test_a_failed_statement_is_observed_and_counted(self, engine: Engine) -> None:
        instrument_engine(engine, slow_query_threshold_ms=60_000)
        durations = {"operation": "select", "outcome": "error"}
        before_duration = sample("orbit_db_query_duration_seconds_count", **durations)
        before_errors = sample("orbit_db_errors_total", kind="other")

        with engine.connect() as connection, pytest.raises(OperationalError):
            connection.execute(text("SELECT * FROM table_that_does_not_exist"))

        assert sample("orbit_db_query_duration_seconds_count", **durations) == before_duration + 1
        assert sample("orbit_db_errors_total", kind="other") == before_errors + 1

    def test_instrumentation_never_changes_the_result(self, engine: Engine) -> None:
        instrument_engine(engine, slow_query_threshold_ms=60_000)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 41 + 1")).scalar_one() == 42


class TestSlowQueryLog:
    @pytest.fixture
    def events(self) -> Iterator[list[dict[str, object]]]:
        configure_logging(build_settings(log_format=LogFormat.JSON, log_level="INFO"))
        captured: list[dict[str, object]] = []

        class Recorder(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if isinstance(record.msg, dict):
                    captured.append(record.msg)

        handler = Recorder()
        logging.getLogger().addHandler(handler)
        yield captured
        logging.getLogger().removeHandler(handler)

    def test_a_statement_over_the_threshold_is_logged_without_its_values(
        self, engine: Engine, events: list[dict[str, object]]
    ) -> None:
        instrument_engine(engine, slow_query_threshold_ms=0.0001)
        secret = "the-confidential-contract-text"

        with engine.connect() as connection:
            connection.execute(text("SELECT :value AS v"), {"value": secret})

        (slow,) = [e for e in events if e["event"] == "db.slow_query"]
        assert slow["statement_type"] == "select"
        assert slow["outcome"] == "ok"
        assert isinstance(slow["duration_ms"], float)
        # The record carries the statement SQLAlchemy sends -- a placeholder --
        # and never the bound value.
        assert "SELECT" in str(slow["statement"])
        assert secret not in str(events)

    def test_a_long_statement_is_bounded(
        self, engine: Engine, events: list[dict[str, object]]
    ) -> None:
        instrument_engine(engine, slow_query_threshold_ms=0.0001)
        wide = ", ".join(f"{i} AS c{i}" for i in range(400))

        with engine.connect() as connection:
            connection.execute(text(f"SELECT {wide}"))

        (slow,) = [e for e in events if e["event"] == "db.slow_query"]
        assert len(str(slow["statement"])) <= 400

    def test_a_fast_statement_is_not_logged(
        self, engine: Engine, events: list[dict[str, object]]
    ) -> None:
        instrument_engine(engine, slow_query_threshold_ms=60_000)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        assert [e for e in events if e["event"] == "db.slow_query"] == []


class TestPoolGauges:
    def test_reports_in_use_and_idle_connections(self, tmp_path: object) -> None:
        # A file database gets a real QueuePool; `sqlite://` uses a singleton pool.
        pooled = create_engine(f"sqlite:///{tmp_path}/pool.db", pool_size=3, max_overflow=2)
        try:
            first, second = pooled.connect(), pooled.connect()
            publish_pool_gauges(pooled)

            assert sample("orbit_db_pool_connections", state="in_use") >= 2
            assert sample("orbit_db_pool_limit") >= 5
            first.close()
            second.close()
            publish_pool_gauges(pooled)
            assert sample("orbit_db_pool_connections", state="idle") >= 2
        finally:
            pooled.dispose()
