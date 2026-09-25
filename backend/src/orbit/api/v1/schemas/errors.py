"""Error response contract.

One envelope for every failure, so a client has exactly one shape to parse.
``code`` is the machine-readable, stable half of the contract and is what
clients branch on; ``message`` is written for a person and may be reworded or
localised without breaking anyone.

See docs/decisions/0014-error-handling-strategy.md.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FieldError(BaseModel):
    """One validation failure, addressed to a specific input field."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(description="Dotted path to the offending field, e.g. 'body.email'.")
    message: str = Field(description="What is wrong with this field.")


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str = Field(
        description="Stable, machine-readable error code. Branch on this, not on `message`.",
        examples=["DOCUMENT_NOT_FOUND"],
    )
    message: str = Field(
        description="Human-readable summary, safe to display.",
        examples=["The requested document could not be found."],
    )
    request_id: str = Field(
        description="Correlation id for this request. Quote it when reporting a problem.",
        examples=["01JB2X8N4K7QF3TVWZ9M5PDCRA"],
    )
    details: list[FieldError] | None = Field(
        default=None,
        description="Field-level validation failures. Present only for validation errors.",
    )


class ErrorResponse(BaseModel):
    """The body returned by every non-2xx response."""

    model_config = ConfigDict(frozen=True)

    error: ErrorBody
