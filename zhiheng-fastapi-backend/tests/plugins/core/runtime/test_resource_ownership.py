from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.database import Base
from module_admin.entity.do.menu_do import SysMenu
from module_admin.entity.do.role_do import SysRoleMenu
from plugins.core.discovery.registry import PluginRegistry
from plugins.core.management.entity.do import models
from plugins.core.management.entity.do.models import (
    SysPlugin,
    SysPluginConfig,
    SysPluginMenu,
    SysPluginMigration,
    SysPluginResourceOwnership,
)
from plugins.core.management.service.service import PluginService
from plugins.core.runtime.resource_references import has_resource_references
from tests.plugins.core.management.test_plugin_state import create_sqlite_sys_job_table
from tests.plugins.core.runtime.test_resource_references import build_plugin

PLUGIN_ID = 'reference_demo'


@pytest_asyncio.fixture
async def ownership_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        tables = [
            SysPlugin,
            SysPluginConfig,
            SysPluginMenu,
            SysPluginMigration,
            SysPluginResourceOwnership,
            SysMenu,
            SysRoleMenu,
        ]
        await connection.run_sync(Base.metadata.create_all, tables=[model.__table__ for model in tables])
        await create_sqlite_sys_job_table(connection)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def legacy_record(*, installed: bool = False) -> SysPlugin:
    return SysPlugin(
        plugin_id=PLUGIN_ID,
        plugin_name=PLUGIN_ID,
        version='1.0.0',
        installed_version='1.0.0' if installed else None,
        status='installed' if installed else 'discovered',
    )


async def ownership(db: AsyncSession) -> SysPluginResourceOwnership | None:
    return await db.get(SysPluginResourceOwnership, PLUGIN_ID)


@pytest.mark.asyncio
async def test_fresh_discovery_is_known_empty_without_loading_callback(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path, body='raise AssertionError("must not execute")')
    async with ownership_db() as db:
        await PluginService.upsert_discovered_plugin_services(db, plugin, plugin.backend_path.parent)
        await db.commit()
        assert (await ownership(db)).state == 'none'
        assert not await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')


@pytest.mark.asyncio
@pytest.mark.parametrize('history', ['legacy', 'partial', 'migration_only'])
async def test_ambiguous_history_blocks_without_probing_business_tables(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    history: str,
) -> None:
    plugin = build_plugin(tmp_path, body='raise AssertionError("business tables need not exist")')
    async with ownership_db() as db:
        if history != 'migration_only':
            db.add(legacy_record())
        if history != 'legacy':
            db.add(
                SysPluginMigration(
                    plugin_id=PLUGIN_ID, migration_path='001.sql', migration_checksum='a', status='failed'
                )
            )
        await db.commit()
        registry = PluginRegistry.build([plugin])
        with pytest.raises(RuntimeError, match=r'未知|无法确认'):
            await has_resource_references(registry, db, 'ai.model', 'm')
        await PluginService.upsert_discovered_plugin_services(db, plugin, plugin.backend_path.parent)
        await db.commit()
        assert (await ownership(db)).state == 'unknown'
        with pytest.raises(RuntimeError, match=r'未知|无法确认'):
            await has_resource_references(registry, db, 'ai.model', 'm')


@pytest.mark.asyncio
async def test_complete_install_without_migrations_recovers_unknown(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path)
    async with ownership_db() as db:
        db.add(legacy_record())
        await db.commit()
        await PluginService.upsert_discovered_plugin_services(db, plugin, plugin.backend_path.parent)
        assert (await ownership(db)).state == 'unknown'
        await PluginService.mark_plugin_installed_services(db, plugin)
        await db.commit()
        assert (await ownership(db)).state == 'retained'
        assert (await db.execute(select(SysPluginMigration))).scalars().all() == []
        db.calls = []
        assert await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')
        assert db.calls == [(PLUGIN_ID, 'm')]


@pytest.mark.asyncio
async def test_none_with_partial_migration_becomes_unknown_without_callback(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path, body='raise AssertionError("must not execute")')
    async with ownership_db() as db:
        await PluginService.upsert_discovered_plugin_services(db, plugin, plugin.backend_path.parent)
        db.add(
            SysPluginMigration(plugin_id=PLUGIN_ID, migration_path='001.sql', migration_checksum='a', status='failed')
        )
        await db.commit()
        with pytest.raises(RuntimeError, match=r'未知|无法确认'):
            await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['missing', 'no_declaration', 'missing_callable'])
async def test_retained_ownership_without_metadata_fails_closed_when_callback_unavailable(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    source: str,
) -> None:
    plugin = build_plugin(tmp_path)
    async with ownership_db() as db:
        await PluginService.upsert_discovered_plugin_services(db, plugin, plugin.backend_path.parent)
        await PluginService.mark_plugin_installed_services(db, plugin)
        await PluginService.purge_plugin_metadata_by_id_services(db, PLUGIN_ID)
        await db.commit()
        assert await db.get(SysPlugin, PLUGIN_ID) is None
        assert (await ownership(db)).state == 'retained'
    if source == 'no_declaration':
        plugin.manifest.backend.resource_references.clear()
    elif source == 'missing_callable':
        (plugin.backend_path / 'references.py').unlink()
    async with ownership_db() as db:
        with pytest.raises((RuntimeError, ImportError, FileNotFoundError, AttributeError)):
            await has_resource_references(
                PluginRegistry.build([] if source == 'missing' else [plugin]),
                db,
                'ai.model',
                'm',
            )


@pytest.mark.asyncio
async def test_reinstall_preserves_old_resource_type_and_callback_obligations(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path)
    async with ownership_db() as db:
        await PluginService.upsert_discovered_plugin_services(db, plugin, plugin.backend_path.parent)
        await PluginService.mark_plugin_installed_services(db, plugin)
        await db.commit()
        original_path = plugin.manifest.backend.resource_references.pop('ai.model')
        plugin.manifest.backend.resource_references['other.resource'] = original_path
        await PluginService.mark_plugin_installed_services(db, plugin)
        await db.commit()
        with pytest.raises(RuntimeError, match='声明'):
            await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')
        plugin.manifest.backend.resource_references['ai.model'] = 'plugins.reference_demo.references:replacement'
        with (plugin.backend_path / 'references.py').open('a') as file:
            file.write('\nasync def replacement(db, resource_id):\n    return False\n')
        await PluginService.mark_plugin_installed_services(db, plugin)
        await db.commit()
        db.calls = []
        assert await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')
        assert db.calls == [(PLUGIN_ID, 'm')]


@pytest.mark.asyncio
async def test_uninstall_failure_rolls_back_ownership_and_plugin_state_together(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = build_plugin(tmp_path)
    async with ownership_db() as db:
        db.add(legacy_record(installed=True))
        await db.commit()
        monkeypatch.setattr(
            'plugins.core.management.service.service.PluginJobInstaller.pause_plugin_jobs',
            AsyncMock(side_effect=RuntimeError('job failure')),
        )
        with pytest.raises(RuntimeError, match='job failure'):
            await PluginService.mark_plugin_uninstalled_services(db, PLUGIN_ID, plugin)
        await db.rollback()
    async with ownership_db() as db:
        assert (await db.get(SysPlugin, PLUGIN_ID)).installed_version == '1.0.0'
        assert await ownership(db) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('installed', [True, False])
async def test_legacy_metadata_purge_materializes_unknown_before_erasing_evidence(
    ownership_db: async_sessionmaker[AsyncSession],
    installed: bool,
) -> None:
    async with ownership_db() as db:
        db.add(legacy_record(installed=installed))
        await db.commit()
        await PluginService.purge_plugin_metadata_by_id_services(db, PLUGIN_ID)
        await db.commit()
    async with ownership_db() as db:
        assert await db.get(SysPlugin, PLUGIN_ID) is None
        assert (await ownership(db)).state == 'unknown'
        with pytest.raises(RuntimeError, match=r'未知|无法确认'):
            await has_resource_references(PluginRegistry.build([]), db, 'ai.model', 'm')


@pytest.mark.asyncio
async def test_rediscovery_does_not_reset_retained_ownership_after_metadata_purge(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path)
    async with ownership_db() as db:
        await PluginService.upsert_discovered_plugin_services(db, plugin, plugin.backend_path.parent)
        await PluginService.mark_plugin_installed_services(db, plugin)
        await PluginService.purge_plugin_metadata_by_id_services(db, PLUGIN_ID)
        await PluginService.upsert_discovered_plugin_services(db, plugin, plugin.backend_path.parent)
        await db.commit()
        assert (await ownership(db)).state == 'retained'
        db.calls = []
        assert await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')


@pytest.mark.asyncio
async def test_reference_check_preserves_caller_pending_writes_and_transaction(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path)
    async with ownership_db() as db:
        db.add(legacy_record(installed=True))
        await db.commit()
        record = await db.get(SysPlugin, PLUGIN_ID)
        record.description = 'caller pending change'
        transaction = db.sync_session.get_transaction()
        db.commit = AsyncMock(wraps=db.commit)
        db.rollback = AsyncMock(wraps=db.rollback)
        db.calls = []
        assert await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')
        assert record in db.dirty
        assert db.sync_session.get_transaction() is transaction
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()
        assert await ownership(db) is None
        await db.rollback()
    async with ownership_db() as db:
        assert (await db.get(SysPlugin, PLUGIN_ID)).description is None


@pytest.mark.asyncio
async def test_create_all_adds_ownership_table_without_losing_legacy_rows() -> None:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SysPlugin.__table__.create)
            await connection.execute(
                SysPlugin.__table__.insert().values(plugin_id=PLUGIN_ID, plugin_name='Legacy', version='1')
            )
            ownership_model = getattr(models, 'SysPluginResourceOwnership', None)
            assert ownership_model is not None, 'Startup create_all must discover ownership schema'
            tables = [SysPlugin.__table__, ownership_model.__table__]
            await connection.run_sync(Base.metadata.create_all, tables=tables)
            await connection.run_sync(Base.metadata.create_all, tables=tables)
            assert 'sys_plugin_resource_ownership' in await connection.run_sync(
                lambda sync: inspect(sync).get_table_names()
            )
            assert (await connection.execute(select(SysPlugin.plugin_id))).scalar_one() == PLUGIN_ID
            assert not ownership_model.__table__.foreign_keys
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_installed_uninstall_captures_declaration_before_clearing_version(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path)
    async with ownership_db() as db:
        db.add(legacy_record(installed=True))
        await db.commit()
        await PluginService.mark_plugin_uninstalled_services(db, PLUGIN_ID, plugin)
        await db.commit()
    async with ownership_db() as db:
        assert (await db.get(SysPlugin, PLUGIN_ID)).installed_version is None
        assert (await ownership(db)).state == 'retained'
        db.calls = []
        assert await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')


@pytest.mark.asyncio
async def test_declared_but_unexecuted_purge_hook_does_not_clear_obligation(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path)
    plugin.manifest.backend.hooks.on_purge = 'plugins.reference_demo.references:purge'
    async with ownership_db() as db:
        await PluginService.upsert_discovered_plugin_services(db, plugin, plugin.backend_path.parent)
        await PluginService.mark_plugin_installed_services(db, plugin)
        await PluginService.purge_plugin_services(db, plugin)
        await db.commit()
    async with ownership_db() as db:
        assert (await ownership(db)).state == 'retained'
        assert await db.get(SysPlugin, PLUGIN_ID) is None
        db.calls = []
        assert await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')


@pytest.mark.asyncio
async def test_corrupt_ownership_snapshot_blocks_deletion_without_callback(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path, body='raise AssertionError("must not execute")')
    async with ownership_db() as db:
        db.add(SysPluginResourceOwnership(plugin_id=PLUGIN_ID, state='retained', resource_references=[]))
        await db.commit()
        with pytest.raises(RuntimeError, match='快照无效'):
            await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation', ['uninstall_with_source', 'uninstall_without_source', 'purge_with_source', 'metadata_purge']
)
async def test_installed_evidence_overrides_none_before_uninstall_or_purge_without_migrations(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    operation: str,
) -> None:
    plugin = build_plugin(tmp_path)
    prior_references = {'legacy.resource': ['plugins.reference_demo.references:has_references']}
    async with ownership_db() as db:
        db.add(legacy_record(installed=True))
        db.add(SysPluginResourceOwnership(plugin_id=PLUGIN_ID, state='none', resource_references=prior_references))
        await db.commit()
        assert (await db.execute(select(SysPluginMigration))).scalars().all() == []
        db.calls = []
        assert await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')
        assert (await ownership(db)).state == 'none', 'The read-only checker must not reconcile state'
        if operation == 'uninstall_with_source':
            await PluginService.mark_plugin_uninstalled_services(db, PLUGIN_ID, plugin)
        elif operation == 'uninstall_without_source':
            await PluginService.mark_plugin_uninstalled_services(db, PLUGIN_ID)
        elif operation == 'purge_with_source':
            await PluginService.purge_plugin_services(db, plugin)
        else:
            await PluginService.purge_plugin_metadata_by_id_services(db, PLUGIN_ID)
        await db.commit()
    async with ownership_db() as db:
        record = await db.get(SysPlugin, PLUGIN_ID)
        if operation.startswith('uninstall'):
            assert record.installed_version is None
        else:
            assert record is None
        registry = PluginRegistry.build([plugin], [record] if record is not None else [])
        row = await ownership(db)
        assert row.resource_references['legacy.resource'] == prior_references['legacy.resource']
        db.calls = []
        if operation.endswith('with_source'):
            assert await has_resource_references(registry, db, 'ai.model', 'm')
            assert row.state == 'retained'
            assert row.resource_references['ai.model'] == [plugin.manifest.backend.resource_references['ai.model']]
        else:
            with pytest.raises(RuntimeError, match='归属未知'):
                await has_resource_references(registry, db, 'ai.model', 'm')
            assert row.state == 'unknown'
            assert db.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['uninstall', 'purge', 'metadata_purge'])
async def test_installed_evidence_does_not_promote_existing_unknown_during_preservation(
    ownership_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    operation: str,
) -> None:
    plugin = build_plugin(tmp_path, body='raise AssertionError("unknown history must not query business tables")')
    async with ownership_db() as db:
        db.add(legacy_record(installed=True))
        db.add(SysPluginResourceOwnership(plugin_id=PLUGIN_ID, state='unknown', resource_references={}))
        await db.commit()
        if operation == 'uninstall':
            await PluginService.mark_plugin_uninstalled_services(db, PLUGIN_ID, plugin)
        elif operation == 'purge':
            await PluginService.purge_plugin_services(db, plugin)
        else:
            await PluginService.purge_plugin_metadata_by_id_services(db, PLUGIN_ID)
        await db.commit()
    async with ownership_db() as db:
        assert (await ownership(db)).state == 'unknown'
        with pytest.raises(RuntimeError, match='归属未知'):
            await has_resource_references(PluginRegistry.build([plugin]), db, 'ai.model', 'm')


@pytest_asyncio.fixture
async def ownership_snapshot_db(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """File SQLite gives the reader and lifecycle writer independent connections."""
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "ownership.sqlite"}')
    async with engine.begin() as connection:
        tables = [
            SysPlugin,
            SysPluginConfig,
            SysPluginMenu,
            SysPluginMigration,
            SysPluginResourceOwnership,
            SysMenu,
            SysRoleMenu,
        ]
        await connection.run_sync(Base.metadata.create_all, tables=[model.__table__ for model in tables])
        await create_sqlite_sys_job_table(connection)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('initial_ownership', ['missing', 'none'])
async def test_purge_commit_after_first_read_cannot_mix_evidence_snapshots(
    ownership_snapshot_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    initial_ownership: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = build_plugin(tmp_path)
    async with ownership_snapshot_db() as setup:
        setup.add(legacy_record(installed=True))
        if initial_ownership == 'none':
            setup.add(SysPluginResourceOwnership(plugin_id=PLUGIN_ID, state='none', resource_references={}))
        await setup.commit()
    async with ownership_snapshot_db() as reader:
        execute = reader.execute
        purged = False
        read_statements = 0

        async def execute_then_purge(statement: object, *args, **kwargs) -> object:
            nonlocal purged, read_statements
            result = await execute(statement, *args, **kwargs)
            read_statements += 1
            if not purged:
                purged = True
                async with ownership_snapshot_db() as writer:
                    await PluginService.purge_plugin_services(writer, plugin)
                    await writer.commit()
            return result

        monkeypatch.setattr(reader, 'execute', execute_then_purge)
        reader.calls = []
        assert await has_resource_references(PluginRegistry.build([plugin]), reader, 'ai.model', 'm')
        assert purged
        assert read_statements == 1
        assert reader.calls == [(PLUGIN_ID, 'm')]
    async with ownership_snapshot_db() as verification:
        assert await verification.get(SysPlugin, PLUGIN_ID) is None
        assert (await ownership(verification)).state == 'retained'


@pytest.mark.asyncio
async def test_reference_evidence_ignores_stale_orm_without_overwriting_pending_changes(
    ownership_snapshot_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    plugin = build_plugin(tmp_path)
    async with ownership_snapshot_db() as setup:
        await PluginService.upsert_discovered_plugin_services(setup, plugin, plugin.backend_path.parent)
        await setup.commit()
    async with ownership_snapshot_db() as reader:
        cached_ownership = await ownership(reader)
        cached_plugin = await reader.get(SysPlugin, PLUGIN_ID)
        original_description = cached_plugin.description
        assert cached_ownership.state == 'none'
        assert cached_plugin.installed_version is None
        transaction = reader.sync_session.get_transaction()
        async with ownership_snapshot_db() as writer:
            await PluginService.mark_plugin_installed_services(writer, plugin)
            await PluginService.mark_plugin_uninstalled_services(writer, PLUGIN_ID, plugin)
            await writer.commit()
        pending_references = {'pending.resource': ['plugins.reference_demo.references:has_references']}
        cached_ownership.resource_references = pending_references
        cached_plugin.description = 'caller pending description'
        reader.commit = AsyncMock(wraps=reader.commit)
        reader.rollback = AsyncMock(wraps=reader.rollback)
        reader.calls = []

        assert await has_resource_references(PluginRegistry.build([plugin]), reader, 'ai.model', 'm')

        assert reader.sync_session.get_transaction() is transaction
        assert cached_ownership.state == 'none'
        assert cached_ownership.resource_references == pending_references
        assert cached_plugin.description == 'caller pending description'
        assert cached_ownership in reader.dirty
        assert cached_plugin in reader.dirty
        reader.commit.assert_not_awaited()
        reader.rollback.assert_not_awaited()
        await reader.rollback()
    async with ownership_snapshot_db() as verification:
        assert (await ownership(verification)).state == 'retained'
        assert (await ownership(verification)).resource_references == {
            'ai.model': [plugin.manifest.backend.resource_references['ai.model']]
        }
        assert (await verification.get(SysPlugin, PLUGIN_ID)).description == original_description
