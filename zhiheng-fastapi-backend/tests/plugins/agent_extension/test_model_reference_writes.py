from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.vo.work_task_vo import WorkTaskCreateModel
from plugins.agent_extension.service import agent_service, work_task_service
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.public.model_queries import lock_model_reference
from tests.plugins.agent_extension.test_work_task_service import work_db as work_db  # noqa: PLC0414 - shared fixture

OWNER = 10


@pytest.mark.asyncio
async def test_multi_agent_creation_locks_models_in_stable_order(
    work_db: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, original_agent = work_db
    second_agent = str(uuid4())
    second_model = 'mdl_' + uuid4().hex
    async with factory() as db:
        first = await db.get(AgentConfiguration, original_agent)
        assert first is not None
        first_model = first.model_id
        db.add(
            AiModelCatalog(
                model_id=second_model,
                model_code='other',
                display_name='Other',
                model_type='chat',
                lifecycle_status='active',
                published_at=datetime.now(),
            )
        )
        db.add(
            AgentConfiguration(
                agent_id=second_agent,
                owner_user_id=OWNER,
                name='Second',
                model_id=second_model,
                status='published',
                published_version=1,
                published_config={**first.published_config, 'modelId': second_model, 'name': 'Second'},
            )
        )
        await db.commit()
        locked = []

        async def observe(session: AsyncSession, model_id: str) -> bool:
            locked.append(model_id)
            return await lock_model_reference(session, model_id)

        monkeypatch.setattr(agent_service, 'lock_model_reference', observe)
        monkeypatch.setattr(work_task_service, 'lock_model_reference', observe, raising=False)
        agents = [
            agent for _, agent in sorted([(first_model, original_agent), (second_model, second_agent)], reverse=True)
        ]
        result = await work_task_service.WorkTaskService(db).create_task(
            WorkTaskCreateModel(goal='Goal', agentIds=agents, activeAgentId=agents[0], clientMessageId='ordered'),
            actor_user_id=OWNER,
        )
        assert result.task_id
        assert locked == sorted([first_model, second_model])
