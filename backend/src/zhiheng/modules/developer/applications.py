from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.contracts import (
    ApplicationCreate,
    ApplicationGovernanceInfo,
    ApplicationGovernancePage,
    ApplicationInfo,
    ApplicationPatch,
)
from zhiheng.modules.developer.orm import DeveloperApplication, SourceEnrollment
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


class ApplicationService:
    async def require(
        self, session: AsyncSession, principal: Principal, application_id: UUID, *, lock: bool = False
    ) -> DeveloperApplication:
        query = select(DeveloperApplication).where(
            DeveloperApplication.id == application_id, DeveloperApplication.deleted_at.is_(None)
        )
        query = query.where(DeveloperApplication.owner_id == principal.id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await session.scalar(query)
        if row is None:
            raise AppError('application_not_found', '应用不存在', 404)
        return row

    async def require_owned(
        self, session: AsyncSession, owner_id: UUID, application_id: UUID, *, lock: bool = True
    ) -> ApplicationInfo:
        row = await self.require(session, Principal(owner_id, '', False), application_id, lock=lock)
        return self.info(row)

    async def require_active_owned(
        self, session: AsyncSession, owner_id: UUID, application_id: UUID, *, lock: bool = True
    ) -> ApplicationInfo:
        row = await self.require(session, Principal(owner_id, '', False), application_id, lock=lock)
        if row.status != 'active' or not row.platform_enabled:
            raise AppError('application_conflict', '应用已停用', 409)
        return self.info(row)

    async def create(self, session: AsyncSession, principal: Principal, body: ApplicationCreate) -> ApplicationInfo:
        for _ in range(3):
            identifier = uuid4()
            row = DeveloperApplication(
                id=identifier, code=body.code or f'app_{identifier.hex}', name=body.name, owner_id=principal.id
            )
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError as error:
                if body.code is not None:
                    raise AppError('application_code_conflict', '应用编码已存在', 409) from None
                if getattr(error.orig, 'sqlstate', None) != '23505':
                    raise
            else:
                return self.info(row)
        raise AppError('application_identifier_unavailable', '暂时无法分配应用标识，请稍后重试', 503)

    async def list(
        self, session: AsyncSession, principal: Principal, page: int, page_size: int, search: str | None
    ) -> dict[str, object]:
        query = select(DeveloperApplication).where(DeveloperApplication.deleted_at.is_(None))
        query = query.where(DeveloperApplication.owner_id == principal.id)
        if search:
            query = query.where(DeveloperApplication.name.contains(search, autoescape=True))
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(DeveloperApplication.created_at.desc(), DeveloperApplication.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {'items': [self.info(row) for row in rows], 'total': total or 0, 'page': page, 'page_size': page_size}

    async def update(
        self, session: AsyncSession, principal: Principal, application_id: UUID, body: ApplicationPatch
    ) -> ApplicationInfo:
        row = await self.require(session, principal, application_id, lock=True)
        if row.version != body.expected_version:
            raise AppError('application_conflict', '应用状态或版本已改变', 409)
        row.name = body.name
        row.version += 1
        row.updated_at = utcnow()
        return self.info(row)

    async def transition(
        self, session: AsyncSession, principal: Principal, application_id: UUID, action: str
    ) -> ApplicationInfo:
        row = await self.require(session, principal, application_id, lock=True)
        if action == 'enable' and not row.platform_enabled:
            raise AppError('application_platform_disabled', '应用已被平台停用，请联系管理员', 409)
        target = 'active' if action == 'enable' else 'disabled'
        if action == 'delete':
            row.status, row.deleted_at = 'disabled', utcnow()
        elif row.status == target:
            raise AppError('application_conflict', '应用已经处于目标状态', 409)
        else:
            row.status = target
        row.version += 1
        row.updated_at = utcnow()
        return self.info(row)

    async def require_governance(
        self, session: AsyncSession, principal: Principal, application_id: UUID, *, lock: bool = False
    ) -> DeveloperApplication:
        principal.require_admin()
        query = select(DeveloperApplication).where(
            DeveloperApplication.id == application_id, DeveloperApplication.deleted_at.is_(None)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await session.scalar(query)
        if row is None:
            raise AppError('application_not_found', '应用不存在', 404)
        return row

    async def governance_info(self, session: AsyncSession, row: DeveloperApplication) -> ApplicationGovernanceInfo:
        count, size = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(SourceEnrollment.size), 0)).where(
                    SourceEnrollment.application_id == row.id, SourceEnrollment.purged_at.is_(None)
                )
            )
        ).one()
        return ApplicationGovernanceInfo(
            id=row.id,
            name=row.name,
            owner_id=row.owner_id,
            owner_enabled=row.status == 'active',
            platform_enabled=row.platform_enabled,
            version=row.version,
            source_file_limit=row.source_file_limit,
            source_byte_limit=row.source_byte_limit,
            source_count=count,
            source_bytes=size,
        )

    async def governance_list(
        self, session: AsyncSession, principal: Principal, *, owner_id: UUID | None, page: int, page_size: int
    ) -> ApplicationGovernancePage:
        principal.require_admin()
        query = select(DeveloperApplication).where(DeveloperApplication.deleted_at.is_(None))
        if owner_id is not None:
            query = query.where(DeveloperApplication.owner_id == owner_id)
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(DeveloperApplication.created_at.desc(), DeveloperApplication.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return ApplicationGovernancePage(
            items=[await self.governance_info(session, row) for row in rows],
            total=total or 0,
            page=page,
            page_size=page_size,
        )

    async def governance_state(
        self, session: AsyncSession, principal: Principal, application_id: UUID, *, enabled: bool, expected_version: int
    ) -> ApplicationGovernanceInfo:
        row = await self.require_governance(session, principal, application_id, lock=True)
        if row.version != expected_version:
            raise AppError('application_conflict', '应用状态或版本已改变', 409)
        row.platform_enabled = enabled
        row.version += 1
        row.updated_at = utcnow()
        await session.flush()
        return await self.governance_info(session, row)

    @staticmethod
    def info(row: DeveloperApplication) -> ApplicationInfo:
        info = ApplicationInfo.model_validate(row, from_attributes=True)
        return info if row.platform_enabled else info.model_copy(update={'status': 'disabled'})
