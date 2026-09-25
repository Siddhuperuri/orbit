"""The one place an embedding vendor is chosen.

Everything else in ORBIT receives an `EmbeddingProvider` and never learns which
adapter, endpoint, or credential is behind it. Every real provider is wrapped
in the same retry decorator, so a new adapter cannot forget it.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from orbit.core.config import AIProvider, Settings
from orbit.core.logging import get_logger
from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.ports.embeddings import EmbeddingProvider
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from orbit.infrastructure.ai.openai_embeddings import OpenAIEmbeddingProvider
from orbit.infrastructure.ai.resilient_embeddings import (
    EmbeddingRetryPolicy,
    RetryingEmbeddingProvider,
)

logger = get_logger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class EmbeddingBinding:
    provider: EmbeddingProvider
    #: Owned by the binding and closed with it; `None` for the fake.
    http: httpx.AsyncClient | None

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()


def build_embedding_provider(settings: Settings) -> EmbeddingBinding:
    match settings.ai_provider:
        case AIProvider.FAKE:
            if settings.embedding_model:
                # Deliberately not honoured: see fake_embeddings.py.
                logger.info(
                    "embedding.fake_ignores_configured_model",
                    configured_model=settings.embedding_model,
                )
            return EmbeddingBinding(
                provider=FakeEmbeddingProvider(
                    dimensions=settings.embedding_dimensions,
                    max_batch_size=settings.embedding_batch_size,
                    max_batch_tokens=settings.embedding_max_batch_tokens,
                ),
                http=None,
            )
        case AIProvider.OPENAI:
            # Presence of all three is guaranteed by Settings validation.
            assert settings.openai_api_key is not None  # noqa: S101
            assert settings.openai_base_url is not None  # noqa: S101
            assert settings.embedding_model is not None  # noqa: S101
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    settings.embedding_request_timeout_seconds, connect=_CONNECT_TIMEOUT_SECONDS
                )
            )
            adapter = OpenAIEmbeddingProvider(
                client,
                base_url=str(settings.openai_base_url),
                api_key=settings.openai_api_key.get_secret_value(),
                space=EmbeddingSpace(
                    model=settings.embedding_model, dimensions=settings.embedding_dimensions
                ),
                max_batch_size=settings.embedding_batch_size,
                max_batch_tokens=settings.embedding_max_batch_tokens,
            )
            return EmbeddingBinding(
                provider=RetryingEmbeddingProvider(
                    adapter,
                    EmbeddingRetryPolicy(
                        max_retries=settings.embedding_max_retries,
                        base_delay_seconds=settings.embedding_retry_base_seconds,
                        max_wait_seconds=settings.embedding_retry_max_wait_seconds,
                    ),
                ),
                http=client,
            )
