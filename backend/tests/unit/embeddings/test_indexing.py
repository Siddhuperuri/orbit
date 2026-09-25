"""Embedding and indexing inside the pipeline, on fakes.

What is proved here: a READY version has a complete index with truthful
metadata; no failure of the provider -- an outage, a malformed answer, a wrong
width -- lets a version become READY or leaves a partial index; duplicate
processing does not pay twice; and reuse never crosses a workspace or a space.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from orbit.application.embeddings.embed_chunks import ChunkEmbedder
from orbit.application.processing.lifecycle import PROCESSING_SYSTEM, JobOutcome
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.domain.access import AccessContext, Role
from orbit.domain.embeddings import embedding_input_sha256
from orbit.domain.errors import AIProviderUnavailableError
from orbit.domain.models.entities import Document, ProcessingStatus
from orbit.domain.processing.content import ChunkDraft
from orbit.domain.processing.failures import FailureKind
from orbit.domain.processing.jobs import JobStatus
from orbit.infrastructure.ai.fake_embeddings import FakeEmbeddingProvider
from tests.unit.fakes.fake_processing import FakeEmbeddingIndexRepository
from tests.unit.processing.harness import ControllableEmbedder, Pipeline, build_pipeline

SECTION = (
    "{topic} is governed by a written policy. Every request about {topic} is logged, "
    "reviewed by a second person, and retained for audit. Exceptions to the {topic} "
    "policy require written approval from the security lead and expire after ninety days. "
)


def _handbook(*topics: str) -> bytes:
    body = "\n\n".join(
        f"## {topic.title()}\n\n{SECTION.format(topic=topic) * 4}" for topic in topics
    )
    return f"# Handbook\n\n{body}\n".encode()


async def _new_version(pipeline: Pipeline, document: Document, data: bytes) -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield data

    await pipeline.add_version.execute(
        pipeline.ctx, document.id, filename="handbook.md", content_stream=stream()
    )


# ---------------------------------------------------------------------------
# READY means indexed, with truthful metadata
# ---------------------------------------------------------------------------


class TestIndexMetadata:
    async def test_every_chunk_of_a_ready_version_records_its_space_input_and_time(
        self,
    ) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("handbook.md", _handbook("access", "travel", "leave"))
        await pipeline.drain()

        version = pipeline.version(document)
        chunks = pipeline.chunks(document)
        assert version.status is ProcessingStatus.READY
        assert version.chunk_count == len(chunks) > 1
        space = pipeline.embedder.space
        for chunk in chunks:
            assert (chunk.version_id, chunk.document_id) == (version.id, document.id)
            assert chunk.space == space
            assert len(chunk.embedding) == chunk.embedding_dimensions == space.dimensions
            assert chunk.embedding_input_sha256 == embedding_input_sha256(
                chunk.heading_path, chunk.text
            )
            assert chunk.embedded_at == pipeline.clock.now()
            assert math.isclose(math.hypot(*chunk.embedding), 1.0, rel_tol=1e-5)

    async def test_each_vector_belongs_to_its_own_chunk_text(self) -> None:
        """Pairing is by input hash: re-embedding each stored chunk's own input
        reproduces exactly the vector stored beside it."""
        pipeline = await build_pipeline(embedder=ControllableEmbedder(batch_size=2))
        document = await pipeline.upload("handbook.md", _handbook("access", "travel", "leave"))
        await pipeline.drain()

        fake = pipeline.embedder.inner
        for chunk in pipeline.chunks(document):
            expected = fake.vector(
                f"{chunk.heading_path}\n\n{chunk.text}" if chunk.heading_path else chunk.text
            )
            assert list(chunk.embedding) == list(expected), chunk.ordinal

    async def test_batches_respect_the_providers_bounds(self) -> None:
        embedder = ControllableEmbedder(batch_size=2)
        pipeline = await build_pipeline(embedder=embedder)
        document = await pipeline.upload(
            "handbook.md", _handbook("access", "travel", "leave", "expenses", "hiring")
        )
        await pipeline.drain()
        distinct = len({chunk.embedding_input_sha256 for chunk in pipeline.chunks(document)})
        assert embedder.calls == math.ceil(distinct / 2)


# ---------------------------------------------------------------------------
# Failed embeddings never produce READY or a partial index
# ---------------------------------------------------------------------------


class TestFailedEmbeddings:
    async def test_an_outage_on_a_later_batch_leaves_nothing_indexed_and_the_document_pending(
        self,
    ) -> None:
        embedder = ControllableEmbedder(batch_size=1)
        pipeline = await build_pipeline(embedder=embedder)
        document = await pipeline.upload("handbook.md", _handbook("access", "travel", "leave"))

        async def outage_after_first_batch() -> None:
            embedder.failures.append(AIProviderUnavailableError("503 on batch 2"))

        embedder.before_return = outage_after_first_batch
        (job_id,) = pipeline.dispatched_job_ids()
        result = await pipeline.run(job_id)

        assert result.outcome is JobOutcome.RETRY_SCHEDULED
        version = pipeline.version(document)
        assert (version.status, version.chunk_count) == (ProcessingStatus.PENDING, 0)
        assert pipeline.chunks(document) == []
        first, _ = pipeline.jobs(document)
        assert (first.failure_kind, first.error_code) == (
            FailureKind.TRANSIENT,
            "AI_PROVIDER_UNAVAILABLE",
        )

        await pipeline.drain()
        assert pipeline.version(document).status is ProcessingStatus.READY

    @pytest.mark.parametrize(
        "bad_output",
        [
            [[math.nan] * 32],
            [[0.0] * 32],
            [],
        ],
        ids=["nan", "zero-vector", "missing-vectors"],
    )
    async def test_an_unindexable_response_is_retried_never_indexed(
        self, bad_output: list[list[float]]
    ) -> None:
        embedder = ControllableEmbedder(batch_size=1, scripted_outputs=[bad_output])
        pipeline = await build_pipeline(embedder=embedder)
        document = await pipeline.upload("a.txt", (SECTION.format(topic="access") * 3).encode())
        (job_id,) = pipeline.dispatched_job_ids()

        result = await pipeline.run(job_id)

        assert result.outcome is JobOutcome.RETRY_SCHEDULED
        assert pipeline.chunks(document) == []
        assert pipeline.version(document).status is ProcessingStatus.PENDING
        assert pipeline.jobs(document)[0].error_code == "AI_PROVIDER_RESPONSE_INVALID"
        await pipeline.drain()
        assert pipeline.version(document).status is ProcessingStatus.READY

    async def test_a_vector_of_the_wrong_width_fails_at_once_as_a_defect(self) -> None:
        embedder = ControllableEmbedder(scripted_outputs=[[[0.25] * 16]])
        pipeline = await build_pipeline(embedder=embedder)
        document = await pipeline.upload("a.txt", (SECTION.format(topic="access") * 3).encode())
        await pipeline.drain()

        version = pipeline.version(document)
        assert (version.status, version.chunk_count) == (ProcessingStatus.FAILED, 0)
        (job,) = pipeline.jobs(document)
        assert (job.status, job.failure_kind) == (JobStatus.FAILED, FailureKind.DEFECT)
        assert embedder.calls == 1, "a configuration defect is not retried"
        assert pipeline.chunks(document) == []

    async def test_a_space_the_schema_cannot_store_is_refused_before_paying_the_provider(
        self,
    ) -> None:
        pipeline = await build_pipeline()
        pipeline.uow_factory.state.controls.column_dimensions = 1536
        document = await pipeline.upload("a.txt", (SECTION.format(topic="access") * 3).encode())
        await pipeline.drain()

        assert pipeline.version(document).status is ProcessingStatus.FAILED
        assert pipeline.jobs(document)[0].failure_kind is FailureKind.DEFECT
        assert pipeline.embedder.calls == 0

    async def test_ready_is_never_written_when_the_index_cannot_be_verified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def one_short(*args: object, **kwargs: object) -> int:
            del args, kwargs
            return 1

        monkeypatch.setattr(FakeEmbeddingIndexRepository, "count_indexed", one_short)
        pipeline = await build_pipeline()
        document = await pipeline.upload("handbook.md", _handbook("access", "travel"))
        await pipeline.drain()

        version = pipeline.version(document)
        assert version.status is ProcessingStatus.FAILED
        assert pipeline.jobs(document)[0].failure_kind is FailureKind.DEFECT
        assert pipeline.chunks(document) == [], "the index transaction rolled back"

    async def test_a_lost_lease_stops_embedding_before_the_next_batch(self) -> None:
        embedder = ControllableEmbedder(batch_size=1)
        pipeline = await build_pipeline(embedder=embedder)
        document = await pipeline.upload("handbook.md", _handbook("access", "travel", "leave"))
        (job_id,) = pipeline.dispatched_job_ids()

        async def recovery_takes_the_job() -> None:
            job = pipeline.uow_factory.state.jobs[job_id]
            pipeline.uow_factory.state.jobs[job_id] = dataclasses.replace(
                job, worker_id="successor"
            )

        embedder.before_return = recovery_takes_the_job
        result = await pipeline.run(job_id)

        assert result.outcome is JobOutcome.LEASE_LOST
        assert embedder.calls == 1, "no further batches are paid for"
        assert pipeline.chunks(document) == []


# ---------------------------------------------------------------------------
# Duplicate processing
# ---------------------------------------------------------------------------


def _draft(ordinal: int, text: str, heading: tuple[str, ...] = ()) -> ChunkDraft:
    return ChunkDraft(
        ordinal=ordinal,
        text=text,
        token_count=len(text.split()),
        char_start=ordinal * 1000,
        char_end=ordinal * 1000 + len(text),
        page_start=None,
        page_end=None,
        heading_path=heading,
    )


class TestDuplicateProcessing:
    async def test_identical_inputs_in_one_document_are_embedded_once(self) -> None:
        pipeline = await build_pipeline()
        embedder = pipeline.embedder
        chunk_embedder = ChunkEmbedder(
            pipeline.uow_factory, embedder, pipeline.clock, PROCESSING_SYSTEM
        )
        drafts = [
            _draft(0, "Standard confidentiality notice.", ("Legal",)),
            _draft(1, "Distinct body text.", ("Legal",)),
            _draft(2, "Standard confidentiality notice.", ("Legal",)),
            # Same text under a different heading is a different input.
            _draft(3, "Standard confidentiality notice.", ("Annex",)),
        ]
        result = await chunk_embedder.embed(drafts, workspace_id=pipeline.ctx.workspace_id)

        assert len(embedder.inputs) == 3
        assert (result.report.distinct_inputs, result.report.embedded) == (3, 3)
        assert list(result.chunks[0].embedding) == list(result.chunks[2].embedding)
        assert list(result.chunks[0].embedding) != list(result.chunks[3].embedding)
        assert [chunk.draft.ordinal for chunk in result.chunks] == [0, 1, 2, 3]

    async def test_a_redelivered_message_does_not_pay_the_provider_again(self) -> None:
        pipeline = await build_pipeline()
        await pipeline.upload("handbook.md", _handbook("access", "travel"))
        (job_id,) = pipeline.dispatched_job_ids()
        await pipeline.run(job_id, "worker-a")
        calls = pipeline.embedder.calls

        again = await pipeline.run(job_id, "worker-b")

        assert (again.outcome, again.reason) == (JobOutcome.SKIPPED, "already_finished")
        assert pipeline.embedder.calls == calls

    async def test_reprocessing_an_indexed_document_reuses_every_vector(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("handbook.md", _handbook("access", "travel", "leave"))
        await pipeline.drain()
        before = {c.embedding_input_sha256: c for c in pipeline.chunks(document)}
        inputs_before = len(pipeline.embedder.inputs)
        indexed_at = pipeline.clock.now()

        pipeline.clock.advance(timedelta(days=3))
        version = pipeline.version(document)
        pipeline.uow_factory.state.versions[version.id] = dataclasses.replace(
            version, status=ProcessingStatus.FAILED, failure_code="X", failure_reason="x"
        )
        await pipeline.reprocess.execute(pipeline.ctx, document.id)
        await pipeline.drain()

        after = pipeline.chunks(document)
        assert pipeline.version(document).status is ProcessingStatus.READY
        assert len(pipeline.embedder.inputs) == inputs_before, "nothing was re-embedded"
        assert {c.embedding_input_sha256 for c in after} == set(before)
        for chunk in after:
            assert list(chunk.embedding) == list(before[chunk.embedding_input_sha256].embedding)
            assert chunk.embedded_at == indexed_at, "a reused vector keeps its creation time"

    async def test_the_same_passages_in_another_file_are_not_paid_for_twice(self) -> None:
        pipeline = await build_pipeline()
        original = await pipeline.upload("handbook.md", _handbook("access", "travel"))
        await pipeline.drain()
        first_inputs = list(pipeline.embedder.inputs)
        known = {c.embedding_input_sha256 for c in pipeline.chunks(original)}

        # Different bytes (so not a deduplicated upload), identical passages.
        copy = await pipeline.upload("handbook-copy.md", _handbook("access", "travel") + b"\n")
        await pipeline.drain()

        assert pipeline.version(copy).status is ProcessingStatus.READY
        assert {c.embedding_input_sha256 for c in pipeline.chunks(copy)} == known
        assert pipeline.embedder.inputs == first_inputs, "every vector was reused"

    async def test_a_new_version_of_the_same_document_is_embedded_afresh(self) -> None:
        """A known limit, asserted so that changing it is a visible decision:
        uploading a version deletes the previous version's chunks at once
        (models/content.py), so there is nothing left to reuse."""
        pipeline = await build_pipeline()
        document = await pipeline.upload("handbook.md", _handbook("access", "travel"))
        await pipeline.drain()
        embedded_before = len(pipeline.embedder.inputs)

        await _new_version(pipeline, document, _handbook("access", "travel") + b"\nEdited.\n")
        await pipeline.drain()

        assert pipeline.version(document).status is ProcessingStatus.READY
        assert len(pipeline.embedder.inputs) > embedded_before

    async def test_reuse_never_crosses_a_workspace(self) -> None:
        pipeline = await build_pipeline()
        data = _handbook("access", "travel")
        await pipeline.upload("handbook.md", data)
        await pipeline.drain()
        first_inputs = list(pipeline.embedder.inputs)

        workspace = await CreateWorkspace(pipeline.uow_factory).execute(
            name="Globex", created_by_user_id=pipeline.ctx.user_id
        )
        other = AccessContext(
            user_id=pipeline.ctx.user_id, workspace_id=workspace.id, role=Role.OWNER
        )
        await pipeline.upload("handbook.md", data, ctx=other)
        await pipeline.drain()

        assert pipeline.embedder.inputs[len(first_inputs) :] == first_inputs
        lookups = pipeline.uow_factory.state.controls.reuse_lookups
        assert lookups == [pipeline.ctx.workspace_id, workspace.id]

    async def test_vectors_from_another_space_are_never_reused(self) -> None:
        pipeline = await build_pipeline()
        document = await pipeline.upload("handbook.md", _handbook("access", "travel"))
        await pipeline.drain()
        drafts = [
            _draft(
                i, chunk.text, tuple(chunk.heading_path.split(" > ")) if chunk.heading_path else ()
            )
            for i, chunk in enumerate(pipeline.chunks(document))
        ]

        other_model = ControllableEmbedder(
            inner=FakeEmbeddingProvider(dimensions=32, model_id="orbit-fake-embedding-v2")
        )
        result = await ChunkEmbedder(
            pipeline.uow_factory, other_model, pipeline.clock, PROCESSING_SYSTEM
        ).embed(drafts, workspace_id=pipeline.ctx.workspace_id)

        assert result.report.reused == 0
        assert len(other_model.inputs) == len(drafts)
