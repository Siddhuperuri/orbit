"""The metrics module's own contract: what it exports, and how it is guarded."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY, Gauge

from orbit.core import metrics
from orbit.core.metrics import bounded, observe, observe_duration
from tests.unit.observability.helpers import sample

# Identifiers that must live in logs, never in labels: each is unbounded, and an
# unbounded label is how a metrics backend is taken down.
_FORBIDDEN_LABELS = frozenset(
    {
        "user_id",
        "workspace_id",
        "document_id",
        "job_id",
        "request_id",
        "chunk_id",
        "email",
        "path",
        "url",
        "filename",
        "query",
        "text",
    }
)


def _orbit_collectors() -> list[object]:
    return [
        value
        for name, value in vars(metrics).items()
        if name.isupper() and hasattr(value, "_labelnames") and hasattr(value, "_name")
    ]


def test_every_metric_is_namespaced_and_documented() -> None:
    collectors = _orbit_collectors()
    assert len(collectors) > 20, "metrics were expected to be defined in this module"
    for collector in collectors:
        name = collector._name  # type: ignore[attr-defined]
        documentation = collector._documentation  # type: ignore[attr-defined]
        assert name.startswith("orbit_"), name
        # The docstring rule: every series says which question it answers.
        assert "Answers:" in documentation, f"{name} does not say what question it answers"


def test_no_metric_has_an_unbounded_label() -> None:
    for collector in _orbit_collectors():
        labels = set(collector._labelnames)  # type: ignore[attr-defined]
        offending = labels & _FORBIDDEN_LABELS
        assert not offending, f"{collector._name} labels by {offending}"  # type: ignore[attr-defined]


def test_every_gauge_declares_how_processes_combine() -> None:
    """A gauge without a multiprocess mode silently reports one process's view
    (or the wrong aggregate) the moment the API runs more than one worker."""
    for collector in _orbit_collectors():
        if isinstance(collector, Gauge):
            assert collector._multiprocess_mode is not None


class TestBounded:
    def test_known_values_pass_through(self) -> None:
        assert bounded("pdf", {"pdf", "text"}) == "pdf"

    def test_unknown_values_collapse_to_the_default(self) -> None:
        assert bounded("application/x-attacker-1234", {"pdf", "text"}) == "other"

    def test_none_collapses_to_the_default(self) -> None:
        assert bounded(None, {"pdf"}, default="unknown") == "unknown"


class TestObserve:
    def test_records_ok_with_a_duration(self) -> None:
        before = sample("orbit_db_query_duration_seconds_count", outcome="ok", operation="OPX")
        with observe(metrics.DB_QUERY_DURATION, operation="OPX"):
            pass
        after = sample("orbit_db_query_duration_seconds_count", outcome="ok", operation="OPX")
        assert after == before + 1

    def test_labels_a_raised_exception_and_reraises_it(self) -> None:
        before = sample("orbit_db_query_duration_seconds_count", outcome="error", operation="OPX")
        with (
            pytest.raises(ValueError, match="boom"),
            observe(metrics.DB_QUERY_DURATION, operation="OPX"),
        ):
            raise ValueError("boom")
        after = sample("orbit_db_query_duration_seconds_count", outcome="error", operation="OPX")
        assert after == before + 1, "a metric must record the failure without swallowing it"

    def test_the_body_can_set_the_outcome(self) -> None:
        before = sample("orbit_db_query_duration_seconds_count", outcome="custom", operation="OPX")
        with observe(metrics.DB_QUERY_DURATION, operation="OPX") as outcome:
            outcome.value = "custom"
        after = sample("orbit_db_query_duration_seconds_count", outcome="custom", operation="OPX")
        assert after == before + 1

    def test_a_classifier_maps_exceptions_to_bounded_outcomes(self) -> None:
        before = sample(
            "orbit_db_query_duration_seconds_count", outcome="classified", operation="OPX"
        )
        with (
            pytest.raises(RuntimeError),
            observe(metrics.DB_QUERY_DURATION, classify=lambda _exc: "classified", operation="OPX"),
        ):
            raise RuntimeError
        after = sample(
            "orbit_db_query_duration_seconds_count", outcome="classified", operation="OPX"
        )
        assert after == before + 1

    def test_observe_duration_records_even_when_the_block_raises(self) -> None:
        before = sample("orbit_search_stage_duration_seconds_count", stage="lexical")
        with (
            pytest.raises(ValueError, match="x"),
            observe_duration(metrics.SEARCH_STAGE_DURATION, stage="lexical"),
        ):
            raise ValueError("x")
        assert sample("orbit_search_stage_duration_seconds_count", stage="lexical") == before + 1


def test_the_default_registry_is_served_without_multiprocess_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(metrics.MULTIPROC_ENV, raising=False)
    assert metrics.serving_registry() is REGISTRY
