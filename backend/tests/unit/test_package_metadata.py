"""Guards against the package version and the distribution metadata drifting apart.

`orbit.__version__` is what the API reports in `/healthz` and in structured log
records; `pyproject.toml` is what gets baked into the built image. When those
disagree, a production log line attributes a failure to the wrong build, which
is exactly the situation where that attribution matters most.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import orbit

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def test_runtime_version_matches_distribution_metadata() -> None:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    assert orbit.__version__ == project["version"]


def test_package_ships_type_information() -> None:
    """A `py.typed` marker is what makes strict typing visible to consumers."""
    marker = Path(orbit.__file__).parent / "py.typed"
    assert marker.is_file(), "src/orbit/py.typed is missing; downstream typing breaks silently"


def test_interpreter_is_within_the_supported_range() -> None:
    """Fails loudly if a developer's virtualenv drifts off the supported interpreter."""
    assert (3, 11) <= sys.version_info[:2] < (3, 13)
