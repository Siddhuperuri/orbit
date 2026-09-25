"""Search endpoint.

`POST`, not `GET`: the query text is user content and often sensitive ("salary
band for Jane"), and a query string ends up in access logs, proxy logs, and
browser history. A body does not.

Authorization is the same as every workspace route: the access context is
resolved from the path's workspace and the caller's membership (a non-member
gets 404), and the use case requires `search:query`. The workspace then
constrains both retrievers inside their SQL.
"""

from __future__ import annotations

from fastapi import APIRouter

from orbit.api.deps import AccessContextDep, ClientIpDep, SearchDep
from orbit.api.v1.schemas.search import SearchRequest, SearchResponseOut
from orbit.domain.retrieval import SearchFilters, SearchQuery

router = APIRouter(prefix="/workspaces/{workspace_id}/search", tags=["search"])


@router.post(
    "",
    response_model=SearchResponseOut,
    summary="Search the workspace's documents",
    description=(
        "Hybrid retrieval: PostgreSQL full-text search and vector similarity, "
        "fused with Reciprocal Rank Fusion (k = 60). `mode` selects either "
        "retriever alone. Each result carries its document, version, chunk, "
        "source location, and each retriever's rank and score. If the embedding "
        "provider is unavailable, a hybrid request is answered lexically and "
        "says so in `retrievers` and `degraded`. "
        "Filters (`document_ids`, `folder_id`/`unfiled`, `tag_ids`, "
        "`content_types`) narrow the search inside the retrievers' SQL and can "
        "never widen it. Paging is by `offset`; `has_more` says whether another "
        "page exists. The `relevance` numbers are diagnostics for evaluation, "
        "not values to show a reader. Rate limited per account and per client "
        "address; exceeding either returns 429 with `Retry-After`."
    ),
)
async def search(
    body: SearchRequest,
    ctx: AccessContextDep,
    search_use_case: SearchDep,
    client_ip: ClientIpDep,
) -> SearchResponseOut:
    response = await search_use_case.execute(
        ctx,
        SearchQuery(
            text=body.query,
            limit=body.limit,
            offset=body.offset,
            mode=body.mode,
            filters=SearchFilters(
                document_ids=frozenset(body.document_ids) if body.document_ids else None,
                folder_id=body.folder_id,
                unfiled=body.unfiled,
                tag_ids=frozenset(body.tag_ids) if body.tag_ids else None,
                content_types=frozenset(body.content_types) if body.content_types else None,
            ),
        ),
        client_ip=client_ip,
    )
    return SearchResponseOut.from_domain(response)
