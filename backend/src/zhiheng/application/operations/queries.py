"""Current App/Scope/ACL views over permanent pointers to original native work."""

from datetime import datetime
from http import HTTPStatus
from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.application.knowledge_preparation import KnowledgePreparation
from zhiheng.application.operations.contracts import OperationInfo, OperationStatus
from zhiheng.infrastructure.database import Database
from zhiheng.modules.developer.public import APIRequestInfo, ResourceAction, ResourceKind, Scope
from zhiheng.modules.knowledge.public import JOB_TERMINAL, PREPARATION_TERMINAL, KnowledgeService
from zhiheng.runtime.contracts import TERMINAL_STATUSES
from zhiheng.shared.errors import AppError

PAGE_BATCH = 100


class OperationQueries:
    def __init__(
        self,
        database: Database,
        access: ResourceAccessService,
        knowledge: KnowledgeService,
        preparation: KnowledgePreparation,
    ) -> None:
        self.database, self.access, self.knowledge, self.preparation = database, access, knowledge, preparation

    async def authorize(
        self, session: AsyncSession, caller: CallerContext, request: APIRequestInfo, *, cancel: bool = False
    ) -> None:
        query = request.result_kind == 'knowledge_preparation'
        scope: Scope = 'knowledge:write' if cancel else ('knowledge:query' if query else 'knowledge:read')
        caller = await self.access.current(session, caller, scope)
        if (
            caller.identity is None
            or caller.identity.application_id != request.application_id
            or request.owner_id != caller.principal.id
        ):
            raise AppError('operation_not_found', '操作不存在', 404)
        for target in sorted(request.resources, key=lambda item: (item['kind'] == 'file', item['id'])):
            if target['kind'] == 'file':
                continue
            kind = cast('ResourceKind', target['kind'])
            action = cast('ResourceAction', target['action'])
            if kind == 'knowledge_base':
                action = 'query' if query else ('write' if cancel else 'read')
            await self.access.require(
                session,
                caller,
                kind,
                UUID(target['id']),
                action,
                lock=False,
                include_deleted=request.operation == 'knowledge.delete_base',
                operation_scope=scope if cancel else None,
            )

    async def project(self, session: AsyncSession, caller: CallerContext, request: APIRequestInfo) -> OperationInfo:
        assert request.result_id is not None
        if request.result_kind == 'knowledge_job':
            job = await self.knowledge.get_job_info(session, self.access.personal(caller.principal), request.result_id)
            visible = []
            for document in job.document_results:
                try:
                    await self.access.files.require(
                        session, self.access.personal(caller.principal), document.file_id, active=True
                    )
                except AppError as error:
                    if error.status_code != HTTPStatus.NOT_FOUND:
                        raise
                    continue
                visible.append(document)
            job.document_results = visible
            status = {'pending': 'queued', 'completed': 'succeeded', 'partial': 'partially_succeeded'}.get(
                job.status, job.status
            )
            status = await self.cancel_status(session, job.id, status)
            if status not in {
                'queued',
                'running',
                'succeeded',
                'partially_succeeded',
                'cancel_requested',
                'cancelled',
                'failed',
                'interrupted',
            }:
                status = 'running'
            return OperationInfo(
                id=request.id,
                kind='knowledge_job',
                native_id=job.id,
                status=cast('OperationStatus', status),
                phase=job.phase,
                progress={
                    'total': job.total_documents,
                    'completed': job.completed_documents,
                    'failed': job.failed_documents,
                },
                cancel_allowed=job.status not in JOB_TERMINAL and job.status != 'cancel_requested',
                result=job.model_dump(mode='json'),
                error=job.error,
                created_at=request.created_at,
                updated_at=job.updated_at,
            )
        if request.result_kind == 'knowledge_preparation':
            row = await self.preparation.store.get(session, request.result_id)
            result = self.preparation.store.info(row)
            result.hits = await self.preparation.visible_hits(
                session, self.access.personal(caller.principal), result.hits
            )
            if caller.identity is not None:
                key = await self.access.keys.info_for_identity(session, caller.identity)
                if 'knowledge:query' not in key.scopes:
                    result.hits = []
            status = 'succeeded' if row.status in {'completed', 'admitted'} else row.status
            status = await self.cancel_status(session, row.id, status)
            if status not in {
                'queued',
                'running',
                'succeeded',
                'cancel_requested',
                'cancelled',
                'failed',
                'interrupted',
            }:
                status = 'running'
            return OperationInfo(
                id=request.id,
                kind='knowledge_preparation',
                native_id=row.id,
                status=cast('OperationStatus', status),
                phase=row.phase,
                progress={},
                cancel_allowed=row.status not in PREPARATION_TERMINAL and row.status != 'cancel_requested',
                result=result.model_dump(mode='json'),
                error=result.error,
                created_at=request.created_at,
                updated_at=row.updated_at,
            )
        raise AppError('operation_not_found', '操作不存在', 404)

    async def cancel_status(self, session: AsyncSession, native_id: UUID, status: str) -> str:
        if status != 'cancelled':
            return status
        for child_id in await self.preparation.steps.children(session, native_id):
            child = await self.preparation.runs.resource_snapshot(session, child_id, 'knowledge_embedding', native_id)
            if child.status not in TERMINAL_STATUSES:
                return 'cancel_requested'
        return status

    async def get(self, caller: CallerContext, request_id: UUID) -> OperationInfo:
        assert caller.identity is not None
        async with self.database.sessions.begin() as session:
            request = await self.access.requests.get(session, caller.identity.application_id, request_id)
            await self.authorize(session, caller, request)
            return await self.project(session, caller, request)

    async def page(self, caller: CallerContext, after: tuple[datetime, UUID] | None, limit: int) -> list[OperationInfo]:
        assert caller.identity is not None
        async with self.database.sessions.begin() as session:
            key = await self.access.keys.revalidate(session, caller.identity, lock=True)
            if not set(key.scopes) & {'knowledge:read', 'knowledge:query'}:
                raise AppError('insufficient_scope', 'Key lacks the required scope', 403)
            found: list[OperationInfo] = []
            while len(found) < limit:
                rows = await self.access.requests.operation_page(
                    session, caller.identity.application_id, after=after, limit=PAGE_BATCH
                )
                if not rows:
                    break
                for request in rows:
                    try:
                        await self.authorize(session, caller, request)
                        found.append(await self.project(session, caller, request))
                    except AppError as error:
                        if error.status_code not in {403, 404}:
                            raise
                    if len(found) >= limit:
                        break
                after = (rows[-1].created_at, rows[-1].id)
                if len(rows) < PAGE_BATCH:
                    break
            return found
