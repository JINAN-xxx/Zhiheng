"""Read-only runtime facts for administrators; no deployment secrets or cache mutation."""

import asyncio
import os
import platform
import shutil
from pathlib import Path
from time import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency

router = APIRouter(prefix='/api/admin', tags=['admin-monitor'])
Health = Literal['ok', 'unavailable']
WORKER_HEARTBEAT_MAX_AGE_SECONDS = 15


class PlatformInfo(BaseModel):
    python_version: str
    system: str
    cpu_count: int | None
    rss_bytes: int | None
    disk_total_bytes: int | None
    disk_used_bytes: int | None


class DatabaseInfo(BaseModel):
    status: Health = 'unavailable'
    version: str | None = None
    size_bytes: int | None = None
    connections: int | None = None


class RedisInfo(BaseModel):
    status: Health = 'unavailable'
    used_memory_bytes: int | None = None
    connected_clients: int | None = None
    uptime_seconds: int | None = None
    db_keys: int | None = None


class WorkerInfo(BaseModel):
    status: Health = 'unavailable'
    heartbeat_age_seconds: float | None = None


class MonitorInfo(BaseModel):
    platform: PlatformInfo
    database: DatabaseInfo
    redis: RedisInfo
    worker: WorkerInfo
    schema_version: str | None


def _rss() -> int | None:
    try:
        pages = int(Path('/proc/self/statm').read_text().split()[1])
        return pages * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        return None


def _number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@router.get('/monitor')
async def monitor(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> MonitorInfo:
    principal.require_permission('system:monitor:read')
    total = used = None
    try:
        disk = shutil.disk_usage(container.settings.files_root)
        total, used = disk.total, disk.used
    except OSError:
        pass
    database = DatabaseInfo()
    schema = None
    try:
        row = (
            await session.execute(
                text(
                    "SELECT current_setting('server_version'), pg_database_size(current_database()), "
                    '(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()), '
                    '(SELECT version_num FROM alembic_version)'
                )
            )
        ).one()
        database = DatabaseInfo(status='ok', version=row[0], size_bytes=row[1], connections=row[2])
        schema = str(row[3])
    except SQLAlchemyError:
        await session.rollback()
    redis = RedisInfo()
    worker = WorkerInfo()
    try:
        memory, clients, server, keys = await asyncio.gather(
            container.redis.info('memory'),
            container.redis.info('clients'),
            container.redis.info('server'),
            container.redis.dbsize(),
        )
        redis = RedisInfo(
            status='ok',
            used_memory_bytes=_number(memory.get('used_memory')),
            connected_clients=_number(clients.get('connected_clients')),
            uptime_seconds=_number(server.get('uptime_in_seconds')),
            db_keys=keys,
        )
        heartbeat = await container.redis.get(container.settings.worker_health_key)
        if heartbeat is not None:
            age = max(0.0, time() - float(heartbeat))
            worker = WorkerInfo(
                status='ok' if age <= WORKER_HEARTBEAT_MAX_AGE_SECONDS else 'unavailable',
                heartbeat_age_seconds=age,
            )
    except (RedisError, ValueError, TypeError):
        pass
    return MonitorInfo(
        platform=PlatformInfo(
            python_version=platform.python_version(),
            system=platform.system(),
            cpu_count=os.cpu_count(),
            rss_bytes=_rss(),
            disk_total_bytes=total,
            disk_used_bytes=used,
        ),
        database=database,
        redis=redis,
        worker=worker,
        schema_version=schema,
    )
