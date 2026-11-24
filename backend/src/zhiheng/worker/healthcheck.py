"""Check the worker heartbeat from this container."""

import asyncio
from socket import gethostname

from redis.asyncio import Redis

from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.worker_heartbeat import worker_heartbeat_age


async def main() -> int:
    settings = Settings()
    async with Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True) as redis:
        age = await worker_heartbeat_age(redis, settings.worker_heartbeats_key, hostname=gethostname())
    return 0 if age is not None else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
