from fastapi import Request

from config.database import AsyncSessionLocal
from plugins.agent_extension.service.work_task_run_service import WorkTaskRunService
from plugins.ai.infrastructure.model_runtime_provider import get_model_runtime_engine
from plugins.ai.infrastructure.skill_context_adapter import SkillContextAdapter


def get_work_task_run_service(request: Request) -> WorkTaskRunService:
    factory = getattr(request.app.state, 'agent_work_session_factory', AsyncSessionLocal)
    return WorkTaskRunService(
        factory,
        model_runtime=get_model_runtime_engine(request),
        skill_resolver=SkillContextAdapter(factory, getattr(request.app.state, 'agent_skill_context_provider', None)),
        knowledge_provider=getattr(request.app.state, 'agent_knowledge_provider', None),
    )
