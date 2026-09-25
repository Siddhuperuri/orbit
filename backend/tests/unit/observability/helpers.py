"""Reading metric samples in tests.

The registry is process-global, so a test never asserts an absolute value --
another test in the same process may have moved it. It records a sample before
the action and asserts the *change*.
"""

from __future__ import annotations

import json

from prometheus_client import REGISTRY


def sample(name: str, **labels: str) -> float:
    """Current value of one series, or 0.0 if it has never been touched."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def parse_log_lines(text: str) -> list[dict[str, object]]:
    """JSON log records from captured stderr, ignoring anything that is not one."""
    records: list[dict[str, object]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records
