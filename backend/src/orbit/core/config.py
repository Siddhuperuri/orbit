"""Application configuration.

Every environment-specific value enters the process here and nowhere else. No
module reads ``os.environ`` directly, so the full set of things that can vary
between environments is enumerable by reading one file.

Validation runs at import time and is deliberately fatal. A process that refuses
to start is far cheaper to diagnose than one that starts and behaves subtly
wrongly -- a 32-character secret key in production, or an embedding dimension
that disagrees with the database schema, produces damage that is silent until it
is expensive.
"""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Mirrors of `orbit.domain.embeddings` limits. `core` sits below `domain` in the
# layering contract and cannot import them; a unit test keeps the copies equal.
MAX_EMBEDDING_DIMENSIONS = 2000
MAX_EMBEDDING_MODEL_ID_LENGTH = 128

# Below this length an HS256 signing key is brute-forceable; the JWA spec
# requires a key at least as long as the hash output (32 bytes for SHA-256).
MIN_SECRET_KEY_LENGTH = 32


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"

    @property
    def is_production(self) -> bool:
        return self is Environment.PRODUCTION


class LogFormat(StrEnum):
    CONSOLE = "console"
    JSON = "json"


class AIProvider(StrEnum):
    FAKE = "fake"
    OPENAI = "openai"


class EmailProvider(StrEnum):
    """How outbound mail leaves the process.

    There is deliberately no `fake` member. A fake sender that silently
    swallows password-reset mail is the single most dangerous thing this
    setting could offer: everything looks healthy, and no user can ever
    recover an account. The two honest options are "not configured, so
    requesting one fails loudly" and "written to the log so a developer can
    copy the link" -- and the latter is refused in production, because a
    reset link in a log file is a credential in a log file.
    """

    #: Default. Raises `EmailDeliveryError` on every send.
    UNCONFIGURED = "unconfigured"
    #: Development only: logs that a message *would* have been sent.
    CONSOLE = "console"


class Settings(BaseSettings):
    """Runtime configuration, populated from ``ORBIT_``-prefixed variables."""

    model_config = SettingsConfigDict(
        env_prefix="ORBIT_",
        # The repository keeps one `.env` at its root, but every npm script
        # runs from `backend/` -- so the parent is read too. Later files win,
        # so a `backend/.env` can still override. Missing files are skipped,
        # and real environment variables take precedence over both.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        # Unknown ORBIT_* variables are ignored rather than fatal: a deployment
        # may legitimately carry variables for a newer or older release during a
        # rolling deploy, and refusing to boot on those would block rollback.
        extra="ignore",
        case_sensitive=False,
    )

    # -- Runtime -------------------------------------------------------------
    env: Environment = Environment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    service_name: str = "orbit-api"

    # -- Database ------------------------------------------------------------
    database_url: PostgresDsn
    test_database_url: PostgresDsn | None = None
    db_pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    db_max_overflow: Annotated[int, Field(ge=0, le=100)] = 5
    db_pool_timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 10
    db_echo: bool = False
    # Server-side ceiling on one statement, applied to every connection this
    # process opens. Without it, a pathological query -- a vector scan that
    # misses the index, a lock wait behind a long transaction -- holds a
    # pooled connection until the client gives up, and a handful of those
    # exhaust the pool and stall every other request. PostgreSQL cancelling
    # the statement is the only limit that works regardless of whether the
    # caller is still waiting. Must exceed the slowest legitimate query;
    # re-index batches run in the worker, which sets its own.
    db_statement_timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 15
    # The worker's equivalent. Far longer, because its statements legitimately
    # are: a re-index batch writes 500 vectors, and the recovery sweep scans
    # for abandoned jobs. Bounded all the same -- a worker statement that
    # outlives the task's own hard time limit can only be holding locks for a
    # child that is about to be killed.
    worker_db_statement_timeout_seconds: Annotated[int, Field(ge=1, le=3600)] = 120

    # -- Redis ---------------------------------------------------------------
    redis_url: RedisDsn
    celery_broker_url: RedisDsn
    celery_result_backend: RedisDsn
    # Bounded so an unreachable or wedged Redis cannot occupy a request worker
    # indefinitely. These are short on purpose: every Redis call ORBIT makes
    # is a single O(1) command against a local-network cache, so a command
    # that has not answered in a second or two is not slow, it is broken --
    # and every caller degrades safely on failure (ADR-0024).
    redis_connect_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 2.0
    redis_command_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 2.0
    # Query-vector cache (ADR-0024). Keyed by workspace, embedding space, and
    # the hash of the query text; holds the output of a pure function, so a
    # hit can never be stale in the way a cached document list can.
    query_vector_cache_enabled: bool = True
    # Long enough to cover a person refining a search and asking follow-up
    # questions about the same phrasing; short enough that a stale entry after
    # a model change costs one window rather than a day. The embedding space
    # is part of the key, so a model change invalidates immediately anyway.
    query_vector_cache_ttl_seconds: Annotated[int, Field(ge=10, le=86_400)] = 3600

    # -- Object storage ------------------------------------------------------
    # Empty endpoint means real AWS S3; MinIO and other S3-compatible stores
    # need an explicit endpoint and path-style addressing.
    s3_endpoint_url: AnyHttpUrl | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_force_path_style: bool = True
    # Bounded so a hung storage endpoint cannot occupy a request worker for
    # the lifetime of the request. The read timeout is per socket read, not
    # per operation: a 50 MB multipart upload is many reads, each of which
    # must individually progress within the window, so this does not cap how
    # long a large but healthy upload may take.
    s3_connect_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 3.0
    s3_read_timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 10.0
    # Attempts botocore makes internally before raising. Deliberately small:
    # the caller's policy -- the upload's own error path, or the processing
    # job's durable backoff -- is the layer that owns retrying, and a deep
    # SDK-level retry only lengthens the wait before that layer is reached.
    s3_max_attempts: Annotated[int, Field(ge=1, le=10)] = 2

    # -- Security ------------------------------------------------------------
    secret_key: str
    access_token_ttl_seconds: Annotated[int, Field(ge=60, le=3600)] = 900
    refresh_token_ttl_seconds: Annotated[int, Field(ge=3600)] = 2_592_000
    # `NoDecode` suppresses pydantic-settings' default JSON decoding for complex
    # types. Without it, `ORBIT_CORS_ALLOWED_ORIGINS=http://localhost:3000` is
    # parsed as JSON, fails, and the process cannot start -- from the exact value
    # .env.example documents. The validator below accepts the comma-separated
    # form that dotenv files and shell exports can actually express.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # -- Brute-force protection ----------------------------------------------
    # Two independent limits guard every credential check (domain/ports/
    # rate_limiter.py): per account and per client address. Defaults are
    # deliberately generous enough that a person fumbling a password never
    # notices, and tight enough that an automated guesser does immediately.
    login_rate_limit_per_account: Annotated[int, Field(ge=1, le=1000)] = 10
    login_rate_limit_per_ip: Annotated[int, Field(ge=1, le=10_000)] = 50
    login_rate_limit_window_seconds: Annotated[int, Field(ge=10, le=86_400)] = 900
    # Account-lifecycle endpoints (reset request, verification resend) are
    # limited far harder: they cost an outbound email, so an unlimited one is a
    # free way to use ORBIT to spam a third party's inbox.
    account_email_rate_limit_per_account: Annotated[int, Field(ge=1, le=100)] = 3
    account_email_rate_limit_per_ip: Annotated[int, Field(ge=1, le=1000)] = 10
    account_email_rate_limit_window_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3600
    # Uploads are bounded per request by `max_upload_bytes`, but an unlimited
    # *rate* of even small, valid uploads is still a resource-exhaustion path:
    # each one costs a streaming hash, an S3 write, and a database row. This is
    # the account/IP half of that defense; the size ceiling is the other half.
    upload_rate_limit_per_account: Annotated[int, Field(ge=1, le=10_000)] = 60
    upload_rate_limit_per_ip: Annotated[int, Field(ge=1, le=10_000)] = 120
    upload_rate_limit_window_seconds: Annotated[int, Field(ge=10, le=86_400)] = 3600
    # Search is cheap per call and expensive in aggregate: every hybrid query
    # costs a paid embedding request plus a full-text scan and an HNSW probe.
    # Unmetered, it is the cheapest way to spend an operator's AI budget from
    # a valid account, and the cheapest way to saturate the database from one.
    # Looser than chat, because a person refining a query legitimately issues
    # a burst of searches in a way they never issue a burst of questions.
    search_rate_limit_per_account: Annotated[int, Field(ge=1, le=100_000)] = 120
    search_rate_limit_per_ip: Annotated[int, Field(ge=1, le=1_000_000)] = 300
    search_rate_limit_window_seconds: Annotated[int, Field(ge=10, le=86_400)] = 300
    # Whether to believe `X-Forwarded-For`.
    #
    # Defaults to **false**, and the default is the security control: any
    # client can send that header, so trusting it unconditionally lets an
    # attacker give every request a fresh per-IP rate-limit key and write
    # arbitrary addresses into the audit trail. Enable it only where a
    # reverse proxy is known to *overwrite* the header rather than append
    # to it; with the flag off, the socket peer address is used, which is
    # wrong behind a proxy but never attacker-controlled.
    trust_proxy_headers: bool = False

    # -- Email ---------------------------------------------------------------
    email_provider: EmailProvider = EmailProvider.UNCONFIGURED
    email_from_address: str = "no-reply@orbit.local"
    # `{token}` is substituted with the one-time token. These point at the
    # frontend, which posts the token back to the API -- the token never
    # appears in an API URL a proxy or access log would record.
    # S105 reads the field name as a credential; the value is a URL template.
    password_reset_url_template: str = "http://localhost:3000/auth/reset-password?token={token}"  # noqa: S105
    email_verification_url_template: str = "http://localhost:3000/auth/verify-email?token={token}"

    # -- Uploads -------------------------------------------------------------
    max_upload_bytes: Annotated[int, Field(ge=1024, le=1_073_741_824)] = 52_428_800

    # -- Document processing (ADR-0019) -----------------------------------------
    # Attempts per processing run. Only transient failures (a dependency
    # outage, a dead worker) consume retries; a corrupt file fails on attempt 1.
    processing_max_attempts: Annotated[int, Field(ge=1, le=20)] = 5
    processing_retry_base_seconds: Annotated[int, Field(ge=1, le=3600)] = 15
    processing_retry_max_seconds: Annotated[int, Field(ge=1, le=86_400)] = 900
    # A claimed job's lease. Recovery treats a RUNNING job whose lease expired
    # as abandoned by a dead worker. Must outlast the hard time limit in
    # production, or a slow-but-alive job is recovered out from under its
    # worker (safe, because writes are fenced -- but wasted work).
    processing_lease_seconds: Annotated[int, Field(ge=10, le=86_400)] = 960
    # A QUEUED job whose message was last published this long ago is presumed
    # lost by the broker and re-published.
    processing_redelivery_grace_seconds: Annotated[int, Field(ge=10, le=86_400)] = 600
    processing_recovery_interval_seconds: Annotated[int, Field(ge=10, le=3600)] = 60
    # Celery's hard limit SIGKILLs the child; the soft limit raises inside the
    # task first so the failure can be recorded with a reason (ADR-0002).
    processing_task_time_limit_seconds: Annotated[int, Field(ge=30, le=86_400)] = 900
    processing_task_soft_time_limit_seconds: Annotated[int, Field(ge=10, le=86_400)] = 840
    # Hostile-input bounds (ADR-0012). A hard timeout alone admits a document
    # that produces enormous output just under the deadline.
    processing_max_pages: Annotated[int, Field(ge=1, le=100_000)] = 2000
    processing_max_characters: Annotated[int, Field(ge=1000, le=100_000_000)] = 5_000_000
    processing_max_chunks: Annotated[int, Field(ge=1, le=1_000_000)] = 20_000
    # Recycle a worker child after this many kilobytes of RSS.
    worker_max_memory_per_child_kb: Annotated[int, Field(ge=50_000)] = 512_000

    # -- AI (ADR-0007, ADR-0020) ---------------------------------------------
    ai_provider: AIProvider = AIProvider.FAKE
    # A secret: `SecretStr` keeps it out of reprs, tracebacks, and settings
    # dumps. Read with `.get_secret_value()` at the one place that sends it.
    openai_api_key: SecretStr | None = None
    # No default, deliberately. Proxies, regional gateways, and compatible
    # servers all differ here, and a silent fallback to the public endpoint
    # would send document text somewhere the operator did not choose.
    openai_base_url: AnyHttpUrl | None = None

    # The model that produces document and query vectors. Required for a real
    # provider and ignored by `fake`, which has a fixed identity of its own --
    # a fake vector recorded under a real model's name would be accepted as
    # that model's output after the switch, and retrieval would silently
    # return nonsense.
    embedding_model: str | None = None
    # Schema, not a tuning knob: must equal the `chunks.embedding` column
    # width, which a migration fixes. Required, so no deployment inherits a
    # width it never chose. HNSW indexes `vector` up to 2,000 dimensions.
    embedding_dimensions: Annotated[int, Field(ge=1, le=MAX_EMBEDDING_DIMENSIONS)]
    # Request shaping. Both bounds apply; a batch closes at whichever is hit
    # first. Token counts are approximate and err high (ADR-0013).
    embedding_batch_size: Annotated[int, Field(ge=1, le=2048)] = 96
    embedding_max_batch_tokens: Annotated[int, Field(ge=1000, le=1_000_000)] = 100_000
    embedding_request_timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 60
    # In-process retries for one request, before the failure is handed to the
    # job's durable backoff. Absorbs a brief 429 without re-running a whole
    # document; a longer outage still releases the worker (ADR-0020).
    embedding_max_retries: Annotated[int, Field(ge=0, le=10)] = 3
    embedding_retry_base_seconds: Annotated[float, Field(gt=0, le=60)] = 1.0
    # A provider asking to wait longer than this is not waited on in-process.
    embedding_retry_max_wait_seconds: Annotated[float, Field(gt=0, le=300)] = 20.0
    # Chunks per re-index transaction.
    embedding_reindex_batch_size: Annotated[int, Field(ge=1, le=5000)] = 500
    # HNSW candidate list size for dense queries. Tuned against measured
    # recall, not guessed: docs/benchmarks/vector-search.md.
    vector_search_ef_search: Annotated[int, Field(ge=10, le=1000)] = 100
    # Hybrid search (ADR-0021). Candidates each retriever contributes before
    # fusion; deeper than any page of results so a chunk ranked modestly by
    # both retrievers can still win.
    search_candidates_per_retriever: Annotated[int, Field(ge=10, le=200)] = 50
    # `any` (OR) or `all` (AND) over query terms in the full-text retriever.
    # Chosen by measurement: docs/benchmarks/retrieval.md.
    search_lexical_match: Literal["any", "all"] = "any"

    # -- Question answering (ADR-0022) -----------------------------------------
    # The chat model. Ignored by `fake`, which answers as `orbit-fake-llm-v1`.
    llm_model: str = "gpt-4o-mini"
    # Declared, not discovered: the context budget is computed from it.
    llm_context_window: Annotated[int, Field(ge=2048, le=2_000_000)] = 128_000
    # How much a reasoning ("thinking") model may think before answering, sent
    # as the API's `reasoning_effort`. Unset sends nothing, which is what a
    # non-reasoning model such as gpt-4o-mini requires. Reasoning models count
    # their thinking against `answer_max_tokens`, so an unbounded default can
    # spend the whole budget thinking and cut the answer short -- Gemini does
    # exactly that at its default level, hence `low` for it.
    llm_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    # Per request, and between streamed chunks. The answer's overall deadline
    # is `answer_generation_timeout_seconds`.
    llm_request_timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 30
    # In-process retries. Short on purpose: a person is waiting, and a stream
    # is only ever retried before its first token.
    llm_max_retries: Annotated[int, Field(ge=0, le=5)] = 2
    llm_retry_base_seconds: Annotated[float, Field(gt=0, le=10)] = 0.5
    llm_retry_max_wait_seconds: Annotated[float, Field(gt=0, le=60)] = 4.0
    # Consecutive provider failures that open the circuit, and how long it
    # stays open before one probe is let through.
    llm_circuit_failure_threshold: Annotated[int, Field(ge=1, le=100)] = 5
    llm_circuit_reset_seconds: Annotated[float, Field(ge=1, le=3600)] = 30.0

    answer_temperature: Annotated[float, Field(ge=0, le=2)] = 0.1
    # Longest answer, in tokens; reserved out of the context window.
    answer_max_tokens: Annotated[int, Field(ge=64, le=16_000)] = 800
    # Ceiling on retrieved text per prompt. Whole documents are never sent;
    # this bounds cost and keeps marginal chunks out of a large window.
    answer_max_context_tokens: Annotated[int, Field(ge=500, le=100_000)] = 6000
    # Fused candidates retrieved, candidates kept after reranking, and passages
    # that may enter the context.
    answer_retrieval_top_k: Annotated[int, Field(ge=1, le=50)] = 12
    answer_rerank_top_k: Annotated[int, Field(ge=1, le=50)] = 8
    answer_max_sources: Annotated[int, Field(ge=1, le=20)] = 8
    # Earlier messages shown to the model for conversational context.
    answer_history_messages: Annotated[int, Field(ge=0, le=50)] = 6
    answer_max_history_tokens: Annotated[int, Field(ge=0, le=20_000)] = 1500
    # Wall-clock limit on one answer's generation, retries included.
    answer_generation_timeout_seconds: Annotated[float, Field(ge=5, le=600)] = 60.0
    # Every question costs a retrieval and a model call.
    chat_rate_limit_per_account: Annotated[int, Field(ge=1, le=10_000)] = 30
    chat_rate_limit_per_ip: Annotated[int, Field(ge=1, le=100_000)] = 120
    chat_rate_limit_window_seconds: Annotated[int, Field(ge=10, le=86_400)] = 300

    # -- Observability -------------------------------------------------------
    metrics_enabled: bool = True
    # Metrics are served on their own port, never on the public API port
    # (ADR-0015). Loopback by default so a developer laptop exposes nothing;
    # a container must set 0.0.0.0 and rely on the network -- the ingress must
    # not route this port.
    metrics_host: str = "127.0.0.1"
    metrics_port: Annotated[int, Field(ge=1, le=65_535)] = 9100
    # The worker is a separate process on (locally) the same host.
    worker_metrics_port: Annotated[int, Field(ge=1, le=65_535)] = 9101
    # How often queue depth and connection-pool gauges are recomputed. They are
    # read from PostgreSQL, so this bounds the load metrics themselves add.
    metrics_refresh_interval_seconds: Annotated[int, Field(ge=5, le=300)] = 15
    # A statement slower than this is logged (SQL text without parameters).
    db_slow_query_threshold_ms: Annotated[int, Field(ge=10, le=60_000)] = 500
    # A request slower than this is logged at WARNING instead of INFO, so slow
    # traffic is findable by level rather than by a numeric comparison.
    slow_request_threshold_ms: Annotated[int, Field(ge=50, le=120_000)] = 2000

    @property
    def is_production(self) -> bool:
        return self.env.is_production

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, a JSON array, or a real list.

        Environment variables are strings, so the comma-separated form is the
        only one a dotenv file or a shell export can express naturally. A list is
        passed through unchanged so programmatic construction (tests, the
        composition root) stays ergonomic.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            # Tolerate the JSON form, since some deployment tooling emits it.
            decoded: object = json.loads(text)
            return decoded
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _validate_secret_key(self) -> Self:
        if len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            msg = (
                f"ORBIT_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters; "
                f"got {len(self.secret_key)}. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
            raise ValueError(msg)
        if self.is_production and self.secret_key.startswith("REPLACE_ME"):
            msg = "ORBIT_SECRET_KEY is still the placeholder value from .env.example."
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_cors(self) -> Self:
        # ORBIT is served from a single origin (ADR-0009), so a credentialed
        # wildcard is never correct -- and browsers reject it anyway. Failing
        # here turns a silently-broken auth flow into a startup error.
        if "*" in self.cors_allowed_origins:
            msg = (
                "ORBIT_CORS_ALLOWED_ORIGINS may not contain '*': credentialed requests "
                "cannot use a wildcard origin. List exact origins instead."
            )
            raise ValueError(msg)
        if self.is_production and self.cors_allowed_origins:
            msg = (
                "ORBIT_CORS_ALLOWED_ORIGINS must be empty in production. ORBIT is served "
                "from a single origin, so no cross-origin access should be permitted."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_processing(self) -> Self:
        if self.processing_task_soft_time_limit_seconds >= self.processing_task_time_limit_seconds:
            msg = (
                "ORBIT_PROCESSING_TASK_SOFT_TIME_LIMIT_SECONDS must be below the hard limit, "
                "or a timed-out document is killed before its failure can be recorded."
            )
            raise ValueError(msg)
        if self.processing_retry_base_seconds > self.processing_retry_max_seconds:
            msg = "ORBIT_PROCESSING_RETRY_BASE_SECONDS must not exceed the maximum delay."
            raise ValueError(msg)
        if (
            self.is_production
            and self.processing_lease_seconds <= self.processing_task_time_limit_seconds
        ):
            msg = (
                "ORBIT_PROCESSING_LEASE_SECONDS must exceed the task hard time limit in "
                "production, so a live job is never presumed dead while it can still run."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_answering(self) -> Self:
        if not self.llm_model.strip():
            msg = "ORBIT_LLM_MODEL must not be blank."
            raise ValueError(msg)
        if self.answer_rerank_top_k > self.answer_retrieval_top_k:
            msg = "ORBIT_ANSWER_RERANK_TOP_K cannot exceed ORBIT_ANSWER_RETRIEVAL_TOP_K."
            raise ValueError(msg)
        if self.answer_max_tokens + self.answer_max_context_tokens >= self.llm_context_window:
            # Otherwise the budget computation silently shrinks the context to
            # nothing and every question is answered "insufficient evidence".
            msg = (
                "ORBIT_ANSWER_MAX_TOKENS plus ORBIT_ANSWER_MAX_CONTEXT_TOKENS must be below "
                "ORBIT_LLM_CONTEXT_WINDOW."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_ai_provider(self) -> Self:
        if self.ai_provider is not AIProvider.OPENAI:
            return self
        missing = [
            name
            for name, value in (
                (
                    "ORBIT_OPENAI_API_KEY",
                    self.openai_api_key and self.openai_api_key.get_secret_value(),
                ),
                ("ORBIT_OPENAI_BASE_URL", self.openai_base_url),
                ("ORBIT_EMBEDDING_MODEL", self.embedding_model and self.embedding_model.strip()),
            )
            if not value
        ]
        if missing:
            msg = f"ORBIT_AI_PROVIDER=openai requires {', '.join(missing)} to be set."
            raise ValueError(msg)
        if self.embedding_model and len(self.embedding_model) > MAX_EMBEDDING_MODEL_ID_LENGTH:
            msg = f"ORBIT_EMBEDDING_MODEL is limited to {MAX_EMBEDDING_MODEL_ID_LENGTH} characters."
            raise ValueError(msg)
        if self.is_production and self.openai_base_url and self.openai_base_url.scheme != "https":
            # Document text and the API key would cross the network in clear.
            msg = "ORBIT_OPENAI_BASE_URL must use https:// in production."
            raise ValueError(msg)
        return self

    @field_validator("password_reset_url_template", "email_verification_url_template")
    @classmethod
    def _require_token_placeholder(cls, value: str) -> str:
        """A template without `{token}` mails a link that cannot work.

        Caught at startup rather than at the moment a locked-out user needs
        the link, which is the worst possible time to discover it.
        """
        if "{token}" not in value:
            msg = "URL template must contain the '{token}' placeholder."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_production_hardening(self) -> Self:
        if not self.is_production:
            return self
        if self.db_echo:
            # Echoed SQL contains parameter values, which means user data in logs.
            msg = "ORBIT_DB_ECHO must be false in production: it logs query parameters."
            raise ValueError(msg)
        if self.log_format is not LogFormat.JSON:
            msg = "ORBIT_LOG_FORMAT must be 'json' in production for machine-readable logs."
            raise ValueError(msg)
        if self.email_provider is EmailProvider.CONSOLE:
            # The console sender writes the reset link into the log stream,
            # where it is retained, shipped, and readable by anyone with log
            # access -- an account takeover for every user who requests a reset.
            msg = (
                "ORBIT_EMAIL_PROVIDER='console' is forbidden in production: it writes "
                "one-time account links to the log stream."
            )
            raise ValueError(msg)
        if any(
            template.startswith("http://")
            for template in (
                self.password_reset_url_template,
                self.email_verification_url_template,
            )
        ):
            # A one-time token in a plaintext URL is a token on the wire.
            msg = "Account link templates must use https:// in production."
            raise ValueError(msg)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings.

    Cached so that configuration is read and validated exactly once. Tests that
    need different configuration call ``get_settings.cache_clear()`` or build a
    ``Settings`` instance directly rather than mutating this one.
    """
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment
