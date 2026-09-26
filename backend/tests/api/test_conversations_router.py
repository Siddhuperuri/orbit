"""Conversations and question answering over HTTP.

Wired to the real use cases over the in-memory pipeline -- including the real
`ResolveAccessContext`, so a request for a workspace the caller does not
belong to passes through the same membership check production uses -- with a
scriptable language model in place of the provider.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.api.deps import (
    get_answer_question,
    get_create_conversation,
    get_current_user,
    get_delete_conversation,
    get_get_conversation,
    get_list_conversations,
    get_list_messages,
    get_resolve_access_context,
)
from orbit.application.access import ResolveAccessContext
from orbit.application.answering.answer_question import AnswerPolicy, AnswerQuestion
from orbit.application.conversations.manage import (
    CreateConversation,
    DeleteConversation,
    GetConversation,
    ListConversations,
    ListMessages,
)
from orbit.application.retrieval.hybrid_search import HybridSearch, SearchPolicy
from orbit.domain.errors import (
    AIProviderRateLimitedError,
    AIProviderUnavailableError,
    DatabaseUnavailableError,
)
from orbit.domain.models.entities import User
from orbit.domain.retrieval import SearchQuery, SearchResponse
from orbit.infrastructure.ai.fake_llm import FakeLLMProvider
from orbit.infrastructure.ai.reranking import PassthroughReranker
from orbit.infrastructure.chunking.tokens import ApproximateTokenCounter
from orbit.infrastructure.processing.failure_classifier import PipelineFailureClassifier
from tests.unit.fakes.scripted_llm import ScriptedLLM
from tests.unit.processing.harness import Pipeline, build_pipeline

LEAVE = b"Parental leave is sixteen weeks at full pay, booked through the HR portal. " * 5


@dataclass
class World:
    pipeline: Pipeline
    llm: ScriptedLLM
    search: HybridSearch


class _DownSearch(HybridSearch):
    def __init__(self) -> None:
        pass

    async def execute(
        self,
        ctx: object,
        query: SearchQuery,
        *,
        client_ip: str | None = None,
        metered: bool = True,
    ) -> SearchResponse:
        msg = "down"
        raise DatabaseUnavailableError(msg)


@pytest.fixture
def world() -> World:
    pipeline = asyncio.run(build_pipeline())
    asyncio.run(pipeline.upload("leave.txt", LEAVE))
    asyncio.run(pipeline.drain())
    return World(
        pipeline=pipeline,
        llm=ScriptedLLM(inner=FakeLLMProvider(fabricate_citations=True)),
        search=HybridSearch(pipeline.uow_factory, pipeline.embedder, SearchPolicy(ef_search=40)),
    )


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
def signed_in_as(app: FastAPI, world: World) -> Iterator[Callable[[uuid.UUID], None]]:
    uow = world.pipeline.uow_factory
    app.dependency_overrides[get_resolve_access_context] = lambda: ResolveAccessContext(uow)
    app.dependency_overrides[get_create_conversation] = lambda: CreateConversation(uow)
    app.dependency_overrides[get_list_conversations] = lambda: ListConversations(uow)
    app.dependency_overrides[get_get_conversation] = lambda: GetConversation(uow)
    app.dependency_overrides[get_list_messages] = lambda: ListMessages(uow)
    app.dependency_overrides[get_delete_conversation] = lambda: DeleteConversation(uow)
    app.dependency_overrides[get_answer_question] = lambda: AnswerQuestion(
        uow,
        search=world.search,
        reranker=PassthroughReranker(),
        llm=world.llm,
        token_counter=ApproximateTokenCounter(),
        classifier=PipelineFailureClassifier(),
        policy=AnswerPolicy(),
        clock=world.pipeline.clock,
    )

    def as_user(user_id: uuid.UUID) -> None:
        app.dependency_overrides[get_current_user] = lambda: _user(world.pipeline, user_id)

    yield as_user
    app.dependency_overrides.clear()


def _base(world: World) -> str:
    return f"/api/v1/workspaces/{world.pipeline.ctx.workspace_id}/conversations"


def _conversation(client: TestClient, world: World) -> str:
    response = client.post(_base(world), json={"title": "Leave questions"})
    assert response.status_code == 201
    conversation_id: str = response.json()["id"]
    return conversation_id


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


class TestConversations:
    def test_create_list_get_delete(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        signed_in_as(world.pipeline.ctx.user_id)

        conversation_id = _conversation(client, world)

        listed = client.get(_base(world)).json()
        assert [c["id"] for c in listed["items"]] == [conversation_id]
        assert client.get(f"{_base(world)}/{conversation_id}").json()["title"] == (
            "Leave questions"
        )
        assert client.delete(f"{_base(world)}/{conversation_id}").status_code == 204
        assert client.get(f"{_base(world)}/{conversation_id}").status_code == 404

    def test_a_non_member_gets_404_for_the_workspace(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        signed_in_as(uuid.uuid4())

        response = client.post(_base(world), json={})

        assert response.status_code == 404


class TestAsk:
    def test_answers_with_resolved_citations(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        signed_in_as(world.pipeline.ctx.user_id)
        conversation_id = _conversation(client, world)

        response = client.post(
            f"{_base(world)}/{conversation_id}/messages",
            json={"question": "How long is parental leave?"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["role"] == "assistant"
        assert body["status"] == "complete"
        assert body["grounding"] == "grounded"
        assert body["discarded_citation_count"] == 3
        assert "[S99]" not in body["content"]
        (document,) = world.pipeline.uow_factory.state.documents.values()
        citation = body["citations"][0]
        assert f"[{citation['handle']}]" in body["content"]
        assert citation["document"] == {"id": str(document.id), "title": "leave"}
        assert citation["version"]["version_number"] == 1
        assert citation["chunk"]["id"] is not None
        assert set(citation["location"]) == {
            "page_from",
            "page_to",
            "heading_path",
            "char_start",
            "char_end",
        }
        assert citation["snippet"]
        assert set(body["metrics"]) >= {"retrieval_ms", "generation_ms", "total_ms"}
        assert body["usage"] == {"prompt_tokens": 120, "completion_tokens": 30}

        messages = client.get(f"{_base(world)}/{conversation_id}/messages").json()
        assert [m["role"] for m in messages["items"]] == ["user", "assistant"]
        assert messages["items"][1]["citations"] == body["citations"]

    def test_another_users_conversation_is_404(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        signed_in_as(world.pipeline.ctx.user_id)
        conversation_id = _conversation(client, world)
        signed_in_as(uuid.uuid4())

        response = client.post(
            f"{_base(world)}/{conversation_id}/messages", json={"question": "Leave?"}
        )

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "body", [{"question": ""}, {"question": "x" * 2001}, {"question": "a", "extra": 1}]
    )
    def test_invalid_requests_are_rejected(
        self,
        client: TestClient,
        world: World,
        signed_in_as: Callable[[uuid.UUID], None],
        body: dict[str, object],
    ) -> None:
        signed_in_as(world.pipeline.ctx.user_id)
        conversation_id = _conversation(client, world)

        response = client.post(f"{_base(world)}/{conversation_id}/messages", json=body)

        assert response.status_code == 422

    def test_generation_failure_is_503_with_its_own_code(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        world.llm.failure = AIProviderUnavailableError("503")
        signed_in_as(world.pipeline.ctx.user_id)
        conversation_id = _conversation(client, world)

        response = client.post(
            f"{_base(world)}/{conversation_id}/messages", json={"question": "Leave?"}
        )

        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "GENERATION_FAILED"
        assert error["request_id"]
        # The provider's own error never crosses the boundary.
        assert "503" not in error["message"]

    def test_provider_rate_limiting_is_503_with_retry_after(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        world.llm.failure = AIProviderRateLimitedError("429", retry_after_seconds=2.2)
        signed_in_as(world.pipeline.ctx.user_id)
        conversation_id = _conversation(client, world)

        response = client.post(
            f"{_base(world)}/{conversation_id}/messages", json={"question": "Leave?"}
        )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "GENERATION_RATE_LIMITED"
        assert response.headers["Retry-After"] == "3"

    def test_retrieval_failure_is_503_and_distinct(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        world.search = _DownSearch()
        signed_in_as(world.pipeline.ctx.user_id)
        conversation_id = _conversation(client, world)

        response = client.post(
            f"{_base(world)}/{conversation_id}/messages", json={"question": "Leave?"}
        )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "RETRIEVAL_FAILED"
        assert world.llm.calls == 0


class TestStreaming:
    def test_streams_started_retrieval_deltas_then_done(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        signed_in_as(world.pipeline.ctx.user_id)
        conversation_id = _conversation(client, world)

        response = client.post(
            f"{_base(world)}/{conversation_id}/messages/stream",
            json={"question": "How long is parental leave?"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _events(response.text)
        names = [name for name, _ in events]
        assert names[:2] == ["started", "retrieval"]
        assert set(names[2:-1]) == {"delta"}
        assert names[-1] == "done"
        done = events[-1][1]
        assert done["id"] == events[0][1]["answer_message_id"]
        assert done["grounding"] == "grounded"
        assert "[S99]" not in str(done["content"])
        assert done["citations"]

    def test_failures_before_the_stream_are_plain_http_errors(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        world.search = _DownSearch()
        signed_in_as(world.pipeline.ctx.user_id)
        conversation_id = _conversation(client, world)

        response = client.post(
            f"{_base(world)}/{conversation_id}/messages/stream", json={"question": "Leave?"}
        )

        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] == "RETRIEVAL_FAILED"

    def test_a_generation_failure_mid_stream_is_an_error_event_with_the_partial_answer(
        self, client: TestClient, world: World, signed_in_as: Callable[[uuid.UUID], None]
    ) -> None:
        world.llm.failure = AIProviderUnavailableError("reset")
        world.llm.fail_after_chunks = 2
        signed_in_as(world.pipeline.ctx.user_id)
        conversation_id = _conversation(client, world)

        response = client.post(
            f"{_base(world)}/{conversation_id}/messages/stream", json={"question": "Leave?"}
        )

        assert response.status_code == 200
        name, error = _events(response.text)[-1]
        assert name == "error"
        assert error["code"] == "GENERATION_FAILED"
        assert error["retryable"] is True
        assert error["request_id"]
        answer = error["answer"]
        assert isinstance(answer, dict)
        assert answer["status"] == "partial"
        assert answer["content"]
