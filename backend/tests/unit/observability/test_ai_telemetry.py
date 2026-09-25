"""What a call to an AI provider leaves behind: timing, outcome, tokens, and the
ids that connect ORBIT's records to the provider's.

Driven over `httpx.MockTransport` against the real adapters, so the status
codes, headers, and bodies are the ones a provider actually sends.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Callable, Iterator
from typing import cast

import httpx
import pytest

from orbit.core.config import LogFormat
from orbit.core.logging import bind_correlation, clear_correlation, configure_logging
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import (
    AIProviderRateLimitedError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    ConfigurationError,
)
from orbit.domain.llm import ChatRole, LLMMessage, StreamEnd, TextDelta
from orbit.infrastructure.ai.openai_embeddings import OpenAIEmbeddingProvider
from orbit.infrastructure.ai.openai_llm import OpenAILLMProvider
from orbit.infrastructure.ai.telemetry import classify_provider_error
from tests.conftest import build_settings
from tests.unit.observability.helpers import sample

BASE_URL = "https://api.example.test/v1"
SECRET_KEY = "sk-live-do-not-log-this"
DOCUMENT_TEXT = "the confidential merger terms"
SPACE = EmbeddingSpace(model="text-embedding-3-small", dimensions=4)


def _embedding_provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> OpenAIEmbeddingProvider:
    return OpenAIEmbeddingProvider(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url=BASE_URL,
        api_key=SECRET_KEY,
        space=SPACE,
        max_batch_size=16,
        max_batch_tokens=8000,
    )


def _llm(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAILLMProvider:
    return OpenAILLMProvider(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url=BASE_URL,
        api_key=SECRET_KEY,
        model="gpt-4o-mini",
        context_window=128_000,
    )


def _embedding_ok(request: httpx.Request) -> httpx.Response:
    inputs = json.loads(request.content)["input"]
    return httpx.Response(
        200,
        json={
            "data": [{"index": i, "embedding": [0.1, 0.2, 0.3, 0.4]} for i in range(len(inputs))],
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
        },
    )


def _duration(operation: str, outcome: str) -> float:
    return sample(
        "orbit_ai_request_duration_seconds_count",
        provider="openai",
        operation=operation,
        outcome=outcome,
    )


@pytest.fixture
def events() -> Iterator[list[dict[str, object]]]:
    configure_logging(build_settings(log_format=LogFormat.JSON, log_level="DEBUG"))
    captured: list[dict[str, object]] = []

    class Recorder(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if isinstance(record.msg, dict):
                captured.append(record.msg)

    handler = Recorder()
    logging.getLogger().addHandler(handler)
    yield captured
    logging.getLogger().removeHandler(handler)
    clear_correlation()


class TestEmbeddings:
    async def test_a_successful_call_is_timed_and_its_tokens_counted(self) -> None:
        provider = _embedding_provider(_embedding_ok)
        before = _duration("embed_documents", "ok")
        tokens_before = sample("orbit_ai_tokens_total", operation="embed_documents", kind="input")

        await provider.embed_documents(["one", "two"])

        assert _duration("embed_documents", "ok") == before + 1
        assert (
            sample("orbit_ai_tokens_total", operation="embed_documents", kind="input")
            == tokens_before + 7
        )

    async def test_query_and_document_embeddings_are_distinguished(self) -> None:
        provider = _embedding_provider(_embedding_ok)
        before = _duration("embed_query", "ok")
        await provider.embed_query("what is the notice period")
        assert _duration("embed_query", "ok") == before + 1

    @pytest.mark.parametrize(
        ("response", "outcome", "error"),
        [
            (
                httpx.Response(429, headers={"retry-after": "3"}),
                "unavailable",
                AIProviderUnavailableError,
            ),
            (httpx.Response(503), "unavailable", AIProviderUnavailableError),
            (httpx.Response(401), "config_error", ConfigurationError),
            (
                httpx.Response(200, json={"data": []}),
                "invalid_response",
                AIProviderUnavailableError,
            ),
        ],
    )
    async def test_failures_are_classified_into_bounded_outcomes(
        self, response: httpx.Response, outcome: str, error: type[Exception]
    ) -> None:
        provider = _embedding_provider(lambda _request: response)
        before = _duration("embed_documents", outcome)

        with pytest.raises(error):
            await provider.embed_documents(["x"])

        assert _duration("embed_documents", outcome) == before + 1

    async def test_a_transport_failure_is_an_outage_not_a_defect(self) -> None:
        def refuse(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        provider = _embedding_provider(refuse)
        before = _duration("embed_documents", "unavailable")
        with pytest.raises(AIProviderUnavailableError):
            await provider.embed_documents(["x"])
        assert _duration("embed_documents", "unavailable") == before + 1


class TestCorrelation:
    async def test_the_orbit_request_id_is_sent_to_the_provider(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _embedding_ok(request)

        clear_correlation()
        bind_correlation(request_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")
        await _embedding_provider(handler).embed_query("q")

        assert seen[0].headers["X-Client-Request-Id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        clear_correlation()

    async def test_no_header_is_sent_when_there_is_no_request_context(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _embedding_ok(request)

        clear_correlation()
        await _embedding_provider(handler).embed_query("q")
        assert "X-Client-Request-Id" not in seen[0].headers

    async def test_the_providers_own_id_is_captured_on_failure(
        self, events: list[dict[str, object]]
    ) -> None:
        provider = _embedding_provider(
            lambda _request: httpx.Response(503, headers={"x-request-id": "req_provider_abc123"})
        )
        with pytest.raises(AIProviderUnavailableError) as raised:
            await provider.embed_documents(["x"])

        assert raised.value.context["provider_request_id"] == "req_provider_abc123"
        (failure,) = [e for e in events if e["event"] == "ai.request_failed"]
        assert failure["provider_request_id"] == "req_provider_abc123"
        assert failure["status"] == 503
        assert failure["outcome"] == "unavailable"
        assert failure["call"] == "embed_documents"

    async def test_an_absurd_provider_id_is_bounded(self) -> None:
        provider = _embedding_provider(
            lambda _request: httpx.Response(503, headers={"x-request-id": "x" * 5000})
        )
        with pytest.raises(AIProviderUnavailableError) as raised:
            await provider.embed_documents(["x"])
        assert len(str(raised.value.context["provider_request_id"])) <= 128


class TestNothingSensitiveIsRecorded:
    async def test_neither_the_key_nor_the_input_reaches_a_log_or_a_label(
        self, events: list[dict[str, object]]
    ) -> None:
        def echoing_error(request: httpx.Request) -> httpx.Response:
            # Providers sometimes echo the request in an error body.
            return httpx.Response(500, text=f"failed for {request.content.decode()}")

        provider = _embedding_provider(echoing_error)
        with pytest.raises(AIProviderUnavailableError):
            await provider.embed_documents([DOCUMENT_TEXT])

        rendered = json.dumps(events, default=str)
        assert DOCUMENT_TEXT not in rendered
        assert SECRET_KEY not in rendered


class TestLanguageModel:
    @staticmethod
    def _completion(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {"message": {"content": "an answer"}, "finish_reason": "stop"},
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            },
        )

    async def test_a_completion_is_timed_and_its_tokens_counted(self) -> None:
        provider = _llm(self._completion)
        before = _duration("chat_complete", "ok")
        input_before = sample("orbit_ai_tokens_total", operation="chat_complete", kind="input")
        output_before = sample("orbit_ai_tokens_total", operation="chat_complete", kind="output")

        await provider.complete([LLMMessage(ChatRole.USER, "q")], max_tokens=100, temperature=0.0)

        assert _duration("chat_complete", "ok") == before + 1
        assert (
            sample("orbit_ai_tokens_total", operation="chat_complete", kind="input")
            == input_before + 120
        )
        assert (
            sample("orbit_ai_tokens_total", operation="chat_complete", kind="output")
            == output_before + 30
        )

    async def test_a_stream_records_time_to_first_token_and_usage(self) -> None:
        def stream(_request: httpx.Request) -> httpx.Response:
            body = (
                'data: {"choices":[{"delta":{"content":"He"},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"delta":{"content":"llo"},"finish_reason":"stop"}],'
                '"usage":{"prompt_tokens":50,"completion_tokens":2}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, content=body.encode())

        provider = _llm(stream)
        first_before = sample("orbit_llm_first_token_seconds_count")
        stream_before = _duration("chat_stream", "ok")
        output_before = sample("orbit_ai_tokens_total", operation="chat_stream", kind="output")

        events = [
            event
            async for event in provider.stream(
                [LLMMessage(ChatRole.USER, "q")], max_tokens=100, temperature=0.0
            )
        ]

        assert [e.text for e in events if isinstance(e, TextDelta)] == ["He", "llo"]
        assert any(isinstance(e, StreamEnd) for e in events)
        assert sample("orbit_llm_first_token_seconds_count") == first_before + 1
        assert _duration("chat_stream", "ok") == stream_before + 1
        assert (
            sample("orbit_ai_tokens_total", operation="chat_stream", kind="output")
            == output_before + 2
        )

    async def test_rate_limiting_and_timeouts_are_told_apart_from_outages(self) -> None:
        limited = _llm(lambda _r: httpx.Response(429, headers={"retry-after": "2"}))
        before_limited = _duration("chat_complete", "rate_limited")
        with pytest.raises(AIProviderRateLimitedError):
            await limited.complete([LLMMessage(ChatRole.USER, "q")], max_tokens=1, temperature=0)
        assert _duration("chat_complete", "rate_limited") == before_limited + 1

        def time_out(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out")

        before_timeout = _duration("chat_complete", "timeout")
        with pytest.raises(AIProviderTimeoutError):
            await _llm(time_out).complete(
                [LLMMessage(ChatRole.USER, "q")], max_tokens=1, temperature=0
            )
        assert _duration("chat_complete", "timeout") == before_timeout + 1

    async def test_an_abandoned_stream_is_cancelled_not_an_error(self) -> None:
        """A client that goes away mid-answer says nothing about the provider."""

        def stream(_request: httpx.Request) -> httpx.Response:
            body = (
                'data: {"choices":[{"delta":{"content":"He"},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"delta":{"content":"llo"},"finish_reason":null}]}\n\n'
            )
            return httpx.Response(200, content=body.encode())

        provider = _llm(stream)
        before = _duration("chat_stream", "cancelled")
        generator = provider.stream([LLMMessage(ChatRole.USER, "q")], max_tokens=10, temperature=0)
        await generator.__anext__()
        await cast("AsyncGenerator[object, None]", generator).aclose()

        assert _duration("chat_stream", "cancelled") == before + 1


class TestClassification:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (AIProviderRateLimitedError("x"), "rate_limited"),
            (AIProviderTimeoutError("x"), "timeout"),
            (AIProviderUnavailableError("x"), "unavailable"),
            (ConfigurationError("x"), "config_error"),
            (RuntimeError("x"), "error"),
            (GeneratorExit(), "cancelled"),
        ],
    )
    def test_each_error_maps_to_one_bounded_label(
        self, error: BaseException, expected: str
    ) -> None:
        assert classify_provider_error(error) == expected
