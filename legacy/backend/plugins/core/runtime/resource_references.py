import inspect

from sqlalchemy.ext.asyncio import AsyncSession

from plugins.core.discovery.registry import PluginRegistry
from plugins.core.management.dao.resource_ownership_dao import PluginResourceOwnershipDao
from plugins.core.management.service.resource_ownership import reference_snapshot
from plugins.core.runtime.callable import PluginCallableLoader


async def has_resource_references(
    registry: PluginRegistry | None,
    db: AsyncSession,
    resource_type: str,
    resource_id: str,
) -> bool:
    """查询调用方数据库中的持久归属，包括安全卸载和已清理平台元数据的插件。

    注册表只提供源码与回调。旧版不明确的安装历史、缺失源码/声明/回调以及查询错误均抛出，
    由调用方拒绝删除。端口不回填状态、不刷新调用方待写数据，不显式开启或结束事务，遵从调用方事务。
    """
    if registry is None:
        raise RuntimeError('插件注册表尚未就绪，无法检查持久资源引用')

    with db.no_autoflush:
        evidence_rows = await PluginResourceOwnershipDao.get_reference_evidence(db)
        has_references = False
        for evidence in evidence_rows:
            plugin_id = evidence.plugin_id
            plugin = registry.get_plugin(plugin_id)
            installed = bool(evidence.installed_version)
            state = evidence.ownership_state
            if state is None:
                state = 'retained' if installed else 'unknown'
            if state == 'none' and (installed or evidence.has_migration_history):
                state = 'retained' if installed else 'unknown'
            if state == 'none':
                continue
            if state != 'retained':
                raise RuntimeError(f'插件 {plugin_id} 持久资源归属未知，无法确认是否仍有引用')
            if plugin is None:
                raise RuntimeError(f'插件 {plugin_id} 缺少源码，无法检查持久资源引用')
            declared = plugin.discovered_plugin.manifest.backend.resource_references
            references = (
                reference_snapshot(evidence.resource_references) if evidence.ownership_state is not None else {}
            )
            if resource_type not in references and resource_type not in declared:
                continue
            if resource_type not in declared:
                raise RuntimeError(f'插件 {plugin_id} 缺少持久资源引用声明：{resource_type}')
            paths = set(references.get(resource_type, [])) | {declared[resource_type]}
            for callable_path in sorted(paths):
                callback = (
                    PluginCallableLoader(plugin.discovered_plugin, label='资源引用查询')
                    .load(callable_path)
                    .callable_object
                )
                if not inspect.iscoroutinefunction(callback):
                    raise TypeError(f'插件 {plugin_id} 资源引用查询必须使用 async def 声明')
                result = await callback(db, resource_id)
                if not isinstance(result, bool):
                    raise TypeError(f'插件 {plugin_id} 资源引用查询必须返回 bool')
                has_references = has_references or result

        return has_references
