"""FastAPI application factory.

A factory rather than a module-level ``app`` object: a global would be
constructed at import time, which makes it impossible to build an application
with test configuration without mutating process state first.

Startup deliberately does **not** verify that dependencies are reachable. An
instance that refuses to boot because PostgreSQL is briefly unavailable cannot
come back when PostgreSQL returns, and during a rolling deploy that converts a
transient blip into a failed rollout. Reachability is the readiness probe's
question; startup's job is to construct correctly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from orbit import __version__
from orbit.api.deps import bind_request_context
from orbit.api.errors import register_exception_handlers
from orbit.api.middleware.csrf import CsrfOriginCheckMiddleware
from orbit.api.middleware.request_context import RequestContextMiddleware
from orbit.api.middleware.security_headers import SecurityHeadersMiddleware
from orbit.api.v1.routers import (
    auth,
    conversations,
    documents,
    folders,
    health,
    meta,
    search,
    tags,
    workspaces,
)
from orbit.composition.container import Container
from orbit.composition.metrics_server import GaugeRefresher, MetricsServer
from orbit.core.config import Settings, get_settings
from orbit.core.logging import configure_logging, get_logger
from orbit.core.metrics import BUILD_INFO

logger = get_logger(__name__)

API_V1_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application."""
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = Container.create(settings)
        app.state.settings = settings
        app.state.container = container
        app.state.check_readiness = container.check_readiness

        metrics_server, refresher = _start_metrics(settings, container)

        logger.info(
            "application.started",
            environment=settings.env.value,
            version=__version__,
            ai_provider=settings.ai_provider.value,
        )
        try:
            yield
        finally:
            # Runs on SIGTERM after in-flight requests drain, so connections are
            # released deterministically rather than at interpreter exit.
            if refresher is not None:
                await refresher.stop()
            if metrics_server is not None:
                metrics_server.stop()
            await container.aclose()
            logger.info("application.stopped")

    app = FastAPI(
        title="ORBIT API",
        version=__version__,
        summary="Intelligent knowledge and document platform.",
        lifespan=lifespan,
        # Interactive docs expose the full API surface and schema. Useful in
        # development, unnecessary attack surface in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    _register_middleware(app, settings)
    register_exception_handlers(app)
    _register_routers(app)

    return app


def _start_metrics(
    settings: Settings, container: Container
) -> tuple[MetricsServer | None, GaugeRefresher | None]:
    """Expose metrics on their own port and keep the state gauges fresh.

    Metrics are *collected* regardless of this setting -- the counters live in
    process either way -- so switching it off only removes the endpoint.
    """
    BUILD_INFO.labels(
        service=settings.service_name, version=__version__, environment=settings.env.value
    ).set(1)
    if not settings.metrics_enabled:
        return None, None
    server = MetricsServer(host=settings.metrics_host, port=settings.metrics_port)
    server.start()
    refresher = GaugeRefresher(
        container.refresh_scrape_gauges,
        on_failure=container.mark_scrape_gauges_unknown,
        interval_seconds=settings.metrics_refresh_interval_seconds,
    )
    refresher.start()
    return server, refresher


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    """Install middleware.

    Starlette applies middleware in reverse registration order, so the last one
    added is outermost. ``RequestContextMiddleware`` is registered last so that
    it wraps everything and every log record -- including those from the
    security-headers layer -- carries a request id.
    """
    if settings.cors_allowed_origins:
        # Present for development only. In production ORBIT is served from a
        # single origin (ADR-0009) and configuration validation rejects a
        # non-empty allow-list there.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=settings.is_production)
    # Defence in depth behind SameSite=Lax (ADR-0009). Registered before
    # RequestContextMiddleware so a rejected request still gets a request id
    # in its log line and its response header. The CORS allow-list doubles as
    # the trusted-origin list: it is empty in production (enforced by Settings),
    # and in development it is the origin the frontend is served from -- whose
    # `Origin` a Next.js rewrite proxy can no longer match against `Host`.
    app.add_middleware(CsrfOriginCheckMiddleware, trusted_origins=settings.cors_allowed_origins)
    app.add_middleware(
        RequestContextMiddleware,
        slow_request_threshold_ms=settings.slow_request_threshold_ms,
        # Only a trusted reverse proxy may supply a request id. Accepting one
        # from the internet lets a caller collide their requests with another's
        # in the logs.
        trust_incoming_header=settings.is_production,
    )


def _register_routers(app: FastAPI) -> None:
    # Health endpoints are unversioned: they are an operational contract with the
    # orchestrator, not part of the product API.
    app.include_router(health.router)

    # Product API. Everything under this prefix is versioned and is what the
    # browser reaches through the single-origin proxy (ADR-0009).
    #
    # Every product router binds its route's identifiers (workspace, document)
    # and its operation name into the log context before the handler runs.
    context = [Depends(bind_request_context)]
    app.include_router(meta.router, prefix=API_V1_PREFIX, dependencies=context)
    app.include_router(auth.router, prefix=API_V1_PREFIX, dependencies=context)
    app.include_router(workspaces.router, prefix=API_V1_PREFIX, dependencies=context)
    app.include_router(documents.router, prefix=API_V1_PREFIX, dependencies=context)
    app.include_router(folders.router, prefix=API_V1_PREFIX, dependencies=context)
    app.include_router(tags.router, prefix=API_V1_PREFIX, dependencies=context)
    app.include_router(search.router, prefix=API_V1_PREFIX, dependencies=context)
    app.include_router(conversations.router, prefix=API_V1_PREFIX, dependencies=context)
