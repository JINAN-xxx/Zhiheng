"""Explicit, non-owner PostgreSQL pool with complete immutable catalog validation."""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from importlib.resources import files
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from lightrag.kg.postgres_impl import PostgreSQLDB  # type: ignore[import-untyped]
from zhiheng.modules.knowledge.adapters.lightrag.schema import GRAPH_SCHEMA
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import asyncpg  # type: ignore[import-untyped]

MAX_DATABASE_PORT = 65535

CURRENT_STORAGE: ContextVar[PostgresContext] = ContextVar('graph_storage_context')
_COLUMNS = """SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,
pg_get_expr(d.adbin,d.adrelid) AS default_value FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
WHERE n.nspname=$1 AND c.relkind='r' ORDER BY c.relname,a.attnum"""
_INDEXES = 'SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=$1 ORDER BY indexname'
_CONSTRAINTS = """SELECT c.conname,c.contype::text AS contype,pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname=$1 ORDER BY c.conname"""


async def catalog(connection: asyncpg.Connection[Any]) -> dict[str, object]:
    async with connection.transaction():
        await connection.execute('SET LOCAL search_path TO pg_catalog')
        return {
            name: [dict(row) for row in await connection.fetch(query, GRAPH_SCHEMA)]
            for name, query in [('columns', _COLUMNS), ('indexes', _INDEXES), ('constraints', _CONSTRAINTS)]
        }


class _ExplicitDatabase(PostgreSQLDB):  # type: ignore[misc]
    pool: Any

    def __init__(self, config: dict[str, Any], owner: PostgresContext) -> None:
        super().__init__(config)
        self.owner = owner

    async def initdb(self) -> None:
        await super().initdb()
        try:
            await self.owner.verify()
        except BaseException:
            if self.pool is not None:
                await self.owner.close_pool()
            raise

    async def _run_with_retry(self, operation: Callable[..., Awaitable[Any]], **kwargs: Any) -> Any:
        self.owner.check()
        if self.owner.read_only:
            # Server read-only transactions guard closures as well as SQL strings.
            async def read(connection: Any) -> Any:
                async with connection.transaction(readonly=True):
                    return await operation(connection)

            return await super()._run_with_retry(read, **kwargs)
        return await super()._run_with_retry(operation, **kwargs)

    async def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await super().execute(sql, *args, **kwargs)


class PostgresContext:
    def __init__(self, *, url: str, workspace: str, read_only: bool, check: Callable[[], None]) -> None:
        parsed = urlsplit(url)
        try:
            port = parsed.port if parsed.port is not None else 5432
        except ValueError:
            raise AppError('graph_unavailable', '图谱数据库端口无效', 503) from None
        if not 1 <= port <= MAX_DATABASE_PORT:
            raise AppError('graph_unavailable', '图谱数据库端口无效', 503)
        if (
            parsed.scheme != 'postgresql'
            or not parsed.hostname
            or not parsed.username
            or not parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith('/')
            or not parsed.path[1:]
            or '/' in parsed.path[1:]
        ):
            raise AppError('graph_unavailable', '图谱数据库连接无效', 503)
        self.workspace, self.read_only, self.check = workspace, read_only, check
        self.leases: set[int] = set()
        self.db = _ExplicitDatabase(
            {
                'host': parsed.hostname,
                'port': port,
                'user': unquote(parsed.username),
                'password': unquote(parsed.password),
                'database': unquote(parsed.path[1:]),
                'workspace': workspace,
                'max_connections': 4,
                'enable_vector': False,
                'server_settings': f'search_path={GRAPH_SCHEMA},pg_catalog&statement_timeout=8000',
                'connection_retry_attempts': 1,
                'connection_retry_backoff': 0,
                'connection_retry_backoff_max': 0,
                'pool_close_timeout': 5,
            },
            self,
        )

    def write(self) -> None:
        self.check()
        if self.read_only:
            raise AppError('graph_read_only', '活动图谱禁止写入', 409)

    async def verify(self) -> None:
        if self.db.pool is None:
            raise AppError('graph_unavailable', '图谱数据库未连接', 503)
        expected: object = json.loads(files(__package__).joinpath('catalog.json').read_text())
        async with self.db.pool.acquire() as connection:
            if (
                await connection.fetchval('SELECT current_schema()') != GRAPH_SCHEMA
                or await connection.fetchval("SELECT has_schema_privilege(current_user,current_schema(),'CREATE')")
                or await connection.fetchval(
                    """SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls OR r.rolreplication
                    OR EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid)
                    OR EXISTS(SELECT 1 FROM pg_class c WHERE c.relowner=r.oid AND c.relkind IN ('r','p','S','v','m','f'))
                    OR EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspowner=r.oid)
                    OR EXISTS(SELECT 1 FROM pg_proc p WHERE p.proowner=r.oid)
                    OR EXISTS(SELECT 1 FROM pg_database d WHERE d.datdba=r.oid)
                    FROM pg_roles r WHERE r.rolname=current_user"""
                )
                or await connection.fetchval(
                    "SELECT has_database_privilege(current_user,current_database(),'TEMPORARY')"
                )
                or await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE has_schema_privilege(current_user,oid,'CREATE'))"
                )
                or await connection.fetchval("SELECT has_database_privilege(current_user,current_database(),'CREATE')")
                or not await connection.fetchval(
                    """SELECT count(*)=10 AND coalesce(bool_and(
                    has_table_privilege(current_user,c.oid,'SELECT') AND has_table_privilege(current_user,c.oid,'INSERT')
                    AND has_table_privilege(current_user,c.oid,'UPDATE') AND has_table_privilege(current_user,c.oid,'DELETE')
                    ),false) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=$1 AND c.relkind='r'""",
                    GRAPH_SCHEMA,
                )
                or await connection.fetchval(
                    'SELECT EXISTS(SELECT 1 FROM lightrag_graph_edges WHERE src_id COLLATE "C" > tgt_id COLLATE "C")'
                )
                or await catalog(connection) != expected
            ):
                raise AppError('graph_schema_incompatible', '图谱数据库权限或结构不兼容，请运行迁移', 503)

    async def open(self) -> None:
        try:
            await asyncio.wait_for(self.db.initdb(), timeout=8)
        except asyncio.TimeoutError:
            await self.close_pool()
            raise AppError('graph_storage_timeout', '图谱数据库初始化超时', 503) from None

    async def acquire(self, owner: object) -> _ExplicitDatabase:
        await self.verify()
        self.leases.add(id(owner))
        return self.db

    def release(self, owner: object) -> None:
        self.leases.remove(id(owner))

    async def close_pool(self) -> None:
        pool = self.db.pool
        if pool is None:
            return
        closer = asyncio.create_task(pool.close())
        deadline = monotonic() + 5
        try:
            while not closer.done():
                try:
                    await asyncio.wait_for(asyncio.shield(closer), timeout=max(0.001, deadline - monotonic()))
                except asyncio.CancelledError:  # noqa: PERF203 - retain resource ownership through repeated cancellation
                    continue
                except asyncio.TimeoutError:
                    pool.terminate()
                    await asyncio.gather(closer, return_exceptions=True)
            if closer.cancelled():
                pool.terminate()
            elif closer.exception() is not None:
                pool.terminate()
                raise AppError('graph_storage_close_failed', '图谱数据库连接未正常关闭', 503)
        finally:
            self.db.pool = None

    async def reconnect(self) -> None:
        await self.close_pool()
        await self.open()

    async def close(self) -> None:
        try:
            await self.close_pool()
        finally:
            if self.leases:
                raise AppError('graph_storage_leaked', '图谱存储尚未释放', 503)
