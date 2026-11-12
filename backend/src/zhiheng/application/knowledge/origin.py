"""Restore original API execution authority from permanent native-result associations."""

from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.modules.developer.public import APIOrigin, DeveloperIdentity, ResourceAction, ResourceKind
from zhiheng.modules.knowledge.public import RetrievalState


class KnowledgeOriginGuard:
    def __init__(self, access: ResourceAccessService) -> None:
        self.access = access

    async def job(
        self, session: AsyncSession, job_id: UUID, *, model_ids: tuple[UUID, ...] = (), lock: bool = False
    ) -> APIOrigin | None:
        origin = await self.access.requests.origin(session, 'knowledge_job', job_id)
        if origin is None:
            return None
        identity = DeveloperIdentity(origin.application_id, origin.key_id, origin.owner_id)
        request = await self.access.requests.get(session, origin.application_id, origin.request_id)
        await self.access.keys.authorize_identity(session, identity, model_ids, scope=request.required_scope, lock=True)
        principal = await self.access.identity.principal_for_user(session, origin.owner_id)
        caller = CallerContext('key', principal, identity, origin.request_id)
        for resource in sorted(request.resources, key=lambda item: (item['kind'] == 'file', item['id'])):
            await self.access.require(
                session,
                caller,
                cast('ResourceKind', resource['kind']),
                UUID(resource['id']),
                cast('ResourceAction', resource['action']),
                lock=lock,
                include_deleted=request.operation == 'knowledge.delete_base',
            )
            if resource['kind'] == 'file' and not request.operation.startswith('knowledge.delete'):
                await self.access.files.require(
                    session, self.access.personal(principal), UUID(resource['id']), active=True, lock=lock
                )
        return origin

    async def retrieval(self, session: AsyncSession, state: RetrievalState) -> None:
        origin = await self.access.requests.origin(session, 'knowledge_preparation', state.id)
        if origin is None:
            return
        identity = DeveloperIdentity(origin.application_id, origin.key_id, origin.owner_id)
        principal = await self.access.identity.principal_for_user(session, origin.owner_id)
        caller = CallerContext('key', principal, identity, origin.request_id)
        await self.access.require(session, caller, 'knowledge_base', state.resource_id, 'query', lock=False)
