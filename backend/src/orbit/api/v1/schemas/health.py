"""Health and readiness response contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from orbit.domain.ports.health import DependencyStatus


class LivenessResponse(BaseModel):
    """Answer to "is this process alive?"

    Deliberately carries no dependency information: liveness must not depend on
    anything external (docs/decisions/0015-observability-strategy.md).
    """

    model_config = ConfigDict(frozen=True)

    status: str = Field(default="ok", examples=["ok"])
    service: str
    version: str


class DependencyHealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(examples=["database"])
    status: DependencyStatus
    latency_ms: float
    critical: bool = Field(
        description=(
            "Whether the instance can serve traffic without this dependency. A "
            "down non-critical dependency degrades a feature but keeps the "
            "instance in rotation."
        ),
    )
    detail: str | None = Field(
        default=None,
        description=(
            "Generic failure summary. Never carries driver messages, hostnames, or credentials."
        ),
    )


class ReadinessResponse(BaseModel):
    """Answer to "can this process serve traffic?"

    Returned with 200 when ready and 503 when not, so an orchestrator can act on
    the status code alone while a human gets the per-dependency breakdown.
    """

    model_config = ConfigDict(frozen=True)

    status: str = Field(
        examples=["ready", "degraded", "not_ready"],
        description=(
            "`ready`: everything is up. `degraded`: serving, but a non-critical "
            "dependency is down (200). `not_ready`: a critical dependency is "
            "down; take the instance out of rotation (503)."
        ),
    )
    dependencies: list[DependencyHealthResponse]
