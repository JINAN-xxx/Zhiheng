from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperKeyAuthenticator
from plugins.ai.infrastructure.developer_key_codec import DeveloperKeyCodec
from plugins.ai.infrastructure.model_runtime_provider import get_model_runtime_engine
from plugins.ai.service.api_entitlement_service import ApiEntitlementService
from plugins.ai.service.model_service import ModelService
from plugins.ai.service.openai_compat_service import OpenAICompatService
from plugins.ai.service.public_invocation_coordinator import (
    PublicInvocationCoordinator,
    PublicQuotaEngine,
)


@dataclass(frozen=True, slots=True)
class PublicApiServices:
    authenticator: DeveloperKeyAuthenticator
    entitlement: ApiEntitlementService
    coordinator: PublicInvocationCoordinator
    compat: OpenAICompatService


def _session_factory_factory(db: AsyncSession) -> Callable[[], AsyncSession]:
    return async_sessionmaker(bind=db.bind, expire_on_commit=True)


async def get_public_api_services(
    request: Request,
    db: AsyncSession,
    settings: LiteLLMSettings,
) -> PublicApiServices:
    """Build request-scoped public services without taking ownership of shared Runtime.

    注意：插件路由守卫与端点共享同一个 get_db 会话（FastAPI 依赖缓存），守卫的只读查询
    会使会话 autobegin；这里先回滚，恢复公开服务要求的"独占只读"前置条件。
    """
    if db.in_transaction():
        await db.rollback()
    codec = DeveloperKeyCodec(
        current_version=settings.api_key_pepper_current_version,
        peppers={'v1': settings.api_key_pepper_v1},
    )
    authenticator = DeveloperKeyAuthenticator(
        db,
        codec,
        configured=settings.developer_api_configured,
    )
    entitlement = ApiEntitlementService(db)
    model_service = ModelService(db, get_model_runtime_engine(request))
    redis = getattr(request.app.state, 'redis', None)
    session_factory = _session_factory_factory(db)
    quota = PublicQuotaEngine(redis)
    coordinator = PublicInvocationCoordinator(
        db,
        redis,
        session_factory=session_factory,
        quota=quota,
    )
    return PublicApiServices(
        authenticator=authenticator,
        entitlement=entitlement,
        coordinator=coordinator,
        compat=OpenAICompatService(coordinator, model_service),
    )
