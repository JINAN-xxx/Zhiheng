from sqlalchemy.ext.asyncio import AsyncSession

from plugins.core.discovery.scanner import DiscoveredPlugin
from plugins.core.management.dao.resource_ownership_dao import PluginResourceOwnershipDao
from plugins.core.management.entity.do.models import SysPlugin, SysPluginResourceOwnership


def reference_snapshot(value: object) -> dict[str, list[str]]:
    """验证持久回调快照，损坏的记录不能被当作无引用。"""
    if not isinstance(value, dict):
        raise RuntimeError('插件持久资源引用快照无效')
    result = {}
    for resource_type, paths in value.items():
        if (
            not isinstance(resource_type, str)
            or not resource_type
            or not isinstance(paths, list)
            or not paths
            or any(not isinstance(path, str) or not path for path in paths)
        ):
            raise RuntimeError('插件持久资源引用快照无效')
        result[resource_type] = list(paths)
    return result


class PluginResourceOwnershipService:
    """仅在生命周期写事务中记录归属；资源查询不回填、不提交。"""

    @staticmethod
    async def preserve(
        db: AsyncSession,
        plugin_id: str,
        plugin: SysPlugin | None,
        discovered: DiscoveredPlugin | None = None,
    ) -> SysPluginResourceOwnership:
        ownership = await PluginResourceOwnershipDao.get(db, plugin_id)
        if ownership is not None:
            if ownership.state == 'none':
                # 安装版本也是持久证据；无 migration 的插件不能在清除该字段后重新变成“无数据”。
                if plugin is not None and plugin.installed_version:
                    if discovered is None:
                        ownership.state = 'unknown'
                    else:
                        references = reference_snapshot(ownership.resource_references)
                        for kind, path in discovered.manifest.backend.resource_references.items():
                            references[kind] = sorted(set(references.get(kind, [])) | {path})
                        ownership.resource_references = references
                        ownership.state = 'retained'
                elif await PluginResourceOwnershipDao.has_migration_history(db, plugin_id):
                    ownership.state = 'unknown'
            return ownership

        if plugin is not None and plugin.installed_version and discovered is not None:
            state = 'retained'
        elif plugin is not None or await PluginResourceOwnershipDao.has_migration_history(db, plugin_id):
            # 旧版 discovered 可能是安全卸载；migration 成功也可能只是部分安装。
            state = 'unknown'
        else:
            state = 'none'
        ownership = SysPluginResourceOwnership(
            plugin_id=plugin_id,
            state=state,
            resource_references={kind: [path] for kind, path in discovered.manifest.backend.resource_references.items()}
            if discovered is not None
            else {},
        )
        db.add(ownership)
        await db.flush()
        return ownership

    @classmethod
    async def mark_installed(
        cls,
        db: AsyncSession,
        plugin: SysPlugin | None,
        discovered: DiscoveredPlugin,
    ) -> None:
        ownership = await cls.preserve(db, discovered.manifest.id, plugin, discovered)
        references = reference_snapshot(ownership.resource_references)
        for kind, path in discovered.manifest.backend.resource_references.items():
            references[kind] = sorted(set(references.get(kind, [])) | {path})
        ownership.resource_references = references
        ownership.state = 'retained'
        await db.flush()
