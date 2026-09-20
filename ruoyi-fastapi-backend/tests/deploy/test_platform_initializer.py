import hashlib
import importlib.util
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine

from config.env import DataBaseConfig
from plugins.core.management.entity.do.models import SysPlugin, SysPluginMigration

ROOT = Path(__file__).resolve().parents[2]


def initializer() -> object:
    path = ROOT / 'scripts/initialize_platform.py'
    assert path.is_file(), '完整部署需要在后端启动前完成初始化'
    spec = importlib.util.spec_from_file_location('platform_initializer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_key_initialization_reuses_key_and_never_calls_models() -> None:
    module = initializer()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers['Authorization'] == 'Bearer sk-runtime'
        return httpx.Response(200, json={'data': []})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url='http://runtime') as client:
        module.ensure_runtime_key(client, 'sk-runtime', 'sk-management')
    assert calls == [('GET', '/v1/models')]


def test_runtime_key_is_created_with_the_persisted_key_and_verified() -> None:
    module = initializer()
    created = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == 'POST':
            assert request.url.path == '/key/generate'
            assert request.headers['Authorization'] == 'Bearer sk-management'
            body = json.loads(request.content)
            assert body['key'] == 'sk-runtime'
            assert body['key_type'] == 'llm_api'
            created.append(body)
            return httpx.Response(200, json={'key': 'sk-runtime'})
        return httpx.Response(200 if created else 401, json={'data': []})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url='http://runtime') as client:
        module.ensure_runtime_key(client, 'sk-runtime', 'sk-management')
        module.ensure_runtime_key(client, 'sk-runtime', 'sk-management')
    assert len(created) == 1


def test_runtime_auth_failure_does_not_expose_provider_response() -> None:
    module = initializer()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={'error': 'private-password private-token'})

    with (
        httpx.Client(transport=httpx.MockTransport(handler), base_url='http://runtime') as client,
        pytest.raises(module.BootstrapError) as caught,
    ):
        module.ensure_runtime_key(client, 'sk-runtime', 'sk-management')
    assert 'private' not in str(caught.value)


def test_builtin_plugin_sync_selects_install_upgrade_and_preserves_disabled_plugins() -> None:
    module = initializer()
    states = {'ai': {'installed_version': 'old-version', 'enabled': '1'}}
    actions = module.plugin_actions(states)
    assert actions == [('upgrade', 'ai'), ('install', 'agent_extension'), ('enable', 'agent_extension')]


def test_bundled_plugin_resources_use_startup_lifecycle_without_development_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = initializer()
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        {'plugin_id': name, 'installed_version': '1.0.0', 'status': 'installed', 'enabled': '0'}
        for name in module.BUILTIN_PLUGINS
    ]
    monkeypatch.setattr(module.psycopg2, 'connect', lambda **kwargs: connection)
    monkeypatch.setattr(module, 'initialize_plugin_migrations', AsyncMock())
    sync_resources = AsyncMock()
    monkeypatch.setattr(module, 'synchronize_plugin_resources', sync_resources, raising=False)
    cli = MagicMock(return_value=MagicMock(returncode=1))
    monkeypatch.setattr(subprocess, 'run', cli)
    module.initialize_plugins({})
    sync_resources.assert_awaited_once()
    cli.assert_not_called()


@pytest.fixture
def migration_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple]:
    """Use real plugin migration models, SQL execution and history persistence."""
    module = initializer()
    database_file = tmp_path / 'migration.db'
    sync_engine = create_engine(f'sqlite:///{database_file}')
    SysPlugin.__table__.create(sync_engine)
    SysPluginMigration.__table__.create(sync_engine)
    for plugin_id in module.BUILTIN_PLUGINS:
        plugin_root = tmp_path / 'plugins' / plugin_id
        migration = plugin_root / 'migrations/postgresql/001_probe.sql'
        migration.parent.mkdir(parents=True)
        migration.write_text(
            f'create table {plugin_id}_migration_probe (id integer primary key);\n'
            f'insert into {plugin_id}_migration_probe values (1);\n',
            encoding='utf-8',
        )
        (plugin_root / 'plugin.yaml').write_text(
            f'id: {plugin_id}\nname: {plugin_id}\nversion: 1.0.0\n'
            f'backend:\n  module: plugins.{plugin_id}\n  migrations:\n'
            '    - migrations/postgresql/001_probe.sql\n',
            encoding='utf-8',
        )
        with sync_engine.begin() as connection:
            connection.execute(
                SysPlugin.__table__.insert().values(
                    plugin_id=plugin_id,
                    plugin_name=plugin_id,
                    version='1.0.0',
                    installed_version='1.0.0',
                    enabled='1',
                    status='installed',
                )
            )

    engines = []
    disposed = []

    def build_test_engine(url: object, **kwargs: object) -> object:
        assert url.drivername == 'postgresql+asyncpg'
        assert url.database == 'isolated-test'
        engine = create_async_engine(f'sqlite+aiosqlite:///{database_file}')
        engines.append(engine)
        event.listen(engine.sync_engine, 'engine_disposed', lambda _engine: disposed.append(engine))
        return engine

    def connect_to_plugin_states(**kwargs: object) -> object:
        connection = MagicMock()
        with sync_engine.connect() as query:
            rows = [dict(row) for row in query.execute(select(SysPlugin.__table__)).mappings()]
        connection.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
        return connection

    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'create_async_engine', build_test_engine, raising=False)
    monkeypatch.setattr(module.psycopg2, 'connect', connect_to_plugin_states)
    monkeypatch.setattr(DataBaseConfig, 'db_type', 'postgresql')
    cli = AsyncMock()
    monkeypatch.setattr(module, 'synchronize_plugin_resources', cli)
    settings = {'host': 'localhost', 'port': 5432, 'dbname': 'isolated-test', 'user': 'test', 'password': 'test'}
    try:
        yield module, sync_engine, engines, disposed, cli, settings
    finally:
        sync_engine.dispose()


def test_same_version_plugins_apply_missing_migrations_before_cli_resources(migration_environment: tuple) -> None:
    module, database, engines, disposed, cli, settings = migration_environment

    module.initialize_plugins(settings)

    for plugin_id in module.BUILTIN_PLUGINS:
        assert inspect(database).has_table(f'{plugin_id}_migration_probe')
        source = module.ROOT / 'plugins' / plugin_id / 'migrations/postgresql/001_probe.sql'
        with database.connect() as connection:
            record = (
                connection.execute(
                    select(SysPluginMigration.__table__).where(SysPluginMigration.plugin_id == plugin_id)
                )
                .mappings()
                .one()
            )
        assert record['migration_checksum'] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert record['status'] == 'success'
        assert record['attempt_count'] == 1
        assert record['version'] == '1.0.0'
        assert record['statement_count'] == len(source.read_text().splitlines())

    assert len(engines) == 1 and disposed == engines
    assert set(cli.await_args.args[0]) == {'ai', 'agent_extension'}

    # A second invocation uses a new engine and skips applied non-idempotent DDL.
    module.initialize_plugins(settings)
    assert disposed == engines
    with database.connect() as connection:
        assert all(row.attempt_count == 1 for row in connection.execute(select(SysPluginMigration.__table__)))


def test_same_version_checksum_mismatch_fails_main_before_cli_or_backend(
    migration_environment: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module, database, engines, disposed, cli, settings = migration_environment
    with database.begin() as connection:
        connection.execute(
            SysPluginMigration.__table__.insert().values(
                plugin_id='ai',
                migration_path='migrations/postgresql/001_probe.sql',
                migration_checksum='0' * 64,
                version='1.0.0',
                statement_count=2,
                status='success',
                attempt_count=1,
            )
        )
    monkeypatch.setattr(module, 'database_settings', lambda: settings)
    monkeypatch.setattr(module, 'initialize_database', MagicMock())
    monkeypatch.setattr(module, 'ensure_runtime_key', MagicMock())
    monkeypatch.setenv('PLATFORM_POSTGRES_ADMIN_PASSWORD', 'admin-test')
    monkeypatch.setenv('LITELLM_BASE_URL', 'http://unused-test')
    monkeypatch.setenv('LITELLM_RUNTIME_KEY', 'sk-test')
    monkeypatch.setenv('LITELLM_MANAGEMENT_KEY', 'sk-management-test')

    assert module.main() == 1

    assert not inspect(database).has_table('ai_migration_probe')
    cli.assert_not_called()
    assert len(engines) == 1 and disposed == engines
    assert 'migration' in capsys.readouterr().err
    with database.connect() as connection:
        record = connection.execute(select(SysPluginMigration.__table__)).mappings().one()
    assert record['migration_checksum'] == '0' * 64
    assert record['status'] == 'success'
