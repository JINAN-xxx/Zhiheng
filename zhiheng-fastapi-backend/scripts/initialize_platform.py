"""统一 Compose 的一次性数据库、模型网关和插件初始化任务。"""

import asyncio
import os
import sys
from http import HTTPStatus
from importlib import import_module
from pathlib import Path

import httpx
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BUILTIN_PLUGINS = ('ai', 'agent_extension')


class BootstrapError(Exception):
    """初始化边界上的安全错误。"""


def database_settings() -> dict:
    return {
        'host': os.environ.get('DB_HOST', 'litellm-db'),
        'port': int(os.environ.get('DB_PORT', '5432')),
        'dbname': os.environ.get('DB_DATABASE', 'zhiheng-fastapi'),
        'user': os.environ.get('DB_USERNAME', 'zhiheng'),
        'password': os.environ['DB_PASSWORD'],
        'connect_timeout': 10,
    }


def initialize_database(settings: dict, admin_password: str, sql_path: Path) -> None:
    admin = psycopg2.connect(**dict(settings, dbname='postgres', user='litellm', password=admin_password))
    try:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_lock(982147, 1)')
            cursor.execute('SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s', (settings['dbname'],))
            row = cursor.fetchone()
            if row is not None and row[0] != settings['user']:
                raise BootstrapError('业务数据库已存在但所有者不匹配，初始化已停止')
            if row is None and settings['dbname'] in ('ruoyi-fastapi', 'zhiheng-fastapi'):
                other_name = 'zhiheng-fastapi' if settings['dbname'] == 'ruoyi-fastapi' else 'ruoyi-fastapi'
                cursor.execute('SELECT 1 FROM pg_database WHERE datname = %s', (other_name,))
                if cursor.fetchone():
                    raise BootstrapError(
                        f'检测到已有业务数据库 {other_name}，拒绝创建第二个空库；'
                        '请在部署环境中将 PLATFORM_DB_NAME 和 PLATFORM_DB_USER 配置为已有数据库及其所有者'
                    )
            cursor.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', (settings['user'],))
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL('CREATE ROLE {} LOGIN PASSWORD %s').format(sql.Identifier(settings['user'])),
                    (settings['password'],),
                )
            if row is None:
                cursor.execute(
                    sql.SQL('CREATE DATABASE {} OWNER {}').format(
                        sql.Identifier(settings['dbname']), sql.Identifier(settings['user'])
                    )
                )
        # 以业务角色验证已保存密码；已有角色密码不自动重置。
        connection = psycopg2.connect(**settings)
        try:
            with connection, connection.cursor() as cursor:
                for marker in ('zhiheng_bootstrap_state', 'ruoyi_bootstrap_state'):
                    cursor.execute('SELECT to_regclass(%s)', (f'public.{marker}',))
                    if cursor.fetchone()[0]:
                        cursor.execute(
                            sql.SQL('SELECT 1 FROM public.{} WHERE marker_id=1').format(sql.Identifier(marker))
                        )
                        if cursor.fetchone():
                            print('数据库已初始化，保留现有数据并检查增量迁移。', flush=True)
                            return
                cursor.execute("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'")
                if cursor.fetchone()[0]:
                    raise BootstrapError('数据库非空且缺少初始化完成标记，拒绝导入完整 SQL')
                cursor.execute(sql_path.read_text(encoding='utf-8'))
                cursor.execute(
                    'CREATE TABLE public.zhiheng_bootstrap_state (marker_id smallint PRIMARY KEY CHECK(marker_id=1), completed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)'
                )
                cursor.execute('INSERT INTO public.zhiheng_bootstrap_state(marker_id) VALUES(1)')
            print('完整 PostgreSQL 初始化 SQL 已导入。', flush=True)
        finally:
            connection.close()
    finally:
        admin.close()


def ensure_runtime_key(client: httpx.Client, runtime_key: str, management_key: str) -> None:
    runtime_headers = {'Authorization': f'Bearer {runtime_key}'}
    response = client.get('/v1/models', headers=runtime_headers)
    if response.status_code == HTTPStatus.OK:
        return
    if response.status_code not in (401, 403):
        raise BootstrapError(f'模型网关尚未就绪（HTTP {response.status_code}），请重试初始化')
    response = client.post(
        '/key/generate',
        headers={'Authorization': f'Bearer {management_key}'},
        json={'key': runtime_key, 'key_alias': 'platform-runtime', 'key_type': 'llm_api', 'models': []},
    )
    if response.status_code != HTTPStatus.OK:
        raise BootstrapError(f'模型网关运行密钥初始化失败（HTTP {response.status_code}），请检查管理密钥')
    if response.json().get('key') != runtime_key:
        raise BootstrapError('模型网关未接受已保存的运行密钥，初始化已停止')
    response = client.get('/v1/models', headers=runtime_headers)
    if response.status_code != HTTPStatus.OK:
        raise BootstrapError('模型网关运行密钥创建后校验失败')


def plugin_actions(states: dict[str, dict]) -> list[tuple[str, str]]:
    actions = []
    for plugin_id in BUILTIN_PLUGINS:
        state = states.get(plugin_id)
        actions.append(('upgrade' if state and state.get('installed_version') else 'install', plugin_id))
        if not state or state.get('enabled') == '0':
            actions.append(('enable', plugin_id))
    return actions


async def initialize_plugin_migrations(settings: dict) -> None:
    """校验全部历史并补齐迁移，即使已安装版本与 manifest 相同。"""
    # 数据库初始化完成后才加载应用服务，配置错误由 main 的安全错误边界处理。
    database_config = import_module('config.env').DataBaseConfig
    migration_error = import_module('plugins.core.lifecycle.migration').PluginMigrationError
    runtime_gateway = import_module('plugins.core.management.service.gateway').PluginManagementRuntimeGateway
    runtime_builder = import_module('plugins.core.runtime.bootstrap').PluginRuntimeBuilder

    if database_config.db_type != 'postgresql':
        raise BootstrapError('完整部署的插件迁移要求 DB_TYPE=postgresql')
    engine = create_async_engine(
        URL.create(
            'postgresql+asyncpg',
            username=settings['user'],
            password=settings['password'],
            host=settings['host'],
            port=settings['port'],
            database=settings['dbname'],
        ),
        echo=False,
        connect_args={'timeout': settings.get('connect_timeout', 10)},
    )
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    class BootstrapMigrationGateway(runtime_gateway):
        """为生产迁移器及历史存储提供当前事件循环独占的会话。"""

        @staticmethod
        def get_async_session_local() -> async_sessionmaker:
            return session_factory

    try:
        discovered = {plugin.manifest.id: plugin for plugin in runtime_builder(ROOT).discover_plugins()}
        gateway = BootstrapMigrationGateway()
        for plugin_id in BUILTIN_PLUGINS:
            if plugin_id not in discovered:
                raise BootstrapError(f'内置插件 {plugin_id} 清单缺失或无效，后端不会启动')
            print(f'内置插件 {plugin_id}：校验并执行增量 migration', flush=True)
            try:
                await gateway.run_plugin_migrations(discovered[plugin_id])
            except migration_error as error:
                raise BootstrapError(
                    f'内置插件 {plugin_id} migration {error.migration_path} 校验或执行失败，'
                    '后端不会启动，请通过插件 CLI 检查迁移状态后重试'
                ) from error
    finally:
        # 所有插件在同一次 asyncio.run 中执行；不要跨事件循环复用全局连接池。
        await engine.dispose()


async def synchronize_plugin_resources(states: dict[str, dict]) -> None:
    """通过内置插件启动生命周期同步已随发布包提供的资源。"""
    database_module = import_module('config.database')
    startup_module = import_module('plugins.core.runtime.startup')
    gateway_module = import_module('plugins.core.management.service.startup_gateway')
    builder_module = import_module('plugins.core.runtime.bootstrap')
    manager = startup_module.PluginRuntimeStartupManager(
        builder=builder_module.PluginRuntimeBuilder(ROOT),
        management_gateway=gateway_module.PluginManagementStartupGateway(),
    )
    actions = plugin_actions(states)
    discovered = {plugin.manifest.id: plugin for plugin in manager.builder.discover_plugins()}
    try:
        for plugin_id in BUILTIN_PLUGINS:
            if plugin_id not in discovered:
                raise BootstrapError(f'缺少内置插件 {plugin_id}')
            print(f'内置插件 {plugin_id}：同步菜单、配置和安装状态', flush=True)
            await manager.sync_plugin_install(discovered[plugin_id], enabled=('enable', plugin_id) in actions)
    finally:
        await database_module.async_engine.dispose()


def initialize_plugins(settings: dict) -> None:
    asyncio.run(initialize_plugin_migrations(settings))
    connection = psycopg2.connect(**settings)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                'SELECT plugin_id, installed_version, enabled FROM sys_plugin WHERE plugin_id = ANY(%s)',
                (list(BUILTIN_PLUGINS),),
            )
            states = {row['plugin_id']: dict(row) for row in cursor.fetchall()}
    finally:
        connection.close()
    asyncio.run(synchronize_plugin_resources(states))
    connection = psycopg2.connect(**settings)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                'SELECT plugin_id, status, installed_version FROM sys_plugin WHERE plugin_id = ANY(%s)',
                (list(BUILTIN_PLUGINS),),
            )
            rows = cursor.fetchall()
            if len(rows) != len(BUILTIN_PLUGINS) or any(
                row['status'] != 'installed' or not row['installed_version'] for row in rows
            ):
                raise BootstrapError('内置插件安装状态不完整，后端不会启动')
    finally:
        connection.close()


def main() -> int:
    try:
        settings = database_settings()
        initialize_database(settings, os.environ['PLATFORM_POSTGRES_ADMIN_PASSWORD'], ROOT / 'sql/platform-pg.sql')
        with httpx.Client(base_url=os.environ['LITELLM_BASE_URL'], timeout=30) as client:
            ensure_runtime_key(client, os.environ['LITELLM_RUNTIME_KEY'], os.environ['LITELLM_MANAGEMENT_KEY'])
        print('模型网关运行密钥已就绪。', flush=True)
        initialize_plugins(settings)
        print('平台初始化完成。', flush=True)
        return 0
    except BootstrapError as error:
        print(f'初始化失败：{error}', file=sys.stderr)
    except Exception as error:
        # 外部响应、连接串和 SQL 绑定参数可能包含密钥，不直接输出异常正文。
        print(f'初始化失败（{type(error).__name__}），请检查数据库、模型网关和环境配置后重试。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
