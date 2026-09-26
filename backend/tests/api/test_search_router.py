"""The search endpoint: contract, validation, and the membership boundary.

Wired to the real use cases over the in-memory pipeline -- including the real
`ResolveAccessContext`, so a request for a workspace the caller does not
belong to goes through the same membership check production uses.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.api.deps import get_current_user, get_resolve_access_context, get_search
from orbit.application.access import ResolveAccessContext
from orbit.application.retrieval.hybrid_search import HybridSearch, SearchPolicy
from orbit.domain.models.entities import User
from tests.unit.processing.harness import Pipeline, build_pipeline

LEAVE = b"Parental leave is sixteen weeks at full pay, booked through the HR portal. " * 5


@pytest.fixture
def pipeline() -> Pipeline:
    built = asyncio.run(build_pipeline())
    asyncio.run(built.upload("leave.txt", LEAVE))
    asyncio.run(built.drain())
    return built


def _user(pipeline: Pipeline, user_id: uuid.UUID) -> User:
    return User(
        id=user_id,
        email=f"{user_id.hex[:8]}@example.com",
        full_name="Caller",
        is_active=True,
        token_epoch=0,
        created_at=pipeline.clock.now(),
    )


@pytest.fixture
def signed_in_as(app: FastAPI, pipeline: Pipeline) -> Iterator[Callable[[uuid.UUID], None]]:
    app.dependency_overrides[get_resolve_access_context] = lambda: ResolveAccessContext(
        pipeline.uow_factory
    )
    app.dependency_overrides[get_search] = lambda: HybridSearch(
        pipeline.uow_factory, pipeline.embedder, SearchPolicy(ef_search=40)
    )

    def as_user(user_id: uuid.UUID) -> None:
        app.dependency_overrides[get_current_user] = lambda: _user(pipeline, user_id)

    yield as_user
    app.dependency_overrides.clear()


def _url(workspace_id: uuid.UUID) -> str:
    return f"/api/v1/workspaces/{workspace_id}/search"


class TestSearchEndpoint:
    def test_returns_structured_results(
        self, client: TestClient, pipeline: Pipeline, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        signed_in_as(pipeline.ctx.user_id)

        response = client.post(
            _url(pipeline.ctx.workspace_id), json={"query": "parental leave weeks", "limit": 3}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["query"] == "parental leave weeks"
        assert body["mode"] == "hybrid"
        assert body["retrievers"] == ["lexical", "semantic"]
        assert body["degraded"] is None
        assert body["fusion"] == {"algorithm": "reciprocal_rank_fusion", "k": 60}
        top = body["results"][0]
        assert set(top) == {
            "rank",
            "matched_by",
            "document",
            "version",
            "chunk",
            "location",
            "relevance",
        }
        assert top["rank"] == 1 and top["matched_by"] == "both"
        assert top["document"]["title"] == "leave"
        assert top["version"]["version_number"] == 1
        assert "Parental leave" in top["chunk"]["text"]
        assert set(top["location"]) == {
            "page_from",
            "page_to",
            "heading_path",
            "char_start",
            "char_end",
        }
        assert top["relevance"]["lexical"]["rank"] == 1
        assert top["relevance"]["semantic"]["rank"] == 1

    def test_a_non_member_gets_not_found_not_results(
        self, client: TestClient, pipeline: Pipeline, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        signed_in_as(uuid.uuid4())

        response = client.post(_url(pipeline.ctx.workspace_id), json={"query": "parental leave"})

        assert response.status_code == 404
        assert "Parental" not in response.text

    @pytest.mark.parametrize(
        "body",
        [
            {"query": ""},
            {"query": "leave", "limit": 0},
            {"query": "leave", "limit": 51},
            {"query": "leave", "mode": "fuzzy"},
            {"query": "leave", "document_ids": []},
            {"query": "leave", "workspace_id": str(uuid.uuid4())},
        ],
        ids=["empty", "limit-zero", "limit-high", "unknown-mode", "empty-filter", "extra-field"],
    )
    def test_invalid_requests_are_rejected(
        self,
        client: TestClient,
        pipeline: Pipeline,
        signed_in_as: Callable[[uuid.UUID], None],
        body: dict[str, object],
    ) -> None:
        signed_in_as(pipeline.ctx.user_id)
        response = client.post(_url(pipeline.ctx.workspace_id), json=body)
        assert response.status_code == 422

    def test_a_whitespace_only_query_is_a_validation_error(
        self, client: TestClient, pipeline: Pipeline, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        signed_in_as(pipeline.ctx.user_id)
        response = client.post(_url(pipeline.ctx.workspace_id), json={"query": "   "})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
