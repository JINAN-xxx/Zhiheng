from dataclasses import dataclass

from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.core.management.entity.do.models import SysPlugin, SysPluginMigration, SysPluginResourceOwnership


@dataclass(frozen=True, slots=True)
class PluginReferenceEvidence:
    """同一 SQL 快照中的列值，不复用或刷新调用方的 ORM 实例。"""

    plugin_id: str
    ownership_state: str | None
    resource_references: object
    installed_version: str | None
    has_migration_history: bool


class PluginResourceOwnershipDao:
    """归属记录与旧版安装证据查询；事务由调用方管理。"""

    @staticmethod
    async def get(db: AsyncSession, plugin_id: str) -> SysPluginResourceOwnership | None:
        return (
            await db.execute(
                select(SysPluginResourceOwnership)
                .where(SysPluginResourceOwnership.plugin_id == plugin_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    @staticmethod
    async def has_migration_history(db: AsyncSession, plugin_id: str) -> bool:
        return (
            await db.execute(
                select(SysPluginMigration.plugin_id).where(SysPluginMigration.plugin_id == plugin_id).limit(1)
            )
        ).first() is not None

    @staticmethod
    async def get_reference_evidence(
        db: AsyncSession,
    ) -> list[PluginReferenceEvidence]:
        # 必须是一条语句：READ COMMITTED 下分次读可能把 purge 前的归属与 purge 后的元数据混合。
        candidate_ids = union(
            select(SysPluginResourceOwnership.plugin_id),
            select(SysPlugin.plugin_id),
            select(SysPluginMigration.plugin_id),
        ).subquery()
        migration_exists = (
            select(SysPluginMigration.plugin_id)
            .where(SysPluginMigration.plugin_id == candidate_ids.c.plugin_id)
            .exists()
        )
        query = (
            select(
                candidate_ids.c.plugin_id,
                SysPluginResourceOwnership.state,
                SysPluginResourceOwnership.resource_references,
                SysPlugin.installed_version,
                migration_exists,
            )
            .select_from(candidate_ids)
            .outerjoin(SysPluginResourceOwnership, SysPluginResourceOwnership.plugin_id == candidate_ids.c.plugin_id)
            .outerjoin(SysPlugin, SysPlugin.plugin_id == candidate_ids.c.plugin_id)
            .order_by(candidate_ids.c.plugin_id)
        )
        return [PluginReferenceEvidence(*row) for row in await db.execute(query)]
