"""Resource traffic never consumes model RPM/TPM, reservations, or balances."""

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.keys import DeveloperIdentity, KeyService
from zhiheng.modules.developer.orm import ResourceMinuteBucket
from zhiheng.modules.developer.scopes import RESOURCE_RPM_MAX
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


class ResourceLimitService:
    def __init__(self, keys: KeyService, *, platform_rpm: int = RESOURCE_RPM_MAX) -> None:
        if type(platform_rpm) is not int or not 1 <= platform_rpm <= RESOURCE_RPM_MAX:
            raise ValueError('Invalid platform resource RPM')
        self.keys, self.platform_rpm = keys, platform_rpm

    async def consume(self, session: AsyncSession, identity: DeveloperIdentity) -> None:
        key = await self.keys.revalidate(session, identity, lock=True)
        now = int(utcnow().timestamp())
        minute = now // 60
        bucket = await session.get(ResourceMinuteBucket, (identity.key_id, minute), populate_existing=True)
        if bucket is None:
            bucket = ResourceMinuteBucket(key_id=identity.key_id, minute=minute, requests=0)
            session.add(bucket)
        if bucket.requests >= min(key.resource_rpm_limit, self.platform_rpm):
            raise AppError('resource_rate_limit', '资源请求频率超过上限', 429, details={'retry_after': 60 - now % 60})
        bucket.requests += 1
        await session.flush()
