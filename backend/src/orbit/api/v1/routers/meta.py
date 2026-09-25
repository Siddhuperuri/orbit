"""Service metadata.

Distinct from ``/healthz`` on purpose. Health endpoints are an unversioned
operational contract with the orchestrator; this is a versioned product API that
the frontend calls, and it is what lets a support conversation start with "which
build are you on" rather than a guess.

It exposes nothing an authenticated user could not already infer, and nothing
about dependencies -- dependency state belongs to ``/readyz``, which is not
reachable from the browser.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from orbit import __version__
from orbit.api.deps import SettingsDep
from orbit.core.uploads import ACCEPTED_EXTENSIONS

router = APIRouter(tags=["meta"])


class UploadPolicy(BaseModel):
    """What an upload may be, so a client can refuse a file before sending it.

    Advisory only: the server re-checks every one of these, and sniffs the real
    type from the bytes. Exposing them means the browser states the *actual*
    limit instead of a hardcoded copy that drifts from the deployment.
    """

    model_config = ConfigDict(frozen=True)

    max_bytes: int = Field(examples=[52_428_800])
    extensions: list[str] = Field(examples=[[".md", ".pdf", ".txt"]])


class MetaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str = Field(examples=["orbit-api"])
    version: str = Field(examples=["0.1.0"])
    environment: str = Field(examples=["development"])
    api_version: str = Field(default="v1")
    uploads: UploadPolicy


@router.get(
    "/meta",
    response_model=MetaResponse,
    summary="Service metadata",
    description="Build and environment identity for the running API.",
)
async def meta(settings: SettingsDep) -> MetaResponse:
    return MetaResponse(
        service=settings.service_name,
        version=__version__,
        environment=settings.env.value,
        uploads=UploadPolicy(
            max_bytes=settings.max_upload_bytes, extensions=list(ACCEPTED_EXTENSIONS)
        ),
    )
