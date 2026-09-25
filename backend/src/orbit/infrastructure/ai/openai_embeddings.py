"""OpenAI-compatible embedding provider.

Model, dimensions, endpoint, and key all come from configuration; nothing
vendor-specific is decided here beyond the wire format. Any server that speaks
the `/embeddings` API with the `dimensions` parameter works -- which means the
`text-embedding-3` family and compatible gateways. (`text-embedding-ada-002`
rejects `dimensions` and fails as a request error.)

This adapter makes **one** attempt per call. Retries live in
`RetryingEmbeddingProvider`, once, for every provider (ADR-0020); longer
outages fall through to the job's durable backoff (ADR-0019).

Classification of what comes back:

| Response                          | Error                             | Kind      |
|-----------------------------------|-----------------------------------|-----------|
| timeout, connection failure       | `AIProviderUnavailableError`      | transient |
| 408, 409, 429, 5xx                | `AIProviderUnavailableError`      | transient |
| 429 `insufficient_quota`          | `ConfigurationError`              | defect    |
| 401, 403                          | `ConfigurationError`              | defect    |
| 404 (unknown model or endpoint)   | `ConfigurationError`              | defect    |
| other 4xx                         | `RuntimeError`                    | defect    |
| 200 that cannot be paired/parsed  | `AIProviderResponseInvalidError`  | transient |

Rejected credentials, an exhausted quota, and a wrong model name are not the
document's fault and will not fix themselves on retry: they fail loudly as
defects, and the operator reprocesses once the deployment is fixed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from orbit.domain.embeddings import EmbeddingSpace
from orbit.domain.errors import (
    AIProviderResponseInvalidError,
    AIProviderUnavailableError,
    ConfigurationError,
)
from orbit.infrastructure.ai.openai_http import error_code, retry_after_seconds
from orbit.infrastructure.ai.telemetry import (
    correlation_headers,
    provider_call,
    provider_request_id,
    record_tokens,
)

_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_REQUEST_TIMEOUT = 408
_HTTP_CONFLICT = 409
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR = 500

_TRANSIENT_CLIENT_STATUSES = frozenset({_HTTP_REQUEST_TIMEOUT, _HTTP_CONFLICT})


class OpenAIEmbeddingProvider:
    def __init__(  # noqa: PLR0913 -- all required configuration, all keyword-only
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        api_key: str,
        space: EmbeddingSpace,
        max_batch_size: int,
        max_batch_tokens: int,
    ) -> None:
        self._client = client
        self._endpoint = f"{base_url.rstrip('/')}/embeddings"
        self._api_key = api_key
        self._space = space
        self._max_batch_size = max_batch_size
        self._max_batch_tokens = max_batch_tokens

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    @property
    def max_batch_size(self) -> int:
        return self._max_batch_size

    @property
    def max_batch_tokens(self) -> int:
        return self._max_batch_tokens

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return []
        return await self._request(list(texts), operation="embed_documents")

    async def embed_query(self, text: str) -> Sequence[float]:
        (vector,) = await self._request([text], operation="embed_query")
        return vector

    async def _request(self, texts: list[str], *, operation: str) -> list[list[float]]:
        with provider_call(operation=operation, model=self._space.model):
            try:
                response = await self._client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        **correlation_headers(),
                    },
                    json={
                        "model": self._space.model,
                        "input": texts,
                        "dimensions": self._space.dimensions,
                        "encoding_format": "float",
                    },
                )
            except httpx.TransportError as exc:
                msg = "The embedding provider could not be reached."
                raise AIProviderUnavailableError(msg, error=type(exc).__name__) from exc

            self._raise_for_status(response)
            try:
                payload = response.json()
            except ValueError as exc:
                msg = "The embedding provider returned a malformed response."
                raise AIProviderResponseInvalidError(msg) from exc
            vectors = self._vectors(payload, expected=len(texts))
            record_tokens(operation, input_tokens=_total_tokens(payload), output_tokens=None)
            return vectors

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if not response.is_error:
            return
        request_id = provider_request_id(response)
        if status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
            msg = "The embedding provider rejected ORBIT's credentials."
            raise ConfigurationError(msg, status=status, provider_request_id=request_id)
        if status == _HTTP_NOT_FOUND:
            msg = "The embedding provider does not recognise the configured model or endpoint."
            raise ConfigurationError(
                msg, status=status, model=self._space.model, provider_request_id=request_id
            )
        if status == _HTTP_TOO_MANY_REQUESTS and error_code(response) == "insufficient_quota":
            # Shares 429 with rate limiting but will not clear on its own:
            # retrying for fifteen minutes would only delay the operator.
            msg = "The embedding provider account has exhausted its quota."
            raise ConfigurationError(msg, status=status, provider_request_id=request_id)
        if (
            status == _HTTP_TOO_MANY_REQUESTS
            or status >= _HTTP_SERVER_ERROR
            or status in _TRANSIENT_CLIENT_STATUSES
        ):
            msg = "The embedding provider is unavailable or rate limiting."
            raise AIProviderUnavailableError(
                msg,
                status=status,
                retry_after_seconds=retry_after_seconds(response),
                provider_request_id=request_id,
            )
        # The body may echo input text; only the status is kept.
        msg = f"The embedding provider rejected the request (HTTP {status})."
        raise RuntimeError(msg)

    def _vectors(self, payload: Any, *, expected: int) -> list[list[float]]:  # noqa: ANN401 -- JSON
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != expected:
            msg = "The embedding provider returned a malformed response."
            raise AIProviderResponseInvalidError(msg)
        # Paired by the response's own `index`, never by position: the API
        # does not promise to return items in input order, and a positional
        # zip over a reordered response would attach vectors to the wrong text.
        ordered: list[list[float] | None] = [None] * expected
        for item in data:
            index = item.get("index") if isinstance(item, dict) else None
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if (
                not isinstance(index, int)
                or not 0 <= index < expected
                or ordered[index] is not None
                or not isinstance(embedding, list)
            ):
                msg = "The embedding provider returned a malformed response."
                raise AIProviderResponseInvalidError(msg)
            ordered[index] = embedding
        return [vector for vector in ordered if vector is not None]


def _total_tokens(payload: Any) -> int | None:  # noqa: ANN401 -- JSON
    usage = payload.get("usage") if isinstance(payload, dict) else None
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    return total if isinstance(total, int) and total >= 0 else None
