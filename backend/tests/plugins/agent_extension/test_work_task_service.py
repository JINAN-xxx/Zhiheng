import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.do.work_task_do import AgentWorkMessage, AgentWorkTask, AgentWorkTaskMember
from plugins.agent_extension.entity.vo.work_task_vo import (
    WorkTaskCreateModel,
    WorkTaskDetailModel,
    WorkTaskMemberAddModel,
    WorkTaskMemberUpdateModel,
    WorkTaskPageQueryModel,
    WorkTaskUpdateModel,
)
from plugins.agent_extension.service.agent_service import AgentService
from plugins.agent_extension.service.work_task_service import WorkTaskService
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from tests.plugins.agent_extension.test_agent_schema import MODEL_ID

WorkDatabase = tuple[async_sessionmaker[AsyncSession], str]
UPDATED_PUBLISHED_VERSION = 4
TASK_TITLE_LIMIT = 40


@pytest_asyncio.fixture
async def work_db(tmp_path: Path) -> AsyncIterator[WorkDatabase]:
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/work.db')
    async with engine.begin() as conn:
        # Keep concurrent sessions without readers blocking the stream writer's commit.
        await conn.exec_driver_sql('PRAGMA journal_mode=WAL')
        for table in (
            AiModelCatalog.__table__,
            AgentConfiguration.__table__,
            AgentWorkTask.__table__,
            AgentWorkTaskMember.__table__,
            AgentWorkMessage.__table__,
        ):
            await conn.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    agent_id = str(uuid4())
    config = {
        'name': 'Published',
        'description': 'Public description',
        'modelId': MODEL_ID,
        'systemPrompt': 'Frozen',
        'skillSlugs': [],
        'knowledgeBaseIds': [],
        'applicationId': None,
        'temperature': 0.7,
        'maxTokens': 100,
    }
    async with factory() as db:
        db.add(
            AiModelCatalog(
                model_id=MODEL_ID,
                model_code='chat',
                display_name='Chat',
                model_type='chat',
                lifecycle_status='active',
                published_at=datetime.now(),
            )
        )
        db.add(
            AgentConfiguration(
                agent_id=agent_id,
                owner_user_id=10,
                name='Draft',
                description='Draft secret',
                model_id=MODEL_ID,
                system_prompt='Draft secret',
                skill_slugs=[],
                knowledge_base_ids=[],
                temperature=0.7,
                max_tokens=100,
                version=3,
                status='published',
                published_config=config,
                published_version=2,
            )
        )
        await db.commit()
    yield factory, agent_id
    await engine.dispose()


def create_payload(agent_id: str, key: str = 'create', goal: str = 'Complete project') -> WorkTaskCreateModel:
    return WorkTaskCreateModel(goal=goal, agentIds=[agent_id], activeAgentId=agent_id, clientMessageId=key)


@pytest.mark.asyncio
async def test_create_persists_snapshots_and_replays_after_edit(work_db: WorkDatabase) -> None:
    factory, agent_id = work_db
    async with factory() as db:
        first = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10, is_admin=False)
    async with factory() as db:
        detail = await WorkTaskService(db).get_task(first.task_id, actor_user_id=10, is_admin=False)
        assert detail.members[0].agent_name_snapshot == 'Published'
        assert detail.messages[0].content == 'Complete project'
        await WorkTaskService(db).update_task(
            first.task_id, WorkTaskUpdateModel(expectedVersion=1, goal='Changed'), actor_user_id=10, is_admin=False
        )
        replay = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10, is_admin=False)
        assert replay.task_id == first.task_id
        with pytest.raises(AgentServiceError) as conflict:
            await WorkTaskService(db).create_task(
                create_payload(agent_id, goal='Other'), actor_user_id=10, is_admin=False
            )
        assert conflict.value.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_owner_isolation_and_archived_read_only(work_db: WorkDatabase) -> None:
    factory, agent_id = work_db
    async with factory() as db:
        service = WorkTaskService(db)
        first = await service.create_task(create_payload(agent_id), actor_user_id=10, is_admin=False)
        with pytest.raises(AgentServiceError) as missing:
            await service.get_task(first.task_id, actor_user_id=11, is_admin=False)
        assert missing.value.status_code == HTTPStatus.NOT_FOUND
        archived = await service.update_task(
            first.task_id, WorkTaskUpdateModel(expectedVersion=1, status='archived'), actor_user_id=10, is_admin=False
        )
        with pytest.raises(AgentServiceError):
            await service.update_task(
                first.task_id,
                WorkTaskUpdateModel(expectedVersion=archived.version, title='Other'),
                actor_user_id=10,
                is_admin=False,
            )


@pytest.mark.asyncio
async def test_concurrent_create_same_key_writes_one_task(work_db: WorkDatabase) -> None:
    factory, agent_id = work_db

    async def create() -> WorkTaskDetailModel:
        async with factory() as db:
            return await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)

    results = await asyncio.gather(create(), create())
    assert results[0].task_id == results[1].task_id
    async with factory() as db:
        page = await WorkTaskService(db).list_tasks(WorkTaskPageQueryModel(), actor_user_id=10)
        assert page['total'] == 1


@pytest.mark.asyncio
async def test_directory_hides_drafts_secrets_and_other_owners(work_db: WorkDatabase) -> None:
    factory, _agent_id = work_db
    async with factory() as db:
        service = AgentService(db)
        options = await service.list_usable_published_agents(actor_user_id=10, is_admin=False)
        assert options[0].name == 'Published'
        assert options[0].description == 'Public description'
        assert set(options[0].model_dump()) == {'agent_id', 'name', 'description', 'published_version', 'status'}
        assert await service.list_usable_published_agents(actor_user_id=11, is_admin=False) == []
        assert len(await service.list_usable_published_agents(actor_user_id=1, is_admin=True)) == 1


@pytest.mark.asyncio
async def test_member_version_confirmation_freezes_then_rejoins(work_db: WorkDatabase) -> None:
    factory, agent_id = work_db
    async with factory() as db:
        service = WorkTaskService(db)
        first = await service.create_task(create_payload(agent_id), actor_user_id=10)
        agent = await db.get(AgentConfiguration, agent_id)
        agent.published_config = {**agent.published_config, 'name': 'New published', 'systemPrompt': 'New frozen'}
        agent.published_version = UPDATED_PUBLISHED_VERSION
        await db.commit()
        old = await service.get_task(first.task_id, actor_user_id=10)
        assert old.members[0].agent_name_snapshot == 'Published'
        assert old.members[0].latest_published_version == UPDATED_PUBLISHED_VERSION
        with pytest.raises(AgentServiceError):
            await service.update_member(
                first.task_id,
                agent_id,
                WorkTaskMemberUpdateModel(expectedVersion=1, expectedPublishedVersion=3),
                actor_user_id=10,
            )
        updated = await service.update_member(
            first.task_id,
            agent_id,
            WorkTaskMemberUpdateModel(expectedVersion=1, expectedPublishedVersion=UPDATED_PUBLISHED_VERSION),
            actor_user_id=10,
        )
        assert updated.members[0].agent_name_snapshot == 'New published'
        removed = await service.remove_member(first.task_id, agent_id, updated.version, actor_user_id=10)
        assert removed.members == [] and removed.active_agent_id is None
        rejoined = await service.add_member(
            first.task_id, WorkTaskMemberAddModel(expectedVersion=removed.version, agentId=agent_id), actor_user_id=10
        )
        assert len(rejoined.members) == 1 and rejoined.active_agent_id == agent_id
        assert len(rejoined.messages) == 1


@pytest.mark.asyncio
async def test_list_status_admin_delete_and_stale_version(work_db: WorkDatabase) -> None:
    factory, agent_id = work_db
    async with factory() as db:
        service = WorkTaskService(db)
        task = await service.create_task(create_payload(agent_id, goal='Long title ' * 10), actor_user_id=10)
        assert len(task.title) == TASK_TITLE_LIMIT
        completed = await service.update_task(
            task.task_id, WorkTaskUpdateModel(expectedVersion=1, status='completed'), actor_user_id=10
        )
        for payload in (
            WorkTaskUpdateModel(expectedVersion=1, status='active'),
            WorkTaskUpdateModel(expectedVersion=completed.version, goal='Changed'),
        ):
            with pytest.raises(AgentServiceError):
                await service.update_task(task.task_id, payload, actor_user_id=10)
        with pytest.raises(AgentServiceError):
            await service.remove_member(task.task_id, agent_id, completed.version, actor_user_id=10)
        reopened = await service.update_task(
            task.task_id, WorkTaskUpdateModel(expectedVersion=completed.version, status='active'), actor_user_id=10
        )
        await service.update_task(
            task.task_id, WorkTaskUpdateModel(expectedVersion=reopened.version, status='archived'), actor_user_id=10
        )
        assert (await service.list_tasks(WorkTaskPageQueryModel(), actor_user_id=10))['total'] == 0
        assert (await service.list_tasks(WorkTaskPageQueryModel(status='archived'), actor_user_id=1, is_admin=True))[
            'total'
        ] == 1
        await service.delete_task(task.task_id, actor_user_id=1, is_admin=True)
        with pytest.raises(AgentServiceError):
            await service.get_task(task.task_id, actor_user_id=10)
