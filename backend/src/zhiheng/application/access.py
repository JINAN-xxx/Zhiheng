"""Server identity restoration and the intersection of Scope, App grants and domain ACLs."""

from dataclasses import dataclass, field
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.public import (
    APIRequestInfo,
    APIRequestService,
    DeveloperIdentity,
    KeyService,
    ResourceAction,
    ResourceGrantInfo,
    ResourceGrantService,
    ResourceKind,
    ResourceOwnership,
    Scope,
    SourceFileFact,
    resource_scope,
)
from zhiheng.modules.files.public import FileService
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.public import KnowledgeService
from zhiheng.modules.skills.public import SkillService
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability, require_resource_operation
from zhiheng.shared.principal import Principal


@dataclass(frozen=True)
class CallerContext:
    source: Literal['console', 'key']
    principal: Principal
    identity: DeveloperIdentity | None = None
    request_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class DomainAccess:
    actions: frozenset[str]
    ownership: ResourceOwnership
    source: SourceFileFact | None = None


class ResourceAccessService:
    def __init__(
        self,
        identity: IdentityService,
        keys: KeyService,
        grants: ResourceGrantService,
        requests: APIRequestService,
        files: FileService,
        knowledge: KnowledgeService,
        skills: SkillService,
    ) -> None:
        self.identity, self.keys, self.grants, self.requests = identity, keys, grants, requests
        self.files, self.knowledge, self.skills = files, knowledge, skills

    async def console(self, session: AsyncSession, cookie: str | None) -> CallerContext:
        return CallerContext('console', await self.identity.authenticate(session, cookie))

    async def bearer(self, session: AsyncSession, token: str, *, scope: Scope, lock: bool = True) -> CallerContext:
        identity = await self.keys.authenticate(session, token, lock=lock)
        await self.keys.authorize_identity(session, identity, [], scope=scope, lock=lock)
        principal = await self.identity.principal_for_user(session, identity.owner_id)
        return CallerContext('key', principal, identity)

    async def current(self, session: AsyncSession, caller: CallerContext, scope: Scope) -> CallerContext:
        if caller.identity is None or caller.source != 'key' or caller.principal.id != caller.identity.owner_id:
            raise AppError('invalid_api_key', '资源请求需要Bearer密钥', 401)
        await self.keys.authorize_identity(session, caller.identity, [], scope=scope, lock=True)
        principal = await self.identity.principal_for_user(session, caller.identity.owner_id)
        return CallerContext('key', principal, caller.identity, caller.request_id)

    @staticmethod
    def personal(principal: Principal) -> Principal:
        # An administrator's global visibility never grants a personal resource.
        return principal.personal()

    async def domain_actions(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: ResourceKind,
        resource_id: UUID,
        *,
        lock: bool,
        binding: bool = False,
        include_deleted: bool = False,
    ) -> DomainAccess:
        principal = self.personal(principal)
        if kind == 'file':
            info, actions = await self.files.delegable_access(
                session, principal, resource_id, lock=lock, active=binding
            )
            stamp = await self.files.ownership_stamp(session, info.source_file_id or info.id)
            return DomainAccess(
                actions,
                ResourceOwnership(info.owner_id, stamp),
                SourceFileFact(info.id, info.owner_id, info.size, info.status, info.source_file_id),
            )
        if kind == 'knowledge_base':
            base, actions = await self.knowledge.delegable_access(
                session, principal, resource_id, lock=lock, include_deleted=include_deleted
            )
            return DomainAccess(actions, ResourceOwnership(base.owner_id, str(base.owner_id)))
        if kind == 'skill':
            skill = await self.skills.access(session, principal.id, resource_id, lock=lock, include_deleted=True)
            return DomainAccess(
                frozenset({'read', 'write', 'delete'}), ResourceOwnership(skill.owner_id, str(skill.owner_id))
            )
        raise AppError('resource_not_found', '资源不存在', 404)

    async def require(
        self,
        session: AsyncSession,
        caller: CallerContext,
        kind: ResourceKind,
        resource_id: UUID,
        action: ResourceAction,
        *,
        lock: bool = True,
        include_deleted: bool = False,
        operation_scope: Scope | None = None,
    ) -> None:
        """Preflight with lock=False, then lock Models/Connections before final domain locking."""
        current = await self.current(session, caller, operation_scope or resource_scope(kind, action))
        assert current.identity is not None
        binding = await self.grants.require(session, current.identity.application_id, kind, resource_id, action)
        facts = await self.domain_actions(
            session, current.principal, kind, resource_id, lock=lock, include_deleted=include_deleted
        )
        if action not in facts.actions or binding != facts.ownership:
            raise AppError('resource_not_found', '资源不存在', 404)

    async def bind(
        self,
        session: AsyncSession,
        principal: Principal,
        application_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        actions: list[ResourceAction],
        *,
        expected_version: int,
    ) -> ResourceGrantInfo:
        principal = await self.identity.principal_for_user(session, principal.id)
        await self.grants.lock_owned(session, principal.id, application_id, expected_version)
        if kind in {'knowledge_base', 'skill'}:
            module = 'knowledge' if kind == 'knowledge_base' else 'skills'
            if 'delete' in actions:
                require_capability(principal, module, 'delete')
            if 'write' in actions:
                operations = (
                    ('create', 'update', 'import', 'rebuild')
                    if kind == 'knowledge_base'
                    else ('create', 'update', 'toggle')
                )
                principal.require_any_permission(*(f'platform:{module}:{operation}' for operation in operations))
            if 'query' in actions:
                require_capability(principal, module, 'query')
            require_capability(principal, module, 'read')
        facts = await self.domain_actions(session, principal, kind, resource_id, lock=True, binding=True)
        if not set(actions) <= facts.actions:
            raise AppError('resource_not_found', '资源不存在', 404)
        return await self.grants.bind_verified(
            session,
            principal.id,
            application_id,
            kind,
            resource_id,
            tuple(actions),
            ownership=facts.ownership,
            expected_version=expected_version,
            source=facts.source,
        )

    async def autobind(
        self, session: AsyncSession, caller: CallerContext, kind: ResourceKind, resource_id: UUID
    ) -> ResourceGrantInfo:
        """Call in the creation transaction, after Key/App admission and domain registration."""
        caller = await self.current(session, caller, resource_scope(kind, 'write'))
        assert caller.identity is not None
        facts = await self.domain_actions(session, caller.principal, kind, resource_id, lock=True, binding=True)
        # Newly created resources must belong to the authenticated owner, never a shared target.
        if facts.ownership.owner_id != caller.principal.id:
            raise AppError('resource_not_found', '资源不存在', 404)
        return await self.grants.bind_verified(
            session,
            caller.principal.id,
            caller.identity.application_id,
            kind,
            resource_id,
            cast('tuple[ResourceAction, ...]', tuple(sorted(facts.actions))),
            ownership=facts.ownership,
            source=facts.source,
        )

    async def unbind(
        self,
        session: AsyncSession,
        principal: Principal,
        application_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        *,
        expected_version: int,
    ) -> int:
        current = await self.identity.principal_for_user(session, principal.id)
        return await self.grants.unbind(
            session, current.id, application_id, kind, resource_id, expected_version=expected_version
        )

    async def release_purged(self, session: AsyncSession, application_id: UUID, file_id: UUID) -> None:
        # Trusted purge orchestration; no client-supplied status/size/owner facts.
        await self.grants.lock_accounting(session, application_id)
        fact = await self.files.accounting_fact(session, file_id, lock=True)
        await self.grants.release_verified(
            session, application_id, SourceFileFact(fact.id, fact.owner_id, fact.size, fact.status, fact.source_file_id)
        )

    async def begin_request(
        self,
        session: AsyncSession,
        caller: CallerContext,
        operation: str,
        key: UUID,
        payload: dict[str, object],
        *,
        scope: Scope,
        resources: tuple[dict[str, str], ...] = (),
    ) -> APIRequestInfo:
        caller = await self.current(session, caller, scope)
        assert caller.identity is not None
        info = await self.requests.begin(
            session, caller.identity, operation, key, payload, scope=scope, resources=resources
        )
        await self.authorize_request(session, caller, info)
        return info

    async def authorize_request(
        self, session: AsyncSession, caller: CallerContext, info: APIRequestInfo, *, lock: bool = True
    ) -> None:
        current = await self.current(session, caller, info.required_scope)
        require_resource_operation(current.principal, info.operation)
        if current.identity is None or current.identity.application_id != info.application_id:
            raise AppError('request_not_found', '请求不存在', 404)
        for target in sorted(
            info.resources, key=lambda item: ({'knowledge_base': 0, 'skill': 1, 'file': 2}[item['kind']], item['id'])
        ):
            await self.require(
                session,
                current,
                cast('ResourceKind', target['kind']),
                UUID(target['id']),
                cast('ResourceAction', target['action']),
                lock=lock,
                include_deleted=info.operation == 'knowledge.delete_base',
            )
        if info.replayed and info.snapshot_expired:
            details: dict[str, object] = {
                'request_id': str(info.id),
                'result_kind': info.result_kind,
                'result_id': str(info.result_id) if info.result_id else None,
            }
            raise AppError('idempotency_expired', '原请求响应快照已过期，请读取原结果', 409, details=details)
