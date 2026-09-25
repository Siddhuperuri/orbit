"""Processing endpoints: status polling and reprocessing.

Wired to the real use cases over the in-memory pipeline harness, so the HTTP
contract is tested against genuine job history rather than hand-built stubs.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.api.deps import (
    get_access_context,
    get_current_user,
    get_get_processing_status,
    get_reprocess_document,
)
from orbit.domain.access import AccessContext, Role
from orbit.domain.errors import AIProviderUnavailableError
from orbit.domain.models.entities import Document, User
from tests.unit.processing.harness import Pipeline, build_pipeline


@pytest.fixture
def pipeline() -> Pipeline:
    return asyncio.run(build_pipeline(max_attempts=2))


@pytest.fixture
def wired(app: FastAPI, pipeline: Pipeline) -> Iterator[Callable[[Role], None]]:
    user = User(
        id=pipeline.ctx.user_id,
        email="owner@example.com",
        full_name="Owner",
        is_active=True,
        token_epoch=0,
        created_at=pipeline.clock.now(),
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_get_processing_status] = lambda: pipeline.status
    app.dependency_overrides[get_reprocess_document] = lambda: pipeline.reprocess

    def as_role(role: Role) -> None:
        async def context() -> AccessContext:
            return AccessContext(
                user_id=pipeline.ctx.user_id, workspace_id=pipeline.ctx.workspace_id, role=role
            )

        app.dependency_overrides[get_access_context] = context

    yield as_role
    app.dependency_overrides.clear()


def _url(pipeline: Pipeline, document: Document, suffix: str) -> str:
    return f"/api/v1/workspaces/{pipeline.ctx.workspace_id}/documents/{document.id}/{suffix}"


def _exhausted_document(pipeline: Pipeline) -> Document:
    pipeline.embedder.failures.extend(AIProviderUnavailableError("503") for _ in range(2))
    document = asyncio.run(pipeline.upload("notes.txt", b"Some processable text. " * 20))
    asyncio.run(pipeline.drain())
    return document


class TestProcessingStatus:
    def test_reports_status_and_every_attempt_without_operator_detail(
        self, client: TestClient, pipeline: Pipeline, wired: Callable[[Role], None]
    ) -> None:
        wired(Role.VIEWER)
        document = _exhausted_document(pipeline)

        response = client.get(_url(pipeline, document, "processing"))

        assert response.status_code == 200
        body = response.json()
        assert body["document_id"] == str(document.id)
        version = body["version"]
        assert version["status"] == "failed"
        assert version["failure_code"] == "PROCESSING_RETRIES_EXHAUSTED"
        assert "Nothing is wrong with the file" in version["failure_reason"]
        assert version["processed_at"] is not None
        attempts = body["attempts"]
        assert [a["attempt"] for a in attempts] == [2, 1]
        assert {a["failure_kind"] for a in attempts} == {"transient"}
        assert all(a["error_code"] == "AI_PROVIDER_UNAVAILABLE" for a in attempts)
        # Operator detail never reaches the client.
        assert "error_message" not in attempts[0] and "worker_id" not in attempts[0]
        assert "503" not in response.text

    def test_pending_document_reports_a_queued_attempt(
        self, client: TestClient, pipeline: Pipeline, wired: Callable[[Role], None]
    ) -> None:
        wired(Role.VIEWER)
        document = asyncio.run(pipeline.upload("a.txt", b"queued, not processed yet"))
        body = client.get(_url(pipeline, document, "processing")).json()
        assert body["version"]["status"] == "pending"
        assert body["version"]["failure_reason"] is None
        assert [(a["status"], a["stage"]) for a in body["attempts"]] == [("queued", None)]

    def test_unknown_document_is_404(
        self, client: TestClient, pipeline: Pipeline, wired: Callable[[Role], None]
    ) -> None:
        wired(Role.VIEWER)
        url = f"/api/v1/workspaces/{pipeline.ctx.workspace_id}/documents/{uuid.uuid4()}/processing"
        response = client.get(url)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestReprocess:
    def test_a_failed_document_is_accepted_for_reprocessing(
        self, client: TestClient, pipeline: Pipeline, wired: Callable[[Role], None]
    ) -> None:
        wired(Role.MEMBER)
        document = _exhausted_document(pipeline)

        response = client.post(_url(pipeline, document, "reprocess"))

        assert response.status_code == 202
        assert response.json()["current_version"]["status"] == "pending"
        assert len(pipeline.queue.dispatched) == 1

    def test_a_document_that_has_not_failed_is_a_conflict(
        self, client: TestClient, pipeline: Pipeline, wired: Callable[[Role], None]
    ) -> None:
        wired(Role.MEMBER)
        document = asyncio.run(pipeline.upload("a.txt", b"still pending"))
        response = client.post(_url(pipeline, document, "reprocess"))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    def test_a_viewer_may_not_reprocess(
        self, client: TestClient, pipeline: Pipeline, wired: Callable[[Role], None]
    ) -> None:
        wired(Role.VIEWER)
        document = _exhausted_document(pipeline)
        response = client.post(_url(pipeline, document, "reprocess"))
        assert response.status_code == 403
