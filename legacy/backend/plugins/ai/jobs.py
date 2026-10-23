"""AI 插件定时任务：Public API 配额与 Ledger 对账。"""

import asyncio

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config.database import AsyncSessionLocal
from config.get_redis import RedisUtil
from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao
from plugins.ai.service.public_invocation_coordinator import PublicQuotaEngine, PublicQuotaReconciler


async def reconcile_public_quota(
    query_db: AsyncSession | None = None,
    redis: Redis | None = None,
    **kwargs: object,
) -> int:
    """将超时 started Usage Ledger 置为 abandoned 并保守结算。

    使用平台共享数据库连接池创建独立会话执行，避免占用请求级会话。
    """
    del kwargs
    if query_db is None:
        session_factory = AsyncSessionLocal
    else:
        bind = getattr(query_db, 'bind', None)
        if bind is None:
            raise RuntimeError('public-quota-reconciler 任务缺少可用的数据库绑定')
        session_factory = async_sessionmaker(bind=bind, expire_on_commit=True)
    owns_redis = redis is None
    redis_client = redis or await RedisUtil.create_redis_pool(log_enabled=False)
    quota = PublicQuotaEngine(redis_client)
    reconciler = PublicQuotaReconciler(
        session_factory=session_factory,
        quota=quota,
        dao=AiDeveloperApiDao,
    )
    try:
        return await reconciler.reconcile_once()
    except asyncio.CancelledError:
        raise
    finally:
        if owns_redis:
            await redis_client.aclose()
