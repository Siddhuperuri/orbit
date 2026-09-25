"""Folder use cases: list, create, rename, delete."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from orbit.domain.access import AccessContext, Permission
from orbit.domain.errors import ValidationError
from orbit.domain.models.entities import Folder, FolderListing
from orbit.domain.organization import MAX_FOLDERS_PER_WORKSPACE, normalize_folder_name
from orbit.domain.ports.unit_of_work import UnitOfWorkFactory


class ListFolders:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext) -> Sequence[FolderListing]:
        ctx.require(Permission.FOLDER_READ)
        async with self._uow_factory() as uow:
            return await uow.folders.list_all(ctx)


class CreateFolder:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, ctx: AccessContext, *, name: str, parent_id: uuid.UUID | None
    ) -> Folder:
        ctx.require(Permission.FOLDER_WRITE)
        clean = normalize_folder_name(name)
        async with self._uow_factory() as uow:
            # The tree is returned whole, so it has to be bounded. Checked before
            # the insert; two simultaneous creations can overshoot by one, which
            # a soft product limit tolerates.
            if await uow.folders.count(ctx) >= MAX_FOLDERS_PER_WORKSPACE:
                msg = f"A workspace can have at most {MAX_FOLDERS_PER_WORKSPACE} folders."
                raise ValidationError(msg)
            folder = await uow.folders.create(ctx, name=clean, parent_id=parent_id)
            await uow.commit()
        return folder


class RenameFolder:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, ctx: AccessContext, folder_id: uuid.UUID, *, name: str, expected_version: int
    ) -> Folder:
        ctx.require(Permission.FOLDER_WRITE)
        clean = normalize_folder_name(name)
        async with self._uow_factory() as uow:
            folder = await uow.folders.rename(
                ctx, folder_id, name=clean, expected_version=expected_version
            )
            await uow.commit()
        return folder


class DeleteFolder:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, ctx: AccessContext, folder_id: uuid.UUID) -> None:
        ctx.require(Permission.FOLDER_WRITE)
        async with self._uow_factory() as uow:
            await uow.folders.delete(ctx, folder_id)
            await uow.commit()
