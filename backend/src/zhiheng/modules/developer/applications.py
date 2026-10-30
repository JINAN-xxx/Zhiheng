from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.contracts import ApplicationCreate, ApplicationInfo, ApplicationPatch
from zhiheng.modules.developer.orm import DeveloperApplication
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
        if not principal.is_admin:
            query = query.where(DeveloperApplication.owner_id == principal.id)
        if lock:
            query = query.with_for_update()
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
        if row.status != 'active':
            raise AppError('application_conflict', '应用已停用', 409)
        return self.info(row)

    async def create(self, session: AsyncSession, principal: Principal, body: ApplicationCreate) -> ApplicationInfo:
        row = DeveloperApplication(code=body.code, name=body.name, owner_id=principal.id)
        session.add(row)
        try:
            await session.flush()
        except IntegrityError:
            raise AppError('application_code_conflict', '应用编码已存在', 409) from None
        return self.info(row)

    async def list(
        self, session: AsyncSession, principal: Principal, page: int, page_size: int, search: str | None
    ) -> dict[str, object]:
        query = select(DeveloperApplication).where(DeveloperApplication.deleted_at.is_(None))
        if not principal.is_admin:
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

    @staticmethod
    def info(row: DeveloperApplication) -> ApplicationInfo:
        return ApplicationInfo.model_validate(row, from_attributes=True)
