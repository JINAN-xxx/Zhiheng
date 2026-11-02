"""Permanent application request identity and links to native execution facts."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.keys import DeveloperIdentity, KeyService
from zhiheng.modules.developer.orm import APIRequest
from zhiheng.modules.developer.scopes import ResourceAction, ResourceKind, Scope, validate_actions
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

MAX_OPERATION_LENGTH = 80


def normalized_resources(resources: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    """Route target identities are part of request semantics, independent of JSON member order."""
    values: dict[tuple[str, str, str], dict[str, str]] = {}
    try:
        for target in resources:
            kind, action, identifier = target['kind'], target['action'], str(UUID(target['id']))
            if kind not in {'file', 'knowledge_base', 'skill'} or action not in {'read', 'write', 'query', 'delete'}:
                raise ValueError('Invalid resource requirement')
            validate_actions(cast('ResourceKind', kind), (cast('ResourceAction', action),))
            values[(kind, identifier, action)] = {'kind': kind, 'id': identifier, 'action': action}
    except (KeyError, TypeError, ValueError):
        raise AppError('invalid_request', 'Invalid resource requirements', 422) from None
    return [values[key] for key in sorted(values)]


@dataclass(frozen=True)
class APIRequestInfo:
    id: UUID
    application_id: UUID
    key_id: UUID
    owner_id: UUID
    operation: str
    required_scope: Scope
    resources: tuple[dict[str, str], ...]
    native_request_id: UUID
    result_kind: str | None
    result_id: UUID | None
    snapshot: dict[str, object] | None
    snapshot_expired: bool
    replayed: bool
    created_at: datetime

    @property
    def result_pending(self) -> bool:
        """An unresolved admission has no native pointer; execution state belongs to the native job."""
        return self.result_id is None


@dataclass(frozen=True)
class APIOrigin:
    owner_id: UUID
    application_id: UUID
    key_id: UUID
    request_id: UUID


class APIRequestService:
    def __init__(self, keys: KeyService) -> None:
        self.keys = keys

    async def begin(
        self,
        session: AsyncSession,
        identity: DeveloperIdentity,
        operation: str,
        idempotency_key: UUID,
        payload: dict[str, object],
        *,
        scope: Scope,
        resources: tuple[dict[str, str], ...] = (),
    ) -> APIRequestInfo:
        """Trusted application use only; replay output still requires current domain authorization."""
        if not isinstance(idempotency_key, UUID) or not operation or len(operation) > MAX_OPERATION_LENGTH:
            raise AppError('invalid_idempotency_key', 'UUID请求键及有效操作名为必填', 422)
        await self.keys.authorize_identity(session, identity, [], scope=scope, lock=True)
        targets = normalized_resources(resources)
        try:
            canonical = json.dumps(
                {'payload': payload, 'targets': targets},
                sort_keys=True,
                separators=(',', ':'),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise AppError('invalid_request', '请求语义必须为规范JSON', 422) from None
        value = hashlib.sha256(canonical.encode()).hexdigest()
        # Key -> App was locked above. Serializing on App also covers different Keys.
        row = await session.scalar(
            select(APIRequest)
            .where(
                APIRequest.application_id == identity.application_id,
                APIRequest.operation == operation,
                APIRequest.idempotency_key == idempotency_key,
            )
            .execution_options(populate_existing=True)
        )
        if row is not None:
            if row.semantic_hash != value:
                raise AppError('idempotency_conflict', '相同请求键不能用于不同内容', 409)
            return self.info(row, replayed=True)
        row = APIRequest(
            application_id=identity.application_id,
            key_id=identity.key_id,
            owner_id=identity.owner_id,
            operation=operation,
            idempotency_key=idempotency_key,
            semantic_hash=value,
            required_scope=scope,
            resources=targets,
            snapshot_expires_at=utcnow() + timedelta(hours=24),
        )
        session.add(row)
        await session.flush()
        return self.info(row)

    async def complete(
        self,
        session: AsyncSession,
        request_id: UUID,
        *,
        result_kind: str,
        result_id: UUID,
        snapshot: dict[str, object],
        resources: tuple[dict[str, str], ...] | None = None,
    ) -> APIRequestInfo:
        row = await session.scalar(
            select(APIRequest)
            .where(APIRequest.id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AppError('request_not_found', '请求不存在', 404)
        if row.result_id is not None:
            if row.result_id != result_id or row.result_kind != result_kind:
                raise AppError('idempotency_conflict', '请求已关联原生结果', 409)
            if resources is not None:
                row.resources = normalized_resources((*row.resources, *resources))
                await session.flush()
            return self.info(row, replayed=True)
        row.result_kind, row.result_id, row.snapshot = result_kind, result_id, snapshot
        if resources is not None:
            row.resources = normalized_resources((*row.resources, *resources))
        await session.flush()
        return self.info(row)

    async def get(self, session: AsyncSession, application_id: UUID, request_id: UUID) -> APIRequestInfo:
        row = await session.scalar(
            select(APIRequest)
            .where(APIRequest.id == request_id, APIRequest.application_id == application_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AppError('request_not_found', '请求不存在', 404)
        return self.info(row)

    async def origin(self, session: AsyncSession, result_kind: str, result_id: UUID) -> APIOrigin | None:
        row = await session.scalar(
            select(APIRequest).where(APIRequest.result_kind == result_kind, APIRequest.result_id == result_id)
        )
        return APIOrigin(row.owner_id, row.application_id, row.key_id, row.id) if row is not None else None

    async def operation_page(
        self, session: AsyncSession, application_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[APIRequestInfo]:
        query = select(APIRequest).where(
            APIRequest.application_id == application_id,
            APIRequest.result_kind.in_(['knowledge_job', 'knowledge_preparation']),
        )
        if after is not None:
            timestamp, identifier = after
            query = query.where(
                (APIRequest.created_at < timestamp)
                | ((APIRequest.created_at == timestamp) & (APIRequest.id < identifier))
            )
        return [
            self.info(row)
            for row in await session.scalars(
                query.order_by(APIRequest.created_at.desc(), APIRequest.id.desc()).limit(limit)
            )
        ]

    @staticmethod
    def info(row: APIRequest, *, replayed: bool = False) -> APIRequestInfo:
        expired = row.snapshot_expires_at <= utcnow()
        return APIRequestInfo(
            row.id,
            row.application_id,
            row.key_id,
            row.owner_id,
            row.operation,
            cast('Scope', row.required_scope),
            tuple(row.resources),
            uuid5(row.id, 'native-request'),
            row.result_kind,
            row.result_id,
            None if expired else row.snapshot,
            expired,
            replayed,
            row.created_at,
        )
