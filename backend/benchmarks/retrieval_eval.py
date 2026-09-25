"""Retrieval quality evaluation: lexical, semantic, and hybrid, measured.

    uv run python -m benchmarks.retrieval_eval [--output results.json]

What it does, against a real PostgreSQL (a `*_test` or `*_bench` database):

1. **Ingests** `benchmarks/retrieval/corpus/` through the production parser,
   normaliser, chunker, and embed stage -- with whatever embedding provider the
   environment configures (`ORBIT_AI_PROVIDER`) -- into two workspaces:
   `acme` (the searched corpus) and `globex` (another tenant holding
   confidential material and a verbatim copy of one acme policy).
2. **Validates the dataset's claims** before trusting it: every `paraphrase`
   query must share no stemmed term with its answer, and every `identifier`
   query's terms must all occur in its answer, both checked with PostgreSQL's
   own `english` text-search parser. A violation stops the run.
3. **Runs every query** through the production repository SQL and scores
   seven configurations: three lexical variants, semantic, and three fusions.
   The production hybrid path (`HybridSearch`) is also executed and must rank
   exactly as the harness's RRF k=60 does.
4. **Scores** at document level (a document is relevant or not; chunks are
   collapsed to their document in rank order): P@1, P@3, R@5, R@10, MRR@10,
   per category and overall; for `distractor` queries, how often a distractor
   outranks the answer; for `isolation`, the number of leaked results.

It removes everything it created. Results depend on the embedding provider:
the deterministic `fake` provider is lexical underneath, so its "semantic"
numbers measure word overlap, not meaning. The report says which ran.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import statistics
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from orbit.application.embeddings.embed_chunks import ChunkEmbedder
from orbit.application.retrieval.hybrid_search import HybridSearch, SearchPolicy
from orbit.composition.embeddings import build_embedding_provider
from orbit.core.clock import SystemClock
from orbit.core.config import AIProvider, Settings
from orbit.domain.access import AccessContext, Role, SystemContext
from orbit.domain.embeddings import to_indexable_vector
from orbit.domain.models.entities import ProcessingOutcome, ProcessingStatus, VersionContent
from orbit.domain.ports.embeddings import EmbeddingProvider
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory
from orbit.domain.processing.content import ParseLimits
from orbit.domain.retrieval import (
    Candidate,
    LexicalMatch,
    RankedList,
    RetrievalMethod,
    SearchFilters,
    SearchQuery,
    min_max_fusion,
    normalize_query,
    reciprocal_rank_fusion,
)
from orbit.infrastructure.chunking.structure_aware import StructureAwareChunker
from orbit.infrastructure.db.repositories.search import SqlSearchRepository
from orbit.infrastructure.db.session import Database
from orbit.infrastructure.db.unit_of_work import make_unit_of_work_factory
from orbit.infrastructure.parsing.normalization import DocumentNormalizer
from orbit.infrastructure.parsing.registry import default_parser_registry

ROOT = Path(__file__).parent / "retrieval"
SYSTEM = SystemContext(reason="retrieval-evaluation")
PREFIX = "eval-retrieval-"
CANDIDATES = 50
EF_SEARCH = 100
ALL = SearchFilters()
LEX, SEM = RetrievalMethod.LEXICAL, RetrievalMethod.SEMANTIC

CONFIGURATIONS = (
    "lexical",
    "lexical_ts_rank_cd_only",
    "lexical_all_terms",
    "semantic",
    "hybrid_rrf_k60",
    "hybrid_rrf_k10",
    "hybrid_min_max_50_50",
)
PRODUCTION = "hybrid_rrf_k60"


def _write(line: str = "") -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Query:
    id: str
    category: str
    query: str
    relevant: tuple[str, ...]
    distractors: tuple[str, ...] = ()


def load_queries() -> list[Query]:
    raw = json.loads((ROOT / "queries.json").read_text(encoding="utf-8"))
    return [
        Query(
            id=q["id"],
            category=q["category"],
            query=q["query"],
            relevant=tuple(q["relevant"]),
            distractors=tuple(q.get("distractors", ())),
        )
        for q in raw["queries"]
    ]


# ---------------------------------------------------------------------------
# Ingestion, through the production stages
# ---------------------------------------------------------------------------


@dataclass
class Tenant:
    name: str
    ctx: AccessContext
    documents: dict[uuid.UUID, str] = field(default_factory=dict)  # id -> key


async def create_tenant(uow_factory: UnitOfWorkFactory, name: str) -> Tenant:
    async with uow_factory() as uow:
        user = await uow.users.create(
            email=f"{PREFIX}{name}-{uuid.uuid4().hex[:8]}@example.test",
            password_hash="$argon2id$eval",  # noqa: S106 -- a placeholder, never verified
            full_name=name,
        )
        workspace = await uow.workspaces.create(
            name=name, slug=f"{PREFIX}{name}-{uuid.uuid4().hex[:8]}", created_by_user_id=user.id
        )
        await uow.memberships.add_owner(workspace.id, user.id)
        await uow.commit()
    return Tenant(name, AccessContext(user_id=user.id, workspace_id=workspace.id, role=Role.OWNER))


async def ingest(
    uow_factory: UnitOfWorkFactory, provider: EmbeddingProvider, tenant: Tenant
) -> int:
    parser = default_parser_registry().resolve("text/markdown")
    normalizer, chunker = DocumentNormalizer(), StructureAwareChunker()
    embedder = ChunkEmbedder(uow_factory, provider, SystemClock(), SYSTEM)
    limits = ParseLimits()
    chunks_written = 0
    for path in sorted((ROOT / "corpus" / tenant.name).glob("*.md")):
        data = path.read_bytes()
        parsed = normalizer.normalize(parser.parse(io.BytesIO(data), limits=limits), limits=limits)
        drafts = chunker.chunk(parsed)
        embedded = await embedder.embed(drafts, workspace_id=tenant.ctx.workspace_id)
        digest = hashlib.sha256(data + tenant.name.encode()).hexdigest()
        async with uow_factory() as uow:
            document = await uow.documents.create(
                tenant.ctx,
                title=path.stem,
                folder_id=None,
                content=VersionContent(
                    storage_key=f"eval/{digest}",
                    content_sha256=digest,
                    byte_size=len(data),
                    content_type="text/markdown",
                    original_filename=path.name,
                ),
            )
            version = document.current_version
            assert version is not None  # noqa: S101
            await uow.processing.replace_chunks(
                SYSTEM,
                version,
                embedded.chunks,
                space=provider.space,
                chunker_version=chunker.version,
            )
            await uow.processing.transition_version(
                SYSTEM,
                version.id,
                expected=frozenset({ProcessingStatus.PENDING}),
                outcome=ProcessingOutcome.ready(chunk_count=len(embedded.chunks)),
            )
            await uow.commit()
        tenant.documents[document.id] = path.stem
        chunks_written += len(embedded.chunks)
    return chunks_written


# ---------------------------------------------------------------------------
# Dataset validation
# ---------------------------------------------------------------------------


async def lexemes(session: AsyncSession, value: str) -> set[str]:
    result = await session.execute(
        text("SELECT tsvector_to_array(to_tsvector('english', :v))"), {"v": value}
    )
    return set(result.scalar_one())


async def document_lexemes(session: AsyncSession, document_id: uuid.UUID) -> set[str]:
    result = await session.execute(
        text(
            "SELECT DISTINCT unnest(tsvector_to_array(search_vector)) FROM chunks"
            " WHERE document_id = :d"
        ),
        {"d": document_id},
    )
    return set(result.scalars())


async def validate(session: AsyncSession, queries: list[Query], acme: Tenant) -> list[str]:
    by_key = {key: doc_id for doc_id, key in acme.documents.items()}
    problems = []
    for q in queries:
        if q.category == "isolation":
            continue
        missing = [key for key in q.relevant if key not in by_key]
        if missing:
            problems.append(f"{q.id}: unknown relevant document(s) {missing}")
            continue
        query_terms = await lexemes(session, q.query)
        for key in q.relevant:
            doc_terms = await document_lexemes(session, by_key[key])
            if q.category == "paraphrase" and query_terms & doc_terms:
                problems.append(
                    f"{q.id}: paraphrase shares terms {sorted(query_terms & doc_terms)} with {key}"
                )
            if q.category == "identifier" and not query_terms <= doc_terms:
                problems.append(
                    f"{q.id}: identifier terms {sorted(query_terms - doc_terms)} absent from {key}"
                )
    return problems


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def ranked_documents(
    chunk_ids: Sequence[uuid.UUID], chunk_to_doc: dict[uuid.UUID, str]
) -> list[str]:
    seen: list[str] = []
    for chunk_id in chunk_ids:
        key = chunk_to_doc.get(chunk_id, "<unknown>")
        if key not in seen:
            seen.append(key)
    return seen


def precision(ranked: list[str], relevant: set[str], k: int) -> float:
    return sum(1 for d in ranked[:k] if d in relevant) / k


def recall(ranked: list[str], relevant: set[str], k: int) -> float:
    return sum(1 for d in ranked[:k] if d in relevant) / len(relevant)


def reciprocal_rank(ranked: list[str], relevant: set[str], k: int = 10) -> float:
    return next((1.0 / i for i, d in enumerate(ranked[:k], 1) if d in relevant), 0.0)


METRICS = ("p@1", "p@3", "r@5", "r@10", "mrr@10")


def score(ranked: list[str], relevant: set[str]) -> dict[str, float]:
    return {
        "p@1": precision(ranked, relevant, 1),
        "p@3": precision(ranked, relevant, 3),
        "r@5": recall(ranked, relevant, 5),
        "r@10": recall(ranked, relevant, 10),
        "mrr@10": reciprocal_rank(ranked, relevant),
    }


@dataclass
class QueryOutcome:
    id: str
    category: str
    query: str
    relevant: list[str]
    top: dict[str, list[str]]
    scores: dict[str, dict[str, float]]
    distractor_first: dict[str, bool]
    leaked: dict[str, int]


async def evaluate(  # noqa: PLR0913, PLR0917 -- one evaluation run's inputs
    uow_factory: UnitOfWorkFactory,
    database: Database,
    provider: EmbeddingProvider,
    queries: list[Query],
    acme: Tenant,
    globex: Tenant,
) -> tuple[list[QueryOutcome], list[str]]:
    chunk_to_doc: dict[uuid.UUID, str] = {}
    foreign: set[uuid.UUID] = set()
    async with database.session() as session:
        rows = await session.execute(
            text("SELECT id, document_id, workspace_id FROM chunks WHERE workspace_id IN (:a, :g)"),
            {"a": acme.ctx.workspace_id, "g": globex.ctx.workspace_id},
        )
        for chunk_id, document_id, workspace_id in rows:
            if workspace_id == acme.ctx.workspace_id:
                chunk_to_doc[chunk_id] = acme.documents[document_id]
            else:
                chunk_to_doc[chunk_id] = f"globex:{globex.documents[document_id]}"
                foreign.add(chunk_id)

    production = HybridSearch(
        uow_factory,
        provider,
        SearchPolicy(candidates_per_retriever=CANDIDATES, ef_search=EF_SEARCH),
    )
    outcomes: list[QueryOutcome] = []
    mismatches: list[str] = []
    for q in queries:
        normalized = normalize_query(q.query)
        vector = to_indexable_vector(await provider.embed_query(normalized), provider.space)
        async with database.session() as session, session.begin():
            coverage = SqlSearchRepository(session)
            density = SqlSearchRepository(session, lexical_ranking="ts_rank_cd")
            lex = await coverage.lexical_candidates(
                acme.ctx, normalized, match=LexicalMatch.ANY, filters=ALL, limit=CANDIDATES
            )
            lex_density = await density.lexical_candidates(
                acme.ctx, normalized, match=LexicalMatch.ANY, filters=ALL, limit=CANDIDATES
            )
            lex_all = await coverage.lexical_candidates(
                acme.ctx, normalized, match=LexicalMatch.ALL, filters=ALL, limit=CANDIDATES
            )
            sem = await coverage.semantic_candidates(
                acme.ctx,
                vector,
                space=provider.space,
                filters=ALL,
                limit=CANDIDATES,
                ef_search=EF_SEARCH,
            )

        def ids(candidates: Sequence[Candidate]) -> list[uuid.UUID]:
            return [c.chunk_id for c in candidates]

        lists = [RankedList(LEX, tuple(lex)), RankedList(SEM, tuple(sem))]
        chunk_rankings: dict[str, list[uuid.UUID]] = {
            "lexical": ids(lex),
            "lexical_ts_rank_cd_only": ids(lex_density),
            "lexical_all_terms": ids(lex_all),
            "semantic": ids(sem),
            "hybrid_rrf_k60": [c.chunk_id for c in reciprocal_rank_fusion(lists, k=60)],
            "hybrid_rrf_k10": [c.chunk_id for c in reciprocal_rank_fusion(lists, k=10)],
            "hybrid_min_max_50_50": [
                c.chunk_id for c in min_max_fusion(lists, weights={LEX: 0.5, SEM: 0.5})
            ],
        }

        # The production path must rank exactly as the harness's RRF does.
        response = await production.execute(acme.ctx, SearchQuery(text=q.query, limit=50))
        served = [r.chunk.id for r in response.results]
        if served != chunk_rankings[PRODUCTION][:50]:
            mismatches.append(q.id)

        relevant = set(q.relevant)
        top: dict[str, list[str]] = {}
        scores: dict[str, dict[str, float]] = {}
        distractor_first: dict[str, bool] = {}
        leaked: dict[str, int] = {}
        for name, chunk_ids in {**chunk_rankings, "production_use_case": served}.items():
            documents = ranked_documents(chunk_ids, chunk_to_doc)
            top[name] = documents[:5]
            leaked[name] = sum(1 for chunk_id in chunk_ids if chunk_id in foreign)
            if relevant:
                scores[name] = score(documents, relevant)
            if q.distractors:
                first_relevant = next(
                    (i for i, d in enumerate(documents) if d in relevant), len(documents)
                )
                distractor_first[name] = any(d in q.distractors for d in documents[:first_relevant])
        outcomes.append(
            QueryOutcome(
                id=q.id,
                category=q.category,
                query=q.query,
                relevant=list(q.relevant),
                top=top,
                scores=scores,
                distractor_first=distractor_first,
                leaked=leaked,
            )
        )
    return outcomes, mismatches


def summarize(outcomes: list[QueryOutcome]) -> dict[str, Any]:
    categories = sorted({o.category for o in outcomes if o.scores})
    summary: dict[str, Any] = {"overall": {}, "by_category": {c: {} for c in categories}}
    for name in CONFIGURATIONS:
        scored = [o for o in outcomes if o.scores]
        summary["overall"][name] = {
            metric: round(statistics.fmean(o.scores[name][metric] for o in scored), 3)
            for metric in METRICS
        }
        for category in categories:
            subset = [o for o in scored if o.category == category]
            summary["by_category"][category][name] = {
                metric: round(statistics.fmean(o.scores[name][metric] for o in subset), 3)
                for metric in METRICS
            }
        distracted = [o for o in outcomes if o.distractor_first]
        summary.setdefault("distractor_outranks_answer", {})[name] = sum(
            o.distractor_first[name] for o in distracted
        )
        summary.setdefault("leaked_results", {})[name] = sum(o.leaked[name] for o in outcomes)
    summary["leaked_results"]["production_use_case"] = sum(
        o.leaked["production_use_case"] for o in outcomes
    )
    return summary


def markdown(summary: dict[str, Any], queries: int) -> str:
    lines = [f"Overall ({queries} scored queries)", ""]
    header = "| Configuration | " + " | ".join(METRICS) + " |"
    rule = "|---|" + "---:|" * len(METRICS)
    lines += [header, rule]
    for name in CONFIGURATIONS:
        values = summary["overall"][name]
        lines.append(f"| {name} | " + " | ".join(f"{values[m]:.3f}" for m in METRICS) + " |")
    lines += ["", "MRR@10 by category", ""]
    categories = list(summary["by_category"])
    lines += [
        "| Configuration | " + " | ".join(categories) + " |",
        "|---|" + "---:|" * len(categories),
    ]
    for name in CONFIGURATIONS:
        lines.append(
            f"| {name} | "
            + " | ".join(f"{summary['by_category'][c][name]['mrr@10']:.3f}" for c in categories)
            + " |"
        )
    lines += ["", "Distractor ranked above the answer (of the distractor queries)", ""]
    lines += [f"- {n}: {summary['distractor_outranks_answer'][n]}" for n in CONFIGURATIONS]
    lines += ["", "Results leaked from the other tenant", ""]
    lines += [f"- {n}: {v}" for n, v in summary["leaked_results"].items()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def cleanup(database: Database) -> None:
    async with database.session() as session, session.begin():
        await session.execute(
            text("DELETE FROM workspaces WHERE slug LIKE :p"), {"p": f"{PREFIX}%"}
        )
        await session.execute(text("DELETE FROM users WHERE email LIKE :p"), {"p": f"{PREFIX}%"})


async def main(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings(database_url=args.database_url)
    database_name = make_url(args.database_url).database or ""
    if not database_name.endswith(("_test", "_bench")) and not args.force:
        msg = f"Refusing to evaluate against {database_name!r}; use a *_test or *_bench database."
        raise SystemExit(msg)

    database = Database(settings)
    uow_factory = make_unit_of_work_factory(database, cursor_secret="retrieval-eval")  # noqa: S106
    binding = build_embedding_provider(settings)
    provider = binding.provider
    queries = load_queries()
    try:
        await cleanup(database)
        acme = await create_tenant(uow_factory, "acme")
        globex = await create_tenant(uow_factory, "globex")
        acme_chunks = await ingest(uow_factory, provider, acme)
        globex_chunks = await ingest(uow_factory, provider, globex)
        _write(
            f"Ingested acme: {len(acme.documents)} documents / {acme_chunks} chunks; "
            f"globex: {len(globex.documents)} / {globex_chunks}. Provider: {provider.space.key}"
        )

        async with database.session() as session:
            problems = await validate(session, queries, acme)
        if problems:
            for problem in problems:
                _write(f"DATASET INVALID: {problem}")
            raise SystemExit(2)
        _write("Dataset claims verified (paraphrase: no shared terms; identifier: terms present).")

        outcomes, mismatches = await evaluate(
            uow_factory, database, provider, queries, acme, globex
        )
        summary = summarize(outcomes)
        report = {
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "provider": {
                "ai_provider": settings.ai_provider.value,
                "space": provider.space.key,
                "semantic_is_meaningful": settings.ai_provider is not AIProvider.FAKE,
            },
            "corpus": {
                "acme_documents": len(acme.documents),
                "acme_chunks": acme_chunks,
                "globex_documents": len(globex.documents),
                "globex_chunks": globex_chunks,
            },
            "settings": {
                "candidates_per_retriever": CANDIDATES,
                "ef_search": EF_SEARCH,
                "production_configuration": PRODUCTION,
            },
            "production_path_matches_harness": not mismatches,
            "production_path_mismatches": mismatches,
            "summary": summary,
            "queries": [asdict(o) for o in outcomes],
        }
        _write()
        _write(markdown(summary, sum(1 for o in outcomes if o.scores)))
        _write()
        _write(
            "Production HybridSearch ranks identically to harness RRF k=60: "
            + ("yes" if not mismatches else f"NO -- {mismatches}")
        )
        return report
    finally:
        if not args.keep:
            await cleanup(database)
        await binding.aclose()
        await database.dispose()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    import os  # noqa: PLC0415 -- only the CLI reads the environment

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        default=(
            os.environ.get("ORBIT_BENCH_DATABASE_URL") or os.environ.get("ORBIT_TEST_DATABASE_URL")
        ),
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="leave the evaluation data in place")
    parser.add_argument("--force", action="store_true", help="allow any database name")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url, ORBIT_BENCH_DATABASE_URL, or ORBIT_TEST_DATABASE_URL")
    return args


if __name__ == "__main__":
    arguments = parse_args(sys.argv[1:])
    result = asyncio.run(main(arguments))
    if arguments.output:
        arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
