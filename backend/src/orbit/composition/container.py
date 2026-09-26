"""Composition root.

The only module that knows both the ports and their concrete adapters. Every
other layer receives what it needs through injection, which is what allows a
test to substitute any dependency by building a container with different
bindings -- no monkeypatching, no import-time globals.

Construction is eager and ordered; teardown is reverse-ordered and best-effort,
so one failing resource cannot prevent the rest from being released.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from orbit.application.access import ResolveAccessContext
from orbit.application.answering.answer_question import AnswerPolicy, AnswerQuestion
from orbit.application.auth.account_mail import AccountLinkMailer
from orbit.application.auth.authenticate_access_token import AuthenticateAccessToken
from orbit.application.auth.email_verification import RequestEmailVerification, VerifyEmail
from orbit.application.auth.login_user import LoginUser
from orbit.application.auth.logout_session import LogoutSession
from orbit.application.auth.password_reset import CompletePasswordReset, RequestPasswordReset
from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.auth.refresh_session import RefreshSession
from orbit.application.auth.register_user import RegisterUser
from orbit.application.conversations.manage import (
    CreateConversation,
    DeleteConversation,
    GetConversation,
    ListConversations,
    ListMessages,
)
from orbit.application.documents.add_document_version import AddDocumentVersion
from orbit.application.documents.archive_document import ArchiveDocument
from orbit.application.documents.delete_document import DeleteDocument
from orbit.application.documents.get_document import GetDocument
from orbit.application.documents.get_document_download import GetDocumentDownload
from orbit.application.documents.list_document_versions import ListDocumentVersions
from orbit.application.documents.list_documents import ListDocuments
from orbit.application.documents.read_document_content import ReadDocumentContent
from orbit.application.documents.sweep_orphaned_storage import SweepOrphanedStorage
from orbit.application.documents.tag_document import AddDocumentTag, RemoveDocumentTag
from orbit.application.documents.update_document import UpdateDocument
from orbit.application.documents.upload_document import UploadDocument
from orbit.application.folders.manage import CreateFolder, DeleteFolder, ListFolders, RenameFolder
from orbit.application.health.check_readiness import CheckReadiness
from orbit.application.processing.get_processing_status import GetProcessingStatus
from orbit.application.processing.queue_snapshot import ReadQueueSnapshot
from orbit.application.processing.reprocess_document import ReprocessDocument
from orbit.application.registry import UseCases
from orbit.application.retrieval.hybrid_search import HybridSearch, SearchPolicy
from orbit.application.tags.manage import CreateTag, DeleteTag, ListTags, UpdateTag
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.application.workspaces.delete_workspace import DeleteWorkspace
from orbit.application.workspaces.get_workspace import GetWorkspace
from orbit.application.workspaces.list_workspaces import ListWorkspaces
from orbit.application.workspaces.members import (
    ChangeMemberRole,
    InviteMember,
    ListMembers,
    RemoveMember,
)
from orbit.application.workspaces.rename_workspace import RenameWorkspace
from orbit.composition.embeddings import EmbeddingBinding, build_embedding_provider
from orbit.composition.llm import LLMBinding, build_llm_provider
from orbit.core.clock import Clock, SystemClock
from orbit.core.config import EmailProvider, Settings
from orbit.core.logging import get_logger
from orbit.core.metrics import QUEUE_JOBS, QUEUE_OLDEST_READY_AGE
from orbit.domain.ports.audit import AuditSink
from orbit.domain.ports.cache import QueryVectorCache
from orbit.domain.ports.email import EmailSender
from orbit.domain.ports.health import HealthProbe
from orbit.domain.retrieval import LexicalMatch
from orbit.infrastructure.ai.reranking import PassthroughReranker
from orbit.infrastructure.audit import DatabaseAuditSink
from orbit.infrastructure.cache.query_vectors import (
    NullQueryVectorCache,
    RedisQueryVectorCache,
)
from orbit.infrastructure.cache.rate_limiter import RedisRateLimiter
from orbit.infrastructure.cache.redis import RedisClient
from orbit.infrastructure.chunking.tokens import ApproximateTokenCounter
from orbit.infrastructure.db.session import Database
from orbit.infrastructure.db.unit_of_work import make_unit_of_work_factory
from orbit.infrastructure.email.senders import (
    ConsoleEmailSender,
    SmtpEmailSender,
    UnconfiguredEmailSender,
)
from orbit.infrastructure.health import (
    DatabaseProbe,
    EmbeddingSchemaProbe,
    LanguageModelCircuitProbe,
    ObjectStorageProbe,
    RedisProbe,
)
from orbit.infrastructure.processing.failure_classifier import PipelineFailureClassifier
from orbit.infrastructure.queue.celery_app import build_celery_app
from orbit.infrastructure.queue.dispatcher import CeleryProcessingJobQueue
from orbit.infrastructure.storage.s3 import ObjectStorageClient

logger = get_logger(__name__)


@dataclass(slots=True)
class Container:
    """Owns every process-scoped resource and the use cases built from them."""

    settings: Settings
    database: Database
    #: Exposed so the HTTP error handler can record authorization
    #: failures. It is a `domain` Protocol, so `api` reading it off
    #: application state does not cross a layering boundary.
    audit: AuditSink
    redis: RedisClient
    storage: ObjectStorageClient
    #: The query-side embedding provider for search (ADR-0021).
    embeddings: EmbeddingBinding
    #: The chat model for grounded answers (ADR-0022).
    llm: LLMBinding
    check_readiness: CheckReadiness
    use_cases: UseCases
    #: Not called by any API route -- see `sweep_orphaned_storage.py`'s
    #: module docstring for why it is wired here anyway.
    sweep_orphaned_storage: SweepOrphanedStorage
    #: Feeds the queue gauges; see `refresh_scrape_gauges`.
    read_queue_snapshot: ReadQueueSnapshot
    clock: Clock = field(default_factory=SystemClock)

    @classmethod
    def create(cls, settings: Settings, *, clock: Clock | None = None) -> Container:
        """Build the container.

        Constructing a client does not open a connection -- SQLAlchemy, redis-py
        and botocore all connect lazily -- so this is fast and cannot fail
        because a dependency is momentarily down. Whether dependencies are
        actually reachable is the readiness probe's question, not startup's.
        """
        clock = clock or SystemClock()
        database = Database(settings)
        redis = RedisClient(settings)
        storage = ObjectStorageClient(settings)

        llm = build_llm_provider(settings)
        probes: tuple[HealthProbe, ...] = (
            DatabaseProbe(database),
            EmbeddingSchemaProbe(database, expected_dimensions=settings.embedding_dimensions),
            RedisProbe(redis),
            ObjectStorageProbe(storage),
            *((LanguageModelCircuitProbe(llm.circuit),) if llm.circuit is not None else ()),
        )

        # Pagination cursors reuse the application's signing key rather than a
        # second secret (ADR-0003's key already exists, is already validated
        # at startup, and both uses are server-side HMACs over server-
        # generated payloads -- there is no cross-protocol attack surface a
        # second key would close).
        uow_factory = make_unit_of_work_factory(database, cursor_secret=settings.secret_key)

        # The audit sink gets the same factory but opens its own unit of
        # work per event, so recording an event never joins -- and so never
        # rolls back with -- the action being recorded.
        audit = DatabaseAuditSink(uow_factory)
        rate_limiter = RedisRateLimiter(redis.client)
        email_sender = _build_email_sender(settings)

        reset_mailer = AccountLinkMailer(email_sender, settings.password_reset_url_template)
        verify_mailer = AccountLinkMailer(email_sender, settings.email_verification_url_template)
        sweep_orphaned_storage = SweepOrphanedStorage(uow_factory, storage, clock)
        # Publishes by task name only; building the Celery app opens no
        # connection, and the API never imports the pipeline (ADR-0019).
        processing_queue = CeleryProcessingJobQueue(build_celery_app(settings))
        # Connects lazily, like every other client here: a provider outage
        # degrades search, it does not stop the API booting.
        embeddings = build_embedding_provider(settings)
        # Only the query *vector* is cached, and only because it is a pure
        # function of (text, space). No result page, membership, or document
        # list is cached anywhere in ORBIT -- the reasoning is in
        # `domain/ports/cache.py` and ADR-0024.
        vector_cache: QueryVectorCache = (
            RedisQueryVectorCache(redis.client, ttl_seconds=settings.query_vector_cache_ttl_seconds)
            if settings.query_vector_cache_enabled
            else NullQueryVectorCache()
        )
        search = HybridSearch(
            uow_factory,
            embeddings.provider,
            SearchPolicy(
                candidates_per_retriever=settings.search_candidates_per_retriever,
                lexical_match=LexicalMatch(settings.search_lexical_match),
                ef_search=settings.vector_search_ef_search,
            ),
            vector_cache=vector_cache,
            rate_limit=AuthRateLimitGuard(
                rate_limiter, RateLimitPolicy.for_search(settings), audit
            ),
        )

        use_cases = UseCases(
            register_user=RegisterUser(
                uow_factory,
                audit,
                AuthRateLimitGuard(
                    rate_limiter,
                    RateLimitPolicy.for_account_email(settings, scope="register"),
                    audit,
                ),
            ),
            login_user=LoginUser(uow_factory, settings, rate_limiter, audit, clock),
            refresh_session=RefreshSession(uow_factory, settings, audit, clock),
            logout_session=LogoutSession(uow_factory, audit),
            authenticate_access_token=AuthenticateAccessToken(
                uow_factory, secret_key=settings.secret_key, clock=clock
            ),
            request_password_reset=RequestPasswordReset(
                uow_factory,
                reset_mailer,
                audit,
                AuthRateLimitGuard(
                    rate_limiter,
                    RateLimitPolicy.for_account_email(settings, scope="password_reset"),
                    audit,
                ),
                clock,
            ),
            complete_password_reset=CompletePasswordReset(uow_factory, audit, clock),
            request_email_verification=RequestEmailVerification(
                uow_factory,
                verify_mailer,
                audit,
                AuthRateLimitGuard(
                    rate_limiter,
                    RateLimitPolicy.for_account_email(settings, scope="email_verification"),
                    audit,
                ),
                clock,
            ),
            verify_email=VerifyEmail(uow_factory, audit, clock),
            resolve_access_context=ResolveAccessContext(uow_factory),
            create_workspace=CreateWorkspace(uow_factory),
            list_workspaces=ListWorkspaces(uow_factory),
            get_workspace=GetWorkspace(uow_factory),
            rename_workspace=RenameWorkspace(uow_factory),
            delete_workspace=DeleteWorkspace(uow_factory, audit),
            list_members=ListMembers(uow_factory),
            invite_member=InviteMember(uow_factory, audit),
            change_member_role=ChangeMemberRole(uow_factory, audit),
            remove_member=RemoveMember(uow_factory, audit),
            list_documents=ListDocuments(uow_factory),
            get_document=GetDocument(uow_factory),
            update_document=UpdateDocument(uow_factory),
            archive_document=ArchiveDocument(uow_factory),
            delete_document=DeleteDocument(uow_factory),
            list_document_versions=ListDocumentVersions(uow_factory),
            read_document_content=ReadDocumentContent(uow_factory),
            add_document_tag=AddDocumentTag(uow_factory),
            remove_document_tag=RemoveDocumentTag(uow_factory),
            list_folders=ListFolders(uow_factory),
            create_folder=CreateFolder(uow_factory),
            rename_folder=RenameFolder(uow_factory),
            delete_folder=DeleteFolder(uow_factory),
            list_tags=ListTags(uow_factory),
            create_tag=CreateTag(uow_factory),
            update_tag=UpdateTag(uow_factory),
            delete_tag=DeleteTag(uow_factory),
            upload_document=UploadDocument(
                uow_factory,
                storage,
                settings,
                AuthRateLimitGuard(rate_limiter, RateLimitPolicy.for_upload(settings), audit),
                processing_queue,
            ),
            add_document_version=AddDocumentVersion(
                uow_factory,
                storage,
                settings,
                AuthRateLimitGuard(rate_limiter, RateLimitPolicy.for_upload(settings), audit),
                processing_queue,
            ),
            get_document_download=GetDocumentDownload(uow_factory, storage, clock),
            reprocess_document=ReprocessDocument(uow_factory, processing_queue),
            get_processing_status=GetProcessingStatus(uow_factory),
            search=search,
            create_conversation=CreateConversation(uow_factory),
            list_conversations=ListConversations(uow_factory),
            get_conversation=GetConversation(uow_factory),
            list_messages=ListMessages(uow_factory),
            delete_conversation=DeleteConversation(uow_factory),
            answer_question=AnswerQuestion(
                uow_factory,
                search=search,
                reranker=PassthroughReranker(),
                llm=llm.provider,
                token_counter=ApproximateTokenCounter(),
                classifier=PipelineFailureClassifier(),
                policy=answer_policy(settings),
                clock=clock,
                rate_limit=AuthRateLimitGuard(
                    rate_limiter, RateLimitPolicy.for_chat(settings), audit
                ),
            ),
        )

        return cls(
            settings=settings,
            database=database,
            audit=audit,
            redis=redis,
            storage=storage,
            embeddings=embeddings,
            llm=llm,
            check_readiness=CheckReadiness(probes),
            use_cases=use_cases,
            sweep_orphaned_storage=sweep_orphaned_storage,
            read_queue_snapshot=ReadQueueSnapshot(uow_factory, clock),
            clock=clock,
        )

    async def refresh_scrape_gauges(self) -> None:
        """Recompute the gauges that are state read from elsewhere.

        Pool occupancy is in-process and cannot fail; the queue snapshot is a
        database query and can. The pool gauges are published first so a
        database outage still leaves them true.
        """
        self.database.publish_pool_gauges()
        snapshot = await self.read_queue_snapshot.execute()
        QUEUE_JOBS.labels(state="ready").set(snapshot.ready)
        QUEUE_JOBS.labels(state="scheduled").set(snapshot.scheduled)
        QUEUE_JOBS.labels(state="running").set(snapshot.running)
        QUEUE_JOBS.labels(state="lease_expired").set(snapshot.lease_expired)
        QUEUE_OLDEST_READY_AGE.set(snapshot.oldest_ready_age_seconds or 0.0)

    @staticmethod
    def mark_scrape_gauges_unknown() -> None:
        """Report the queue as *unknown* after a failed refresh.

        NaN, not the last value: a stale "no backlog" during a database outage
        would read as good news exactly when the system is least able to say.
        """
        for state in ("ready", "scheduled", "running", "lease_expired"):
            QUEUE_JOBS.labels(state=state).set(math.nan)
        QUEUE_OLDEST_READY_AGE.set(math.nan)

    async def aclose(self) -> None:
        """Release every resource, in reverse construction order.

        Each teardown is isolated: a failure closing one resource must not
        prevent the others from being released, or a shutdown that hits one bad
        connection leaks the rest.
        """
        # Final answer writes run detached from their requests (ADR-0022);
        # let them land before the database goes away.
        await _safely("answers", self.use_cases.answer_question.wait_for_background)
        await _safely("llm", self.llm.aclose)
        await _safely("embeddings", self.embeddings.aclose)
        await _safely("storage", self._close_storage)
        await _safely("redis", self.redis.close)
        await _safely("database", self.database.dispose)

    async def _close_storage(self) -> None:
        # botocore's close is synchronous and returns immediately; wrapping it
        # keeps the teardown sequence above uniform.
        self.storage.close()


def answer_policy(settings: Settings) -> AnswerPolicy:
    return AnswerPolicy(
        retrieval_top_k=settings.answer_retrieval_top_k,
        rerank_top_k=settings.answer_rerank_top_k,
        max_sources=settings.answer_max_sources,
        max_context_tokens=settings.answer_max_context_tokens,
        max_answer_tokens=settings.answer_max_tokens,
        temperature=settings.answer_temperature,
        history_messages=settings.answer_history_messages,
        max_history_tokens=settings.answer_max_history_tokens,
        generation_timeout_seconds=settings.answer_generation_timeout_seconds,
    )


def _build_email_sender(settings: Settings) -> EmailSender:
    """Select the mail adapter.

    `match` over a closed enum rather than a dict lookup with a default:
    adding a provider without a binding is then a type error here, not a
    silent fallback to "no mail leaves the building".
    """
    match settings.email_provider:
        case EmailProvider.CONSOLE:
            # Refused in production by `Settings`; see the reasoning there.
            return ConsoleEmailSender()
        case EmailProvider.UNCONFIGURED:
            return UnconfiguredEmailSender()
        case EmailProvider.SMTP:
            # Host presence is guaranteed by Settings validation.
            assert settings.smtp_host is not None  # noqa: S101
            return SmtpEmailSender(
                host=settings.smtp_host,
                port=settings.smtp_port,
                from_address=settings.email_from_address,
                username=settings.smtp_username,
                password=(
                    settings.smtp_password.get_secret_value() if settings.smtp_password else None
                ),
                starttls=settings.smtp_starttls,
                use_ssl=settings.smtp_use_ssl,
                timeout_seconds=settings.smtp_timeout_seconds,
            )


async def _safely(resource: str, close: Callable[[], Awaitable[None]]) -> None:
    try:
        await close()
    except Exception:
        # Shutdown is best-effort by design. The failure is recorded, never
        # raised: propagating here would abort the remaining teardown.
        logger.exception("shutdown.resource_close_failed", resource=resource)
