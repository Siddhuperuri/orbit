"""Tag use cases: list, create, update, delete."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import ValidationError
from orbit.domain.models.entities import Tag, TagListing
from orbit.domain.organization import (
    MAX_TAGS_PER_WORKSPACE,
    normalize_tag_name,
    require_tag_color,
)
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class ListTags:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext) -> Sequence[TagListing]:
        ctx.require(Permission.TAG_READ)
        async with self._uow_factory() as uow:
            return await uow.tags.list_all(ctx)


class CreateTag:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, *, name: str, color: str | None) -> Tag:
        ctx.require(Permission.TAG_WRITE)
        clean = normalize_tag_name(name)
        chosen = require_tag_color(color)
        async with self._uow_factory() as uow:
            if await uow.tags.count(ctx) >= MAX_TAGS_PER_WORKSPACE:
                msg = f"A workspace can have at most {MAX_TAGS_PER_WORKSPACE} tags."
                raise ValidationError(msg)
            tag = await uow.tags.create(ctx, name=clean, color=chosen)
            await uow.commit()
        return tag


class UpdateTag:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        ctx: AccessContext,
        tag_id: uuid.UUID,
        *,
        name: str | None,
        color: str | None,
        expected_version: int,
    ) -> Tag:
        ctx.require(Permission.TAG_WRITE)
        if name is None and color is None:
            msg = "Nothing to change: send a name, a colour, or both."
            raise ValidationError(msg)
        clean = normalize_tag_name(name) if name is not None else None
        chosen = require_tag_color(color) if color is not None else None
        async with self._uow_factory() as uow:
            tag = await uow.tags.update(
                ctx, tag_id, name=clean, color=chosen, expected_version=expected_version
            )
            await uow.commit()
        return tag


class DeleteTag:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, tag_id: uuid.UUID) -> None:
        ctx.require(Permission.TAG_WRITE)
        async with self._uow_factory() as uow:
            await uow.tags.delete(ctx, tag_id)
            await uow.commit()
