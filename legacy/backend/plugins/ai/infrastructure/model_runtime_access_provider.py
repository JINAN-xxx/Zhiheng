from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from sqlalchemy import text

from config.database import AsyncSessionLocal
from plugins.ai.infrastructure.litellm_management_client import LiteLLMManagementClient
from plugins.ai.infrastructure.runtime_state import MANAGEMENT_CLIENT_STATE_NAME
from plugins.ai.service.runtime_model_access_service import RuntimeModelAccessService

# 同一个平台数据库中的发布请求共用事务级锁，避免跨进程更新白名单时相互覆盖。
RUNTIME_KEY_ACCESS_LOCK_ID = 0x4D4F44454C414343


@asynccontextmanager
async def runtime_key_access_lock() -> AsyncIterator[None]:
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL lock_timeout = '10s'"))
        await session.execute(text('SELECT pg_advisory_xact_lock(:lock_id)'), {'lock_id': RUNTIME_KEY_ACCESS_LOCK_ID})
        yield


def get_runtime_model_access_service(request: Request) -> RuntimeModelAccessService:
    client = getattr(request.app.state, MANAGEMENT_CLIENT_STATE_NAME, None)
    return RuntimeModelAccessService(
        client if isinstance(client, LiteLLMManagementClient) else None,
        access_lock=runtime_key_access_lock,
    )
