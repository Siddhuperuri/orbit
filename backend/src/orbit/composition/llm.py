"""The one place a language-model vendor is chosen.

Everything else receives an `LLMProvider` and never learns which adapter,
endpoint, or credential is behind it. Every real provider is wrapped in the
same resilience decorator -- retries and the circuit breaker -- so a new
adapter cannot forget them.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from orbit.core.config import AIProvider, Settings
from orbit.domain.ports.llm import LLMProvider
from orbit.infrastructure.ai.fake_llm import FakeLLMProvider
from orbit.infrastructure.ai.openai_llm import OpenAILLMProvider
from orbit.infrastructure.ai.resilient_llm import (
    CircuitBreaker,
    LLMRetryPolicy,
    ResilientLLMProvider,
)

_CONNECT_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class LLMBinding:
    provider: LLMProvider
    #: Owned by the binding and closed with it; `None` for the fake.
    http: httpx.AsyncClient | None
    #: The provider's circuit breaker, exposed so readiness can report an open
    #: circuit without making a paid call. `None` for the fake.
    circuit: CircuitBreaker | None = None

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()


def build_llm_provider(settings: Settings) -> LLMBinding:
    match settings.ai_provider:
        case AIProvider.FAKE:
            return LLMBinding(
                provider=FakeLLMProvider(context_window=settings.llm_context_window), http=None
            )
        case AIProvider.OPENAI:
            # Presence of both is guaranteed by Settings validation.
            assert settings.openai_api_key is not None  # noqa: S101
            assert settings.openai_base_url is not None  # noqa: S101
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    settings.llm_request_timeout_seconds, connect=_CONNECT_TIMEOUT_SECONDS
                )
            )
            adapter = OpenAILLMProvider(
                client,
                base_url=str(settings.openai_base_url),
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.llm_model,
                context_window=settings.llm_context_window,
            )
            circuit = CircuitBreaker(
                failure_threshold=settings.llm_circuit_failure_threshold,
                reset_after_seconds=settings.llm_circuit_reset_seconds,
            )
            return LLMBinding(
                provider=ResilientLLMProvider(
                    adapter,
                    LLMRetryPolicy(
                        max_retries=settings.llm_max_retries,
                        base_delay_seconds=settings.llm_retry_base_seconds,
                        max_wait_seconds=settings.llm_retry_max_wait_seconds,
                    ),
                    circuit,
                ),
                http=client,
                circuit=circuit,
            )
