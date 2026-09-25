"""Shared test fixtures.

Settings are constructed explicitly rather than read from a dotenv file, so the
suite is hermetic: it produces the same result on a developer's machine, on a
machine with a stale ``.env``, and in CI where no dotenv file exists at all.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.composition.app import create_app
from orbit.core.config import AIProvider, Environment, LogFormat, Settings

# Deterministic, obviously-fake values. Long enough to satisfy the secret-key
# validator without resembling anything real.
_TEST_SECRET_KEY = "test-secret-key-not-for-any-real-environment-0123456789abcdef"


def build_settings(**overrides: object) -> Settings:
    """Construct settings for a test, with optional per-test overrides.

    ``_env_file=None`` is essential: without it pydantic-settings would read a
    developer's ``.env`` and the suite would behave differently depending on
    whose machine it runs on.
    """
    defaults: dict[str, object] = {
        "env": Environment.TEST,
        "log_level": "WARNING",
        "log_format": LogFormat.CONSOLE,
        "database_url": "postgresql+asyncpg://orbit:test@localhost:5432/orbit_test",
        "redis_url": "redis://localhost:6379/9",
        "celery_broker_url": "redis://localhost:6379/10",
        "celery_result_backend": "redis://localhost:6379/11",
        "s3_endpoint_url": "http://localhost:9000",
        "s3_bucket": "orbit-test",
        "s3_access_key_id": "test-access-key",
        "s3_secret_access_key": "test-secret-key",
        "secret_key": _TEST_SECRET_KEY,
        "ai_provider": AIProvider.FAKE,
        "embedding_dimensions": 1536,
        # No test binds a port; metrics are still collected in process.
        "metrics_enabled": False,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return build_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """The real application, wired with test settings.

    Building the container does not open any connection -- SQLAlchemy, redis-py,
    and botocore all connect lazily -- so this is fast and works offline.
    """
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Test client that runs the application's lifespan.

    Entering the context manager is what triggers startup and shutdown, so
    lifecycle behaviour is exercised rather than bypassed.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def integration_database_url() -> str:
    """URL for the integration test database.

    Read from the environment so CI and local runs can point at different hosts.
    Tests requiring it are marked ``integration`` and skipped when unset.
    """
    url = os.environ.get("ORBIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ORBIT_TEST_DATABASE_URL is not set; run `npm run infra:up` first")
    return url
