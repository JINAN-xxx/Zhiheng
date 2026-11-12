"""Verified resource bindings and source accounting; domain ACLs live in their domains."""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.applications import ApplicationService
from zhiheng.modules.developer.orm import DeveloperApplication, ResourceGrant, SourceEnrollment
from zhiheng.modules.developer.scopes import ResourceAction, ResourceKind, validate_actions
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import ResourceReference
from zhiheng.shared.time import utcnow


@dataclass(frozen=True)
class ResourceOwnership:
    owner_id: UUID
    stamp: str


@dataclass(frozen=True)
class ResourceGrantInfo:
    application_id: UUID
    kind: ResourceKind
    resource_id: UUID
    actions: tuple[ResourceAction, ...]
    application_version: int
    ownership: ResourceOwnership


@dataclass(frozen=True)
class SourceFileFact:
    id: UUID
    owner_id: UUID
    size: int
    status: str
    source_file_id: UUID | None


class ResourceGrantService:
    def __init__(self, applications: ApplicationService) -> None:
        self.applications = applications

    async def knowledge_references(self, session: AsyncSession, base_id: UUID) -> list[ResourceReference]:
        """返回尚未删除应用的知识库资源授权。"""
        rows = await session.scalars(
            select(DeveloperApplication)
            .join(ResourceGrant, ResourceGrant.application_id == DeveloperApplication.id)
            .where(
                DeveloperApplication.deleted_at.is_(None),
                ResourceGrant.kind == 'knowledge_base',
                ResourceGrant.resource_id == base_id,
            )
            .order_by(DeveloperApplication.id)
        )
        return [
            ResourceReference(
                'application', row.id, row.name, row.status, row.owner_id, f'/developer?applicationId={row.id}'
            )
            for row in rows.unique()
        ]

    async def lock_owned(
        self, session: AsyncSession, owner_id: UUID, application_id: UUID, expected_version: int | None = None
    ) -> DeveloperApplication:
        row = await self.applications.require(session, Principal(owner_id, '', False), application_id, lock=True)
        if row.status != 'active' or not row.platform_enabled:
            raise AppError('application_conflict', '应用已停用', 409)
        if expected_version is not None and row.version != expected_version:
            raise AppError('application_conflict', '应用状态或版本已改变', 409)
        return row

    async def bind_verified(
        self,
        session: AsyncSession,
        owner_id: UUID,
        application_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        actions: tuple[ResourceAction, ...],
        *,
        ownership: ResourceOwnership,
        expected_version: int | None = None,
        source: SourceFileFact | None = None,
    ) -> ResourceGrantInfo:
        """Application supplies domain-verified actions and metadata in this transaction."""
        validate_actions(kind, actions)
        app = await self.lock_owned(session, owner_id, application_id, expected_version)
        if source is not None:
            if kind != 'file' or resource_id != source.id or source.owner_id != ownership.owner_id:
                raise ValueError('Source enrollment must match the bound file')
            await self.enroll_verified(session, app, source)
        row = await session.get(ResourceGrant, (application_id, kind, resource_id), populate_existing=True)
        if row is None:
            row = ResourceGrant(application_id=application_id, kind=kind, resource_id=resource_id)
            session.add(row)
        row.actions = list(actions)
        row.resource_owner_id = ownership.owner_id
        row.ownership_stamp = ownership.stamp
        app.version += 1
        app.updated_at = utcnow()
        await session.flush()
        return ResourceGrantInfo(application_id, kind, resource_id, actions, app.version, ownership)

    async def unbind(
        self,
        session: AsyncSession,
        owner_id: UUID,
        application_id: UUID,
        kind: ResourceKind,
        resource_id: UUID,
        *,
        expected_version: int,
    ) -> int:
        app = await self.lock_owned(session, owner_id, application_id, expected_version)
        await session.execute(
            delete(ResourceGrant).where(
                ResourceGrant.application_id == application_id,
                ResourceGrant.kind == kind,
                ResourceGrant.resource_id == resource_id,
            )
        )
        app.version += 1
        app.updated_at = utcnow()
        return app.version

    async def require(
        self, session: AsyncSession, application_id: UUID, kind: ResourceKind, resource_id: UUID, action: ResourceAction
    ) -> ResourceOwnership:
        row = await session.get(ResourceGrant, (application_id, kind, resource_id), populate_existing=True)
        if row is None or action not in row.actions:
            raise AppError('resource_not_found', '资源不存在', 404)
        return ResourceOwnership(row.resource_owner_id, row.ownership_stamp)

    async def list(
        self, session: AsyncSession, owner_id: UUID, application_id: UUID, kind: ResourceKind | None = None
    ) -> list[ResourceGrantInfo]:
        app = await self.applications.require_owned(session, owner_id, application_id, lock=False)
        query = select(ResourceGrant).where(ResourceGrant.application_id == application_id)
        if kind is not None:
            query = query.where(ResourceGrant.kind == kind)
        rows = await session.scalars(
            query.order_by(ResourceGrant.kind, ResourceGrant.resource_id).execution_options(populate_existing=True)
        )
        return [
            ResourceGrantInfo(
                row.application_id,
                cast('ResourceKind', row.kind),
                row.resource_id,
                cast('tuple[ResourceAction, ...]', tuple(row.actions)),
                app.version,
                ResourceOwnership(row.resource_owner_id, row.ownership_stamp),
            )
            for row in rows
        ]

    async def source_usage(self, session: AsyncSession, application_id: UUID) -> tuple[int, int]:
        count, size = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(SourceEnrollment.size), 0)).where(
                    SourceEnrollment.application_id == application_id, SourceEnrollment.purged_at.is_(None)
                )
            )
        ).one()
        return int(count), int(size)

    async def enroll_verified(self, session: AsyncSession, app: DeveloperApplication, source: SourceFileFact) -> None:
        if source.status == 'purged':
            raise AppError('file_not_found', '文件不存在', 404)
        if source.source_file_id is not None:
            return
        existing = await session.get(SourceEnrollment, (app.id, source.id))
        if existing is not None:
            return
        count, size = await self.source_usage(session, app.id)
        if count + 1 > app.source_file_limit or size + source.size > app.source_byte_limit:
            raise AppError('application_storage_limit', '应用原始文件数量或容量已达上限', 409)
        if source.size < 0:
            raise ValueError('Invalid source size')
        session.add(
            SourceEnrollment(application_id=app.id, file_id=source.id, owner_id=source.owner_id, size=source.size)
        )
        await session.flush()

    async def enrolled_applications(self, session: AsyncSession, file_id: UUID) -> tuple[UUID, ...]:
        """Trusted accounting discovery, including grants that were subsequently unbound."""
        return tuple(
            await session.scalars(
                select(SourceEnrollment.application_id)
                .where(
                    SourceEnrollment.file_id == file_id,
                    SourceEnrollment.purged_at.is_(None),
                )
                .order_by(SourceEnrollment.application_id)
            )
        )

    async def lock_accounting(self, session: AsyncSession, application_id: UUID) -> None:
        # Trusted lifecycle path also applies after application disable/deletion.
        row = await session.scalar(
            select(DeveloperApplication)
            .where(DeveloperApplication.id == application_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AppError('application_not_found', '应用不存在', 404)

    async def release_verified(self, session: AsyncSession, application_id: UUID, source: SourceFileFact) -> None:
        if source.status != 'purged':
            raise AppError('file_not_purged', '文件尚未实际清理', 409)
        row = await session.get(SourceEnrollment, (application_id, source.id), populate_existing=True)
        if row is not None and row.purged_at is None:
            row.purged_at = utcnow()

    async def configure_quota(
        self,
        session: AsyncSession,
        principal: Principal,
        application_id: UUID,
        *,
        file_limit: int,
        byte_limit: int,
        expected_version: int,
    ) -> int:
        principal.require_admin()
        if type(file_limit) is not int or type(byte_limit) is not int or file_limit <= 0 or byte_limit <= 0:
            raise AppError('invalid_resource_quota', '容量上限必须为正整数', 422)
        app = await self.applications.require(session, principal, application_id, lock=True)
        if app.version != expected_version:
            raise AppError('application_conflict', '应用状态或版本已改变', 409)
        app.source_file_limit, app.source_byte_limit = file_limit, byte_limit
        app.version += 1
        app.updated_at = utcnow()
        return app.version

    async def configure_governance_quota(
        self,
        session: AsyncSession,
        principal: Principal,
        application_id: UUID,
        *,
        file_limit: int,
        byte_limit: int,
        expected_version: int,
    ) -> int:
        app = await self.applications.require_governance(session, principal, application_id, lock=True)
        if type(file_limit) is not int or type(byte_limit) is not int or file_limit <= 0 or byte_limit <= 0:
            raise AppError('invalid_resource_quota', '容量上限必须为正整数', 422)
        if app.version != expected_version:
            raise AppError('application_conflict', '应用状态或版本已改变', 409)
        app.source_file_limit, app.source_byte_limit = file_limit, byte_limit
        app.version += 1
        app.updated_at = utcnow()
        return app.version
