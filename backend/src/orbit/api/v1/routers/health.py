"""Liveness and readiness endpoints.

These live outside ``/api/v1`` deliberately. They are an operational contract
with the orchestrator, not a product API, and versioning them would mean an
orchestrator's probe configuration breaks when the product API version changes.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from orbit import __version__
from orbit.api.deps import CheckReadinessDep, SettingsDep
from orbit.api.v1.schemas.health import (
    DependencyHealthResponse,
    LivenessResponse,
    ReadinessResponse,
)
from orbit.domain.ports.health import ReadinessReport

router = APIRouter(tags=["health"])


def _status(report: ReadinessReport) -> str:
    if not report.is_ready:
        return "not_ready"
    return "degraded" if report.is_degraded else "ready"


@router.get(
    "/healthz",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description=(
        "Reports whether the process is running. Checks no external dependency, "
        "so a database or Redis outage does not cause healthy instances to be "
        "restarted."
    ),
)
async def liveness(settings: SettingsDep) -> LivenessResponse:
    return LivenessResponse(service=settings.service_name, version=__version__)


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "At least one dependency is unavailable.",
        }
    },
    description=(
        "Reports whether this instance can serve traffic. Checks PostgreSQL, "
        "Redis, object storage, and the language-model circuit concurrently "
        "under a short timeout. Returns 503 only when a *critical* dependency "
        "(PostgreSQL, the vector schema) is down, so the instance leaves the "
        "load balancer's rotation without being restarted. A down non-critical "
        "dependency returns 200 with status `degraded`: the instance still "
        "serves everything that does not need it."
    ),
)
async def readiness(
    check_readiness: CheckReadinessDep,
    response: Response,
) -> ReadinessResponse:
    report = await check_readiness.execute()

    if not report.is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    # Probe results are already user-safe: the use case reduces every failure to
    # a generic detail string before it reaches this layer.
    return ReadinessResponse(
        status=_status(report),
        dependencies=[
            DependencyHealthResponse(
                name=dependency.name,
                status=dependency.status,
                latency_ms=dependency.latency_ms,
                detail=dependency.detail,
                critical=dependency.critical,
            )
            for dependency in report.dependencies
        ],
    )
