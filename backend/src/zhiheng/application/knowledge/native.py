"""Shared native knowledge commands for Console and platform resource admission."""

from dataclasses import replace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.public import lock_billing_admission
from zhiheng.modules.knowledge.public import (
    BaseCreate,
    BaseInfo,
    BasePatch,
    BuildSelection,
    FolderCreate,
    FolderInfo,
    FolderPatch,
    JobInfo,
    KnowledgeService,
    fingerprint,
)
from zhiheng.modules.models.public import ModelBinding, ModelService
from zhiheng.shared.principal import Principal


class NativeKnowledgeCommands:
    def __init__(self, knowledge: KnowledgeService, models: ModelService) -> None:
        self.knowledge, self.models = knowledge, models

    async def create(self, session: AsyncSession, principal: Principal, body: BaseCreate) -> BaseInfo:
        body = await self.knowledge.creation_parameters(session, body)
        assert body.embed_model_id is not None
        binding = await self.models.resolve(session, body.embed_model_id)
        return await self.knowledge.create(session, principal, body, binding)

    async def patch(self, session: AsyncSession, principal: Principal, base_id: UUID, body: BasePatch) -> BaseInfo:
        return await self.knowledge.patch(session, principal, base_id, body)

    async def folder_create(
        self, session: AsyncSession, principal: Principal, base_id: UUID, body: FolderCreate
    ) -> FolderInfo:
        return await self.knowledge.create_folder(session, principal, base_id, body)

    async def folder_patch(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_id: UUID, body: FolderPatch
    ) -> FolderInfo:
        return await self.knowledge.rename_folder(session, principal, base_id, folder_id, body)

    async def select(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        operation: str,
        selection: BuildSelection,
        version: int | None,
        child_id: UUID | None,
    ) -> BuildSelection:
        base = await self.knowledge.authorize_base(
            session, principal, base_id, manage=True, lock=True, include_deleted=operation == 'delete_base'
        )
        self.knowledge.idle(base)
        if operation == 'delete_base':
            await self.knowledge.confirm_deletion(session, principal, base_id, confirmed=selection.confirm_references)
        if operation in {'rebuild', 'delete_base'}:
            assert version is not None
            self.knowledge.version(base.version, version)
            return replace(
                selection,
                document_ids=await self.knowledge.all_document_ids(
                    session, base_id, include_tombstoned=operation == 'delete_base'
                ),
            )
        if operation in {'rechunk', 'delete_document'}:
            assert child_id is not None and version is not None
            document = await self.knowledge.document_info(
                session, base_id, child_id, include_deleted=operation.startswith('delete')
            )
            self.knowledge.version(document.version, version)
            return replace(selection, document_ids=[child_id])
        if operation == 'delete_folder':
            assert child_id is not None and version is not None
            folder = await self.knowledge.authorize_folder(session, base_id, child_id, include_deleted=True)
            assert folder is not None
            self.knowledge.version(folder.version, version)
            return replace(
                selection, document_ids=await self.knowledge.deletion_scope(session, principal, base_id, child_id)
            )
        return selection

    async def queue(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        operation: str,
        request_id: UUID,
        value: str,
        binding: ModelBinding,
        selection: BuildSelection,
    ) -> JobInfo:
        return await self.knowledge.queue(
            session,
            principal,
            base_id,
            operation=operation,
            request_id=request_id,
            value=value,
            binding=binding,
            selection=selection,
        )

    async def console_job(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        operation: str,
        request_id: UUID,
        payload: dict[str, object],
        selection: BuildSelection,
        *,
        version: int | None = None,
        child_id: UUID | None = None,
    ) -> JobInfo:
        await lock_billing_admission(session)
        base = await self.knowledge.authorize_base(
            session, principal, base_id, manage=True, include_deleted=operation == 'delete_base'
        )
        parts: list[object] = [operation, str(base_id)]
        if child_id is not None:
            parts.append(str(child_id))
        value = fingerprint([*parts, payload])
        existing = await self.knowledge.existing_job(session, principal, request_id, value)
        if existing is not None:
            return existing
        binding = (
            await self.knowledge.deletion_binding(session, base_id)
            if operation.startswith('delete')
            else await self.models.resolve(session, base.embed_model_id)
        )
        if operation == 'import':
            roots = await self.knowledge.files.source_root_ids(session, tuple(selection.file_ids or []))
            await self.knowledge.lock_library_sources(session, principal, roots, additional_base_ids=(base_id,))
        selected = await self.select(session, principal, base_id, operation, selection, version, child_id)
        return await self.queue(session, principal, base_id, operation, request_id, value, binding, selected)
