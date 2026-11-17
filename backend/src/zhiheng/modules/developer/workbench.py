"""Only the current owner's API Key expiry metadata is exposed to the workbench."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey
from zhiheng.modules.overview.public import WorkbenchAlert
from zhiheng.shared.principal import Principal


class WorkbenchDeveloper:
    async def alerts(self, session: AsyncSession, actor: Principal, now: datetime) -> list[WorkbenchAlert]:
        if not actor.has_any_permission('platform:developer:read'):
            return []
        rows = await session.execute(
            select(
                DeveloperKey.id,
                DeveloperKey.name,
                DeveloperKey.expires_at,
                DeveloperApplication.id,
                DeveloperApplication.name,
            )
            .join(DeveloperApplication, DeveloperApplication.id == DeveloperKey.application_id)
            .where(
                DeveloperApplication.owner_id == actor.id,
                DeveloperApplication.deleted_at.is_(None),
                DeveloperKey.deleted_at.is_(None),
                DeveloperKey.status.in_(('active', 'retiring')),
                DeveloperKey.expires_at > now,
                DeveloperKey.expires_at <= now + timedelta(days=7),
            )
            .order_by(DeveloperKey.expires_at)
            .limit(6)
        )
        return [
            WorkbenchAlert(
                id='key-' + str(key_id),
                level='warning',
                title=key_name + ' · API Key 即将到期',
                description=app_name + '，请在开发接入中处理。',
                kind='application',
                resource_id=app_id,
                occurred_at=expires,
            )
            for key_id, key_name, expires, app_id, app_name in rows
        ]
