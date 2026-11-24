"""Read current worker heartbeats without mutating the worker registry."""

from __future__ import annotations

from time import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

WORKER_HEARTBEAT_TTL_SECONDS = 15
WORKER_HEARTBEAT_INTERVAL_SECONDS = 2


async def worker_heartbeat_age(
    redis: Redis, key: str, *, hostname: str | None = None, legacy_key: str | None = None
) -> float | None:
    now = time()
    oldest = now - WORKER_HEARTBEAT_TTL_SECONDS
    if hostname is None:
        rows = await redis.zrevrangebyscore(key, now, oldest, start=0, num=1, withscores=True)
    else:
        rows = await redis.zrevrangebyscore(key, now, oldest, withscores=True)
        rows = [row for row in rows if row[0].startswith(f'{hostname}:')]
    if rows:
        return now - float(rows[0][1])
    # During a rolling deployment an older worker can still publish the former single timestamp.
    if hostname is None and legacy_key is not None:
        previous = await redis.get(legacy_key)
        if previous is not None:
            try:
                age = now - float(previous)
            except (ValueError, TypeError):
                return None
            if 0 <= age <= WORKER_HEARTBEAT_TTL_SECONDS:
                return age
    return None
