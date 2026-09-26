"""Embedding providers: the deterministic fake, the OpenAI-compatible adapter's
classification of every response shape against a mocked transport, and the
retry decorator every real provider is wrapped in. No network anywhere."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

import httpx
import pytest

from orbit.composition.embeddings import build_embedding_provider
from orbit.core.config import AIProvider
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import (
    AIProviderResponseInvalidError,
    AIProviderUnavailableError,
    ConfigurationError,
)
from orbit.infrastructure.ai.fake_embeddings import FAKE_EMBEDDING_MODEL, FakeEmbeddingProvider
from orbit.infrastructure.ai.openai_embeddings import OpenAIEmbeddingProvider
from orbit.infrastructure.ai.resilient_embeddings import (
    EmbeddingRetryPolicy,
    RetryingEmbeddingProvider,
)
from tests.conftest import build_settings


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return float(sum(x * y for x, y in zip(left, right, strict=True)))


class TestFakeEmbeddings:
    async def test_vectors_are_deterministic_normalized_and_sized(self) -> None:
        provider = FakeEmbeddingProvider(dimensions=1536)
        first = await provider.embed_documents(["token rotation"])
        again = await FakeEmbeddingProvider(dimensions=1536).embed_documents(["token rotation"])
        assert list(first[0]) == list(again[0]), "identical across provider instances"
        assert len(first[0]) == 1536
        assert math.isclose(math.hypot(*first[0]), 1.0, rel_tol=1e-5)

    async def test_query_and_document_vectors_for_the_same_text_agree(self) -> None:
        provider = FakeEmbeddingProvider(dimensions=64)
        (document,) = await provider.embed_documents(["refresh token rotation"])
        assert list(await provider.embed_query("refresh token rotation")) == list(document)

    async def test_shared_words_mean_higher_similarity(self) -> None:
        provider = FakeEmbeddingProvider(dimensions=256)
        base, near, far = await provider.embed_documents(
            [
                "refresh token rotation policy",
                "token rotation policy details",
                "banana orchard harvest",
            ]
        )
        assert _cosine(base, near) > _cosine(base, far)

    async def test_text_without_words_still_gets_a_unit_vector(self) -> None:
        (vector,) = await FakeEmbeddingProvider(dimensions=8).embed_documents(["!!! ???"])
        assert math.isclose(math.hypot(*vector), 1.0, rel_tol=1e-5)

    async def test_a_different_model_id_is_a_different_space_with_unrelated_vectors(
        self,
    ) -> None:
        text = "incident response escalation matrix"
        v1 = FakeEmbeddingProvider(dimensions=256).vector(text)
        v2 = FakeEmbeddingProvider(dimensions=256, model_id="orbit-fake-embedding-v2").vector(text)
        assert (
            FakeEmbeddingProvider(dimensions=256).space
            != FakeEmbeddingProvider(dimensions=256, model_id="orbit-fake-embedding-v2").space
        )
        # Vectors from two models of the same width are not comparable; the
        # fake reproduces that rather than hiding it.
        assert _cosine(v1, v2) < 0.5

    def test_the_fake_never_records_a_real_model_name(self) -> None:
        settings = build_settings(
            ai_provider=AIProvider.FAKE, embedding_model="text-embedding-3-small"
        )
        binding = build_embedding_provider(settings)
        assert binding.provider.space == EmbeddingSpace(
            model=FAKE_EMBEDDING_MODEL, dimensions=settings.embedding_dimensions
        )
        assert binding.http is None

    def test_batching_bounds_come_from_configuration(self) -> None:
        settings = build_settings(embedding_batch_size=7, embedding_max_batch_tokens=5000)
        provider = build_embedding_provider(settings).provider
        assert (provider.max_batch_size, provider.max_batch_tokens) == (7, 5000)


# ---------------------------------------------------------------------------
# OpenAI-compatible adapter
# ---------------------------------------------------------------------------

SPACE = EmbeddingSpace(model="text-embedding-3-small", dimensions=3)
BASE_URL = "https://embeddings.example.test/v1/"


def _provider(
    handler: httpx.MockTransport, space: EmbeddingSpace = SPACE
) -> OpenAIEmbeddingProvider:
    return OpenAIEmbeddingProvider(
        httpx.AsyncClient(transport=handler),
        base_url=BASE_URL,
        api_key="sk-test",
        space=space,
        max_batch_size=96,
        max_batch_tokens=100_000,
    )


def _ok(vectors: list[list[float]], *, reverse: bool = False) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert str(request.url) == "https://embeddings.example.test/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer sk-test"
        assert body["model"] == "text-embedding-3-small"
        assert body["dimensions"] == 3
        data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
        return httpx.Response(200, json={"data": list(reversed(data)) if reverse else data})

    return httpx.MockTransport(handle)


class TestOpenAIEmbeddings:
    async def test_endpoint_model_and_width_all_come_from_configuration(self) -> None:
        provider = _provider(_ok([[1, 0, 0]]))
        assert await provider.embed_documents(["a"]) == [[1, 0, 0]]
        assert provider.space == SPACE

    async def test_vectors_are_paired_by_index_even_if_the_response_is_reordered(self) -> None:
        provider = _provider(_ok([[1, 0, 0], [0, 1, 0]], reverse=True))
        assert await provider.embed_documents(["a", "b"]) == [[1, 0, 0], [0, 1, 0]]

    async def test_a_query_is_one_vector(self) -> None:
        assert await _provider(_ok([[0, 0, 1]])).embed_query("q") == [0, 0, 1]

    async def test_empty_input_makes_no_call(self) -> None:
        def fail(_: httpx.Request) -> httpx.Response:
            raise AssertionError("no request expected")

        assert await _provider(httpx.MockTransport(fail)).embed_documents([]) == []

    @pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503])
    async def test_rate_limits_timeouts_and_server_errors_are_transient(self, status: int) -> None:
        provider = _provider(httpx.MockTransport(lambda _: httpx.Response(status, json={})))
        with pytest.raises(AIProviderUnavailableError) as caught:
            await provider.embed_documents(["a"])
        assert not isinstance(caught.value, AIProviderResponseInvalidError)

    async def test_retry_after_is_carried_for_the_retry_layer(self) -> None:
        provider = _provider(
            httpx.MockTransport(
                lambda _: httpx.Response(429, headers={"retry-after": "2.5"}, json={})
            )
        )
        with pytest.raises(AIProviderUnavailableError) as caught:
            await provider.embed_documents(["a"])
        assert caught.value.context["retry_after_seconds"] == 2.5

    @pytest.mark.parametrize("header", ["soon", "-1", "nan"])
    async def test_an_unusable_retry_after_is_ignored(self, header: str) -> None:
        provider = _provider(
            httpx.MockTransport(lambda _: httpx.Response(503, headers={"retry-after": header}))
        )
        with pytest.raises(AIProviderUnavailableError) as caught:
            await provider.embed_documents(["a"])
        assert caught.value.context["retry_after_seconds"] is None

    async def test_network_failures_are_transient(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(AIProviderUnavailableError):
            await _provider(httpx.MockTransport(boom)).embed_documents(["a"])

    @pytest.mark.parametrize("status", [401, 403, 404])
    async def test_credentials_and_unknown_models_are_configuration_defects(
        self, status: int
    ) -> None:
        provider = _provider(httpx.MockTransport(lambda _: httpx.Response(status, json={})))
        with pytest.raises(ConfigurationError):
            await provider.embed_documents(["a"])

    async def test_an_exhausted_quota_is_not_retried_as_a_rate_limit(self) -> None:
        body = {"error": {"code": "insufficient_quota", "message": "billing"}}
        provider = _provider(httpx.MockTransport(lambda _: httpx.Response(429, json=body)))
        with pytest.raises(ConfigurationError):
            await provider.embed_documents(["a"])

    async def test_other_client_errors_do_not_echo_the_response_body(self) -> None:
        body = {"error": {"message": "input contained: the user's private document text"}}
        provider = _provider(httpx.MockTransport(lambda _: httpx.Response(400, json=body)))
        with pytest.raises(RuntimeError) as caught:
            await provider.embed_documents(["a"])
        assert "private document" not in str(caught.value)

    @pytest.mark.parametrize(
        "payload",
        [
            {"data": []},
            {"data": [{"index": 0, "embedding": [1, 0, 0]}, {"index": 0, "embedding": [0, 1, 0]}]},
            {"data": [{"index": 5, "embedding": [1, 0, 0]}, {"index": 1, "embedding": [0, 1, 0]}]},
            {"data": [{"index": 0, "embedding": "nope"}, {"index": 1, "embedding": [0, 1, 0]}]},
            {"unexpected": True},
            ["not", "an", "object"],
        ],
        ids=["empty", "duplicate-index", "index-out-of-range", "non-list", "no-data", "array"],
    )
    async def test_a_response_that_cannot_be_paired_is_rejected_as_invalid(
        self, payload: object
    ) -> None:
        provider = _provider(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
        with pytest.raises(AIProviderResponseInvalidError):
            await provider.embed_documents(["a", "b"])

    async def test_an_omitted_index_is_index_zero(self) -> None:
        # How Google's OpenAI-compatible endpoint sends the first item.
        payload = {"data": [{"embedding": [1, 0, 0]}, {"index": 1, "embedding": [0, 1, 0]}]}
        provider = _provider(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
        assert await provider.embed_documents(["a", "b"]) == [[1, 0, 0], [0, 1, 0]]

    async def test_two_items_without_an_index_are_a_duplicate(self) -> None:
        payload = {"data": [{"embedding": [1, 0, 0]}, {"embedding": [0, 1, 0]}]}
        provider = _provider(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
        with pytest.raises(AIProviderResponseInvalidError):
            await provider.embed_documents(["a", "b"])

    async def test_googles_retry_delay_in_the_body_is_carried_for_the_retry_layer(self) -> None:
        body = [
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {"@type": "type.googleapis.com/google.rpc.QuotaFailure"},
                        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "41s"},
                    ],
                }
            }
        ]
        provider = _provider(httpx.MockTransport(lambda _: httpx.Response(429, json=body)))
        with pytest.raises(AIProviderUnavailableError) as caught:
            await provider.embed_documents(["a"])
        assert caught.value.context["retry_after_seconds"] == 41.0

    async def test_a_non_json_success_is_invalid(self) -> None:
        provider = _provider(httpx.MockTransport(lambda _: httpx.Response(200, text="<html>")))
        with pytest.raises(AIProviderResponseInvalidError):
            await provider.embed_documents(["a"])

    def test_openai_is_wrapped_in_retries_with_configured_policy(self) -> None:
        settings = build_settings(
            ai_provider=AIProvider.OPENAI,
            openai_api_key="sk-test",
            openai_base_url="https://gateway.example.test/v1",
            embedding_model="text-embedding-3-small",
            embedding_max_retries=4,
        )
        binding = build_embedding_provider(settings)
        assert isinstance(binding.provider, RetryingEmbeddingProvider)
        assert binding.provider.space == EmbeddingSpace(
            model="text-embedding-3-small", dimensions=1536
        )
        assert binding.http is not None


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------


class _Scripted:
    """A provider that fails with scripted errors before succeeding."""

    def __init__(self, errors: list[BaseException]) -> None:
        self.errors = errors
        self.calls = 0
        self.space = SPACE
        self.max_batch_size = 10
        self.max_batch_tokens = 1000

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return [[1.0, 0.0, 0.0] for _ in texts]

    async def embed_query(self, text: str) -> Sequence[float]:
        (vector,) = await self.embed_documents([text])
        return vector


def _retrying(
    inner: _Scripted, policy: EmbeddingRetryPolicy | None = None
) -> tuple[RetryingEmbeddingProvider, list[float]]:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return (
        RetryingEmbeddingProvider(
            inner,
            policy
            or EmbeddingRetryPolicy(max_retries=3, base_delay_seconds=1, max_wait_seconds=20),
            sleep=sleep,
            random_value=lambda: 0.5,
        ),
        sleeps,
    )


class TestRetryingEmbeddings:
    async def test_a_brief_outage_is_absorbed_with_growing_jittered_backoff(self) -> None:
        inner = _Scripted([AIProviderUnavailableError("503"), AIProviderUnavailableError("503")])
        provider, sleeps = _retrying(inner)
        assert await provider.embed_documents(["a"]) == [[1.0, 0.0, 0.0]]
        assert inner.calls == 3
        # Equal jitter at random=0.5: 0.75 * ceiling, ceilings 1s then 2s.
        assert sleeps == [0.75, 1.5]

    async def test_the_providers_retry_after_is_honoured_as_a_floor(self) -> None:
        inner = _Scripted([AIProviderUnavailableError("429", retry_after_seconds=3.0)])
        provider, sleeps = _retrying(inner)
        await provider.embed_query("q")
        assert sleeps == [3.5]

    async def test_a_retry_after_beyond_the_cap_is_handed_to_the_job_backoff(self) -> None:
        inner = _Scripted([AIProviderUnavailableError("429", retry_after_seconds=120.0)])
        provider, sleeps = _retrying(inner)
        with pytest.raises(AIProviderUnavailableError):
            await provider.embed_documents(["a"])
        assert (inner.calls, sleeps) == (1, []), "a worker is never held asleep through an outage"

    async def test_retries_are_bounded(self) -> None:
        inner = _Scripted([AIProviderUnavailableError("503") for _ in range(10)])
        provider, sleeps = _retrying(inner, EmbeddingRetryPolicy(max_retries=2))
        with pytest.raises(AIProviderUnavailableError):
            await provider.embed_documents(["a"])
        assert (inner.calls, len(sleeps)) == (3, 2)

    async def test_backoff_never_exceeds_the_cap(self) -> None:
        policy = EmbeddingRetryPolicy(max_retries=10, base_delay_seconds=5, max_wait_seconds=8)
        assert all(policy.delay_before_retry(n, random_value=1.0) <= 8 for n in range(1, 40))

    @pytest.mark.parametrize(
        "error", [ConfigurationError("bad key"), RuntimeError("HTTP 400"), ValueError("bug")]
    )
    async def test_what_retrying_cannot_fix_propagates_at_once(self, error: Exception) -> None:
        inner = _Scripted([error])
        provider, sleeps = _retrying(inner)
        with pytest.raises(type(error)):
            await provider.embed_documents(["a"])
        assert (inner.calls, sleeps) == (1, [])

    async def test_an_invalid_response_is_retried(self) -> None:
        inner = _Scripted([AIProviderResponseInvalidError("NaN")])
        provider, _ = _retrying(inner)
        await provider.embed_documents(["a"])
        assert inner.calls == 2

    def test_the_decorator_exposes_the_inner_space_and_bounds(self) -> None:
        provider, _ = _retrying(_Scripted([]))
        assert (provider.space, provider.max_batch_size, provider.max_batch_tokens) == (
            SPACE,
            10,
            1000,
        )
