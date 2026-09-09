from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import AsyncSessionLocal
from plugins.ai.infrastructure.chat_knowledge_adapter import ChatKnowledgeAdapter
from plugins.ai.infrastructure.model_runtime_provider import get_model_runtime_engine
from plugins.ai.infrastructure.skill_context_adapter import SkillContextAdapter
from plugins.ai.service.chat_preferences_service import ChatPreferencesService
from plugins.ai.service.chat_runtime_coordinator import ChatRuntimeCoordinator
from plugins.ai.service.model_service import ModelService
from plugins.ai.service.platform_chat_service import PlatformChatService


def get_platform_chat_service(request: Request, db: AsyncSession) -> PlatformChatService:
    """组装当前 Worker 的 Platform Chat Service，不向 Controller 暴露共享客户端。"""
    return PlatformChatService(
        db,
        model_service=ModelService(
            db,
            get_model_runtime_engine(request),
            session_factory=AsyncSessionLocal,
        ),
        coordinator=ChatRuntimeCoordinator(getattr(request.app.state, 'redis', None)),
        preferences=ChatPreferencesService(db),
        knowledge_resolver=ChatKnowledgeAdapter(getattr(request.app.state, 'knowledge_runtime', None)),
        skill_resolver=SkillContextAdapter(
            AsyncSessionLocal,
            getattr(request.app.state, 'agent_skill_context_provider', None),
        ),
    )
