from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from plugins.core.discovery.registry import PluginRegistry
from plugins.core.discovery.scanner import DiscoveredPlugin
from plugins.core.management.entity.do.models import SysPlugin, SysPluginMigration, SysPluginResourceOwnership
from plugins.core.management.entity.vo.schemas import PluginModel
from plugins.core.manifest.schema import PluginManifest
from plugins.core.runtime.resource_references import has_resource_references


def build_plugin(
    tmp_path: Path,
    plugin_id: str = 'reference_demo',
    *,
    body: str = 'return True',
    resource_type: str = 'ai.model',
    async_callback: bool = True,
) -> DiscoveredPlugin:
    backend_path = tmp_path / 'plugins' / plugin_id
    backend_path.mkdir(parents=True)
    prefix = 'async ' if async_callback else ''
    (backend_path / 'references.py').write_text(
        f'{prefix}def has_references(db, resource_id):\n'
        f'    db.calls.append(({plugin_id!r}, resource_id))\n'
        f'    {body}\n',
        encoding='utf-8',
    )
    manifest = PluginManifest.model_validate(
        {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'backend': {
                'module': f'plugins.{plugin_id}',
                'resourceReferences': {resource_type: f'plugins.{plugin_id}.references:has_references'},
            },
        }
    )
    return DiscoveredPlugin(manifest=manifest, backend_path=backend_path, manifest_path=backend_path / 'plugin.yaml')


def database_plugin(
    plugin_id: str = 'reference_demo',
    *,
    enabled: str = '0',
    status: str = 'installed',
    installed_version: str | None = '1.0.0',
) -> PluginModel:
    return PluginModel(
        pluginId=plugin_id,
        pluginName=plugin_id,
        version='1.0.0',
        installedVersion=installed_version,
        enabled=enabled,
        status=status,
    )


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for model in (SysPlugin, SysPluginMigration, SysPluginResourceOwnership):
            await connection.run_sync(model.__table__.create)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.calls = []
            session.commit = AsyncMock(wraps=session.commit)
            session.rollback = AsyncMock(wraps=session.rollback)
            yield session
    finally:
        await engine.dispose()


async def query_references(registry: PluginRegistry | None, db: AsyncSession) -> bool:
    if registry is not None:
        for plugin in registry.list_plugins():
            record = plugin.database_plugin
            if record is None:
                continue
            db.add(
                SysPlugin(
                    plugin_id=record.plugin_id,
                    plugin_name=record.plugin_id,
                    version='1.0.0',
                    installed_version=record.installed_version,
                    enabled=record.enabled,
                    status=record.status,
                )
            )
            if record.installed_version is None:
                db.add(SysPluginResourceOwnership(plugin_id=record.plugin_id, state='none', resource_references={}))
        for plugin_id in registry.unresolved_installed_plugin_ids:
            db.add(SysPlugin(plugin_id=plugin_id, plugin_name=plugin_id, version='1', installed_version='1'))
        await db.flush()
    return await has_resource_references(registry, db, 'ai.model', 'mdl_reference_test')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('enabled', 'status'), [('0', 'installed'), ('1', 'installed'), ('0', 'error'), ('1', 'error')]
)
async def test_queries_installed_plugins_regardless_of_runtime_state(
    tmp_path: Path, enabled: str, status: str, db: AsyncSession
) -> None:
    plugin = build_plugin(tmp_path)
    registry = PluginRegistry.build([plugin], [database_plugin(enabled=enabled, status=status)])

    assert await query_references(registry, db) is True

    assert db.calls == [('reference_demo', 'mdl_reference_test')]
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('has_database_record', [False, True])
async def test_skips_known_empty_plugins_without_loading_their_callables(
    tmp_path: Path, has_database_record: bool, db: AsyncSession
) -> None:
    plugin = build_plugin(tmp_path)
    (plugin.backend_path / 'references.py').write_text('raise RuntimeError("must not load")\n', encoding='utf-8')
    records = [database_plugin(installed_version=None)] if has_database_record else []

    assert await query_references(PluginRegistry.build([plugin], records), db) is False
    assert db.calls == []


@pytest.mark.asyncio
async def test_skips_plugins_without_requested_resource_type(tmp_path: Path, db: AsyncSession) -> None:
    plugin = build_plugin(tmp_path, resource_type='other.resource')
    (plugin.backend_path / 'references.py').write_text('raise RuntimeError("must not load")\n', encoding='utf-8')

    assert await query_references(PluginRegistry.build([plugin], [database_plugin()]), db) is False
    assert db.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('last_result', [False, True])
async def test_queries_all_installed_plugins_and_combines_results(
    tmp_path: Path, last_result: bool, db: AsyncSession
) -> None:
    plugins = [
        build_plugin(tmp_path, 'reference_first', body='return False'),
        build_plugin(tmp_path, 'reference_last', body=f'return {last_result}'),
    ]
    registry = PluginRegistry.build(plugins, [database_plugin(plugin.manifest.id) for plugin in plugins])

    assert await query_references(registry, db) is last_result
    assert db.calls == [('reference_first', 'mdl_reference_test'), ('reference_last', 'mdl_reference_test')]


@pytest.mark.asyncio
async def test_rejects_missing_registry(db: AsyncSession) -> None:
    with pytest.raises(RuntimeError, match='注册表'):
        await query_references(None, db)


@pytest.mark.asyncio
@pytest.mark.parametrize(('enabled', 'status'), [('0', 'installed'), ('1', 'installed'), ('0', 'error')])
async def test_rejects_installed_plugins_without_source(enabled: str, status: str, db: AsyncSession) -> None:
    registry = PluginRegistry.build([], [database_plugin('missing_source', enabled=enabled, status=status)])

    with pytest.raises(RuntimeError, match='missing_source'):
        await query_references(registry, db)


@pytest.mark.asyncio
@pytest.mark.parametrize('result', ['None', '1', '0', '"false"', '{}'])
async def test_rejects_non_boolean_callback_results(tmp_path: Path, result: str, db: AsyncSession) -> None:
    plugin = build_plugin(tmp_path, body=f'return {result}')

    with pytest.raises(TypeError, match='bool'):
        await query_references(PluginRegistry.build([plugin], [database_plugin()]), db)

    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_propagates_callback_failure_without_ending_caller_transaction(tmp_path: Path, db: AsyncSession) -> None:
    plugins = [
        build_plugin(tmp_path, 'reference_first'),
        build_plugin(tmp_path, 'reference_broken', body='raise RuntimeError("database unavailable")'),
    ]
    registry = PluginRegistry.build(plugins, [database_plugin(plugin.manifest.id) for plugin in plugins])

    with pytest.raises(RuntimeError, match='database unavailable'):
        await query_references(registry, db)

    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_propagates_callback_loading_failure(tmp_path: Path, db: AsyncSession) -> None:
    plugin = build_plugin(tmp_path)
    (plugin.backend_path / 'references.py').write_text(
        'raise ImportError("dependency unavailable")\n', encoding='utf-8'
    )

    with pytest.raises(ImportError, match='dependency unavailable'):
        await query_references(PluginRegistry.build([plugin], [database_plugin()]), db)


@pytest.mark.asyncio
async def test_rejects_sync_callbacks_before_execution(tmp_path: Path, db: AsyncSession) -> None:
    plugin = build_plugin(tmp_path, async_callback=False)

    with pytest.raises(TypeError, match='async def'):
        await query_references(PluginRegistry.build([plugin], [database_plugin()]), db)

    assert db.calls == []


@pytest.mark.asyncio
async def test_loader_rejects_foreign_modules_even_if_manifest_is_mutated(tmp_path: Path, db: AsyncSession) -> None:
    plugin = build_plugin(tmp_path)
    plugin.manifest.backend.resource_references['ai.model'] = 'plugins.other.references:has_references'

    with pytest.raises(RuntimeError, match='当前插件模块'):
        await query_references(PluginRegistry.build([plugin], [database_plugin()]), db)
