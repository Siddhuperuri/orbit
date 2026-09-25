"""Generic pagination request/response contract.

One shape for every list endpoint, so a frontend list component written once
against this envelope works for documents, members, or anything added later.
"""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from orbit.domain.models.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

ItemT = TypeVar("ItemT")

# A bound narrower than MAX_PAGE_SIZE would be redundant validation; this is
# the single place the client-facing limit is declared, and the domain layer's
# `clamp_limit` is the server-side backstop if it is ever bypassed.
PageLimit = Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE, default=DEFAULT_PAGE_SIZE)]


class PageResponse(BaseModel, Generic[ItemT]):
    model_config = ConfigDict(frozen=True)

    items: list[ItemT]
    next_cursor: str | None = Field(
        default=None,
        description="Pass as `cursor` to fetch the next page. Absent on the last page.",
    )
    has_more: bool
