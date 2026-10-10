import inspect

from sqlalchemy.ext.asyncio import AsyncSession

from plugins.core.discovery.registry import PluginRegistry
from plugins.core.runtime.callable import PluginCallableLoader


async def has_resource_references(
    registry: PluginRegistry | None,
    db: AsyncSession,
    resource_type: str,
    resource_id: str,
) -> bool:
    """查询所有已安装插件的持久引用，包括禁用和运行异常的插件。

    callback 必须声明为 async def(db, resource_id) -> bool，使用调用方的数据库会话只读查询。
    本端口不提交或回滚事务；注册表不完整、加载失败、查询异常和非法返回值均向调用方抛出，
    调用方必须据此拒绝删除，不能将检查失败当作无引用。
    """
    if registry is None:
        raise RuntimeError('插件注册表尚未就绪，无法检查持久资源引用')
    if registry.unresolved_installed_plugin_ids:
        plugin_ids = '、'.join(registry.unresolved_installed_plugin_ids)
        raise RuntimeError(f'已安装插件缺少源码，无法检查持久资源引用：{plugin_ids}')

    has_references = False
    for plugin in registry.list_plugins():
        if plugin.database_plugin is None or not plugin.database_plugin.installed_version:
            continue
        callable_path = plugin.discovered_plugin.manifest.backend.resource_references.get(resource_type)
        if callable_path is None:
            continue
        callback = (
            PluginCallableLoader(plugin.discovered_plugin, label='资源引用查询').load(callable_path).callable_object
        )
        if not inspect.iscoroutinefunction(callback):
            raise TypeError(f'插件 {plugin.plugin_id} 资源引用查询必须使用 async def 声明')
        result = await callback(db, resource_id)
        if not isinstance(result, bool):
            raise TypeError(f'插件 {plugin.plugin_id} 资源引用查询必须返回 bool')
        has_references = has_references or result

    return has_references
