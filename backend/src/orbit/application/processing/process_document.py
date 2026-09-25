"""The document processing pipeline (ADR-0012, ADR-0013, ADR-0019).

```
claim ─▶ fetch ─▶ parse ─▶ normalize ─▶ chunk ─▶ embed ─▶ index ─▶ READY
```

One execution handles one job. Every stage is observable -- a log record when
it completes, with its duration and what it produced -- and every failure is
classified and recorded with two messages: one safe for the uploader, one
detailed for the operator.

Idempotency is structural rather than checked for:

* claiming is conditional, so a redelivered message for a finished job is a
  no-op;
* parsing, normalization, and chunking are deterministic, so a re-run produces
  the same chunks;
* embedding reuses vectors already indexed for identical inputs in the same
  workspace and space, so reprocessing an indexed document does not pay the
  provider twice (ADR-0020);
* indexing deletes the version's chunks and inserts the new set -- every one
  with a validated vector in the active space -- in the same transaction that
  marks it READY, so a partial previous attempt cannot leave duplicates, and
  READY cannot precede a complete index;
* every write is fenced on the lease, so an execution recovery gave up on
  cannot overwrite the one that replaced it.

CPU-bound stages (parse, normalize, chunk) run synchronously on the event loop.
That is deliberate: a worker process runs exactly one job, so there is nothing
else for the loop to serve, and the hard time limit that stops a wedged parser
is enforced by the process pool, not by the loop (ADR-0002).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from orbit.application.embeddings.embed_chunks import ChunkEmbedder
from orbit.application.processing.lifecycle import (
    ClaimedJob,
    JobLifecycle,
    JobOutcome,
    JobRunResult,
    LeaseLostError,
)
from orbit.application.processing.policy import ProcessingPolicy
from orbit.application.processing.source import fetch_verified_source
from orbit.core.logging import bind_correlation, get_logger
from orbit.core.metrics import (
    DOCUMENT_PROCESSING_DURATION,
    PIPELINE_STAGE_DURATION,
)
from orbit.domain.errors import (
    DocumentEmptyError,
    DocumentLimitExceededError,
    DocumentNoExtractableTextError,
)
from orbit.domain.ports.processing import (
    Chunker,
    FailureClassifier,
    ParserRegistry,
    TextNormalizer,
)
from orbit.domain.ports.storage import ObjectStorage
from orbit.domain.processing.content import DocumentFormat, ParsedDocument
from orbit.domain.processing.failures import FailureKind
from orbit.domain.processing.jobs import PipelineStage

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineStages:
    """The replaceable stages, injected together."""

    parsers: ParserRegistry
    normalizer: TextNormalizer
    chunker: Chunker
    embedding: ChunkEmbedder


@dataclass(slots=True)
class _Timings:
    started: float = field(default_factory=time.perf_counter)
    stages: dict[str, float] = field(default_factory=dict)
    #: The stage in progress, or the one that was when the run failed -- so a
    #: failure record can say *where* it happened, not only what.
    current: str | None = None

    @contextmanager
    def stage(self, stage: PipelineStage, **start_fields: object) -> Iterator[dict[str, object]]:
        """Time a stage and log its completion with whatever it reports.

        The yielded dict is filled in by the stage body; its contents become
        fields on the completion record, so a stage's output (pages, blocks,
        chunks, tokens) is observable without a second log call.
        """
        report: dict[str, object] = {}
        began = time.perf_counter()
        self.current = stage.value
        logger.debug("pipeline.stage_started", stage=stage.value, **start_fields)
        outcome = "error"
        try:
            yield report
            outcome = "ok"
        finally:
            # A stage that failed still took time, and how long it took before
            # failing (a provider timing out at 60 s) is the finding.
            PIPELINE_STAGE_DURATION.labels(stage=stage.value, outcome=outcome).observe(
                time.perf_counter() - began
            )
        elapsed_ms = round((time.perf_counter() - began) * 1000, 1)
        self.stages[stage.value] = elapsed_ms
        logger.info("pipeline.stage_completed", stage=stage.value, duration_ms=elapsed_ms, **report)

    @property
    def total_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)


class ProcessDocumentJob:
    """Run one job, start to finish. Never raises for anything the document or
    a dependency did; those become recorded outcomes. Raises only if the
    outcome itself could not be recorded (the database is unreachable), which
    the task runtime answers with a delayed redelivery."""

    def __init__(
        self,
        lifecycle: JobLifecycle,
        storage: ObjectStorage,
        stages: PipelineStages,
        classifier: FailureClassifier,
        policy: ProcessingPolicy,
    ) -> None:
        self._lifecycle = lifecycle
        self._storage = storage
        self._stages = stages
        self._classifier = classifier
        self._policy = policy

    async def execute(self, job_id: uuid.UUID, *, worker_id: str) -> JobRunResult:
        bind_correlation(job_id=str(job_id))
        claimed = await self._lifecycle.claim(job_id, worker_id=worker_id)
        if isinstance(claimed, JobRunResult):
            return claimed

        bind_correlation(
            request_id=claimed.job.request_id,
            workspace_id=str(claimed.version.workspace_id),
            document_id=str(claimed.version.document_id),
        )
        timings = _Timings()
        content_format = _format_label(claimed.version.content_type)
        try:
            result = await self._run(claimed, timings)
        except LeaseLostError:
            logger.warning(
                "job.lease_lost",
                phase="pipeline",
                stage=timings.current,
                stages_ms=timings.stages,
            )
            lost = JobRunResult(job_id=job_id, outcome=JobOutcome.LEASE_LOST)
            _observe_execution(content_format, lost, timings)
            return lost
        except Exception as exc:
            failure = self._classifier.classify(exc)
            # Full traceback for the operator on defects; transient and
            # permanent failures are expected conditions and log without one.
            log_fields = {
                "error_code": failure.code,
                "failure_kind": failure.kind.value,
                "detail": failure.bounded_detail(),
                # Where it failed, and how far it got before that.
                "failed_stage": timings.current,
                "stages_ms": timings.stages,
                "run_attempt": claimed.job.run_attempt,
            }
            if failure.kind is FailureKind.DEFECT:
                logger.exception("pipeline.attempt_failed", **log_fields)
            else:
                logger.warning("pipeline.attempt_failed", **log_fields)
            recorded = await self._lifecycle.record_failure(claimed, failure)
            _observe_execution(content_format, recorded, timings)
            return recorded

        logger.info(
            "pipeline.completed",
            outcome=result.outcome.value,
            chunk_count=result.chunk_count,
            total_ms=timings.total_ms,
            stages_ms=timings.stages,
            run_attempt=claimed.job.run_attempt,
        )
        _observe_execution(content_format, result, timings)
        return result

    async def _run(self, claimed: ClaimedJob, timings: _Timings) -> JobRunResult:
        version = claimed.version
        limits = self._policy.limits
        stages = self._stages

        await self._lifecycle.heartbeat(claimed, PipelineStage.FETCH)
        with timings.stage(PipelineStage.FETCH) as report:
            source = await fetch_verified_source(
                self._storage, version, spool_memory_bytes=self._policy.spool_memory_bytes
            )
            report["bytes"] = version.byte_size

        with source:
            await self._lifecycle.heartbeat(claimed, PipelineStage.PARSE)
            with timings.stage(PipelineStage.PARSE, content_type=version.content_type) as report:
                parser = stages.parsers.resolve(version.content_type)
                extracted = parser.parse(source, limits=limits)
                report.update(
                    format=extracted.format.value,
                    pages=extracted.page_count,
                    raw_blocks=len(extracted.blocks),
                    warnings=list(extracted.warnings[:10]),
                )

        with timings.stage(PipelineStage.NORMALIZE) as report:
            parsed = stages.normalizer.normalize(extracted, limits=limits)
            report.update(
                blocks=len(parsed.blocks),
                characters=parsed.character_count,
                dropped_empty_blocks=parsed.stats.dropped_empty_blocks,
                removed_boilerplate_lines=parsed.stats.removed_boilerplate_lines,
                merged_across_pages=parsed.stats.merged_across_pages,
            )
        _require_text(parsed)

        with timings.stage(PipelineStage.CHUNK, chunker_version=stages.chunker.version) as report:
            drafts = stages.chunker.chunk(parsed)
            if len(drafts) > limits.max_chunks:
                msg = (
                    f"This document is too long to process: it produced {len(drafts):,} "
                    f"passages, and the limit is {limits.max_chunks:,}."
                )
                raise DocumentLimitExceededError(msg, chunks=len(drafts))
            report.update(
                chunks=len(drafts),
                tokens=sum(draft.token_count for draft in drafts),
                max_chunk_tokens=max((draft.token_count for draft in drafts), default=0),
            )
        if not drafts:  # pragma: no cover -- a non-empty document always yields a chunk
            msg = "This document contains no readable text."
            raise DocumentEmptyError(msg)

        await self._lifecycle.heartbeat(claimed, PipelineStage.EMBED)
        embedding = stages.embedding
        with timings.stage(PipelineStage.EMBED, space=embedding.space.key) as report:
            await embedding.verify_schema()
            embedded = await embedding.embed(
                drafts,
                workspace_id=version.workspace_id,
                after_batch=lambda: self._lifecycle.heartbeat(claimed, PipelineStage.EMBED),
            )
            report.update(
                vectors=len(embedded.chunks),
                distinct_inputs=embedded.report.distinct_inputs,
                reused=embedded.report.reused,
                embedded=embedded.report.embedded,
                requests=embedded.report.requests,
            )

        await self._lifecycle.heartbeat(claimed, PipelineStage.INDEX)
        with timings.stage(PipelineStage.INDEX) as report:
            result = await self._lifecycle.complete(
                claimed,
                embedded.chunks,
                page_count=parsed.page_count,
                space=embedding.space,
                chunker_version=stages.chunker.version,
            )
            report["chunks"] = result.chunk_count
        return result


def _format_label(content_type: str) -> str:
    """A bounded metric label for a stored content type."""
    lowered = content_type.lower()
    if "pdf" in lowered:
        return DocumentFormat.PDF.value
    if "markdown" in lowered:
        return DocumentFormat.MARKDOWN.value
    if lowered.startswith("text/"):
        return DocumentFormat.TEXT.value
    return "other"


def _observe_execution(content_format: str, result: JobRunResult, timings: _Timings) -> None:
    DOCUMENT_PROCESSING_DURATION.labels(
        format=content_format, outcome=result.outcome.value
    ).observe(time.perf_counter() - timings.started)


def _require_text(parsed: ParsedDocument) -> None:
    """A document with no text must fail visibly, never become READY-and-useless.

    A PDF with pages but no text gets its own reason, because the remedy is
    different: the file is fine, ORBIT cannot read scans yet (ADR-0012).
    """
    is_pdf = parsed.format is DocumentFormat.PDF and bool(parsed.page_count)
    if not parsed.is_empty:
        if is_pdf and _is_effectively_scanned(parsed):
            raise DocumentNoExtractableTextError(
                _SCANNED_MESSAGE,
                pages=parsed.page_count,
                characters=parsed.character_count,
            )
        return
    if is_pdf:
        raise DocumentNoExtractableTextError(_SCANNED_MESSAGE, pages=parsed.page_count)
    msg = "This document contains no readable text."
    raise DocumentEmptyError(msg)


_SCANNED_MESSAGE = (
    "No usable text could be extracted from this PDF. It may be a scanned image; "
    "ORBIT cannot read scanned documents yet."
)

#: Below this many non-space characters per page, across a multi-page PDF, the
#: "text" is a stray caption or stamp on what are really images of pages.
_MIN_CHARACTERS_PER_PAGE = 20
_MIN_PAGES_FOR_DENSITY_CHECK = 3


def _is_effectively_scanned(parsed: ParsedDocument) -> bool:
    """A long PDF with almost no text is a scan with a few stray words on it.

    Marking it READY would make it *look* searchable while answering nothing
    about its actual content -- the same failure ADR-0012 forbids for a PDF
    with no text at all, just harder to notice. A short PDF with little text
    (a one-page memo) is legitimate and is not caught by this.
    """
    pages = parsed.page_count or 0
    if pages < _MIN_PAGES_FOR_DENSITY_CHECK:
        return False
    visible = sum(1 for char in parsed.text if not char.isspace())
    return visible / pages < _MIN_CHARACTERS_PER_PAGE
