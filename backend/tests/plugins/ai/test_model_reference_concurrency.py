"""Optional real PostgreSQL verification; every test owns a disposable schema."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from exceptions.exception import ServiceException
from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.do.work_task_do import AgentWorkTask, AgentWorkTaskMember
from plugins.agent_extension.entity.vo.agent_vo import AgentPublishModel, AgentUpdateModel
from plugins.agent_extension.service.agent_service import AgentService
from plugins.ai.entity.do.ai_developer_api_do import AiApplication
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.infrastructure.model_reference_provider import ModelDeletionReferences
from plugins.ai.knowledge.metadata import MetadataStore
from plugins.ai.knowledge.model_references import model_reference_write
from plugins.ai.public.model_queries import lock_model_reference
from plugins.ai.service.model_catalog_service import ModelCatalogService
from plugins.core.management.entity.do.models import SysPlugin
from tests.plugins.agent_extension.test_agent_schema import config_payload
from tests.plugins.ai.test_model_deletion_references import BINDING, MODEL, REFERENCE_TABLES, agent_registry

POSTGRES_DSN = os.getenv('ZHIHENG_TEST_POSTGRES_DSN')
pytestmark = pytest.mark.skipif(
    not POSTGRES_DSN, reason='Set ZHIHENG_TEST_POSTGRES_DSN to a disposable PostgreSQL database'
)


@pytest_asyncio.fixture
async def postgres_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    schema = 'test_model_refs_' + uuid4().hex
    control = create_async_engine(POSTGRES_DSN)
    async with control.begin() as connection:
        await connection.execute(CreateSchema(schema))
    engine = create_async_engine(POSTGRES_DSN, connect_args={'server_settings': {'search_path': schema}})
    try:
        async with engine.begin() as connection:
            for model in REFERENCE_TABLES:
                await connection.run_sync(model.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            db.add(
                SysPlugin(
                    plugin_id='agent_extension',
                    plugin_name='Agent',
                    version='0.4.0',
                    installed_version='0.4.0',
                    enabled='1',
                    status='installed',
                )
            )
            db.add(
                AiModelCatalog(
                    model_id=MODEL,
                    model_code='model',
                    display_name='Model',
                    model_type='chat',
                    lifecycle_status='disabled',
                )
            )
            await db.flush()
            db.add(
                AiResourceBinding(
                    binding_id=BINDING,
                    resource_type='model',
                    resource_id=MODEL,
                    engine_type='litellm',
                    engine_resource_id='runtime',
                    binding_status='active',
                )
            )
            await db.commit()
        yield factory
    finally:
        await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await control.dispose()


async def wait_for_database_lock(factory: async_sessionmaker[AsyncSession], pid: int) -> None:
    async def observe() -> None:
        while True:
            async with factory() as db:
                waiting = await db.scalar(
                    text("select wait_event_type = 'Lock' from pg_stat_activity where pid = :pid"), {'pid': pid}
                )
            if waiting:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(observe(), 5)


@pytest.mark.asyncio
async def test_reference_writer_commits_before_delete_checks_snapshots(
    postgres_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    async with postgres_db() as writer, postgres_db() as deletion:
        assert await lock_model_reference(writer, MODEL)
        writer.add(
            AgentWorkTask(task_id='task', owner_user_id=1, create_client_message_id='create', title='Task', goal='Goal')
        )
        await writer.flush()
        writer.add(
            AgentWorkTaskMember(
                task_id='task',
                agent_id='agent',
                agent_name_snapshot='Agent',
                published_version=1,
                config_snapshot={'modelId': MODEL},
            )
        )
        await writer.flush()
        pid = await deletion.scalar(text('select pg_backend_pid()'))
        service = ModelCatalogService(
            deletion,
            None,
            ModelDeletionReferences(deletion, registry=agent_registry(enabled=False), metadata=MetadataStore(tmp_path)),
        )
        pending = asyncio.create_task(service.delete_model(MODEL))
        await wait_for_database_lock(postgres_db, pid)
        await writer.commit()
        with pytest.raises(ServiceException) as caught:
            await asyncio.wait_for(pending, 5)
        assert '被业务数据引用' in caught.value.message
    async with postgres_db() as db:
        assert await db.get(AiModelCatalog, MODEL) is not None
        assert await db.get(AiResourceBinding, BINDING) is not None


@pytest.mark.asyncio
async def test_delete_holds_model_and_metadata_guard_until_commit(
    postgres_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed = asyncio.Event()
    release_commit = asyncio.Event()
    validated = asyncio.Event()

    class KnowledgeWriter:
        metadata = MetadataStore(tmp_path)

        @model_reference_write
        async def write(self) -> bool:
            validated.set()
            async with postgres_db() as db:
                if await db.get(AiModelCatalog, MODEL) is None:
                    return False
            async with self.metadata.transaction() as state:
                state['databases']['kb'] = {'embed_model_id': MODEL}
            return True

    async with postgres_db() as deletion, postgres_db() as writer:
        original_commit = deletion.commit

        async def hold_commit() -> None:
            committed.set()
            await release_commit.wait()
            await original_commit()

        monkeypatch.setattr(deletion, 'commit', hold_commit)
        service = ModelCatalogService(
            deletion,
            None,
            ModelDeletionReferences(deletion, registry=agent_registry(), metadata=MetadataStore(tmp_path)),
        )
        deleting = asyncio.create_task(service.delete_model(MODEL))
        await asyncio.wait_for(committed.wait(), 5)
        knowledge = asyncio.create_task(KnowledgeWriter().write())
        pid = await writer.scalar(text('select pg_backend_pid()'))
        model_exists = asyncio.create_task(lock_model_reference(writer, MODEL))
        await wait_for_database_lock(postgres_db, pid)
        assert not validated.is_set()
        release_commit.set()
        await asyncio.wait_for(deleting, 5)
        assert not await asyncio.wait_for(model_exists, 5)
        assert not await asyncio.wait_for(knowledge, 5)
        await writer.rollback()
    assert (await MetadataStore(tmp_path).read())['databases'] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['update', 'publish'])
async def test_agent_write_and_delete_use_one_lock_order_without_database_deadlock(
    postgres_db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    operation: str,
) -> None:
    agent_id = str(uuid4())
    application_id = 'app_' + uuid4().hex
    owner = 10
    async with postgres_db() as db:
        db.add(
            AiApplication(
                application_id=application_id, application_code='app', application_name='App', owner_user_id=owner
            )
        )
        db.add(
            AgentConfiguration(
                agent_id=agent_id, owner_user_id=owner, name='Agent', model_id=MODEL, application_id=application_id
            )
        )
        await db.commit()
    async with postgres_db() as deletion, postgres_db() as writer:
        assert await lock_model_reference(deletion, MODEL)
        pid = await writer.scalar(text('select pg_backend_pid()'))
        service = AgentService(writer)
        if operation == 'update':
            writing = asyncio.create_task(
                service.update_agent(
                    agent_id,
                    AgentUpdateModel(**config_payload(modelId=MODEL, applicationId=application_id, expectedVersion=1)),
                    actor_user_id=owner,
                    is_admin=False,
                )
            )
        else:
            writing = asyncio.create_task(
                service.publish_agent(
                    agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=owner, is_admin=False
                )
            )
        await wait_for_database_lock(postgres_db, pid)
        catalogue = ModelCatalogService(
            deletion,
            None,
            ModelDeletionReferences(deletion, registry=agent_registry(), metadata=MetadataStore(tmp_path)),
        )
        outcomes = await asyncio.wait_for(
            asyncio.gather(catalogue.delete_model(MODEL), writing, return_exceptions=True), 5
        )
        assert isinstance(outcomes[0], ServiceException)
        assert '被业务数据引用' in outcomes[0].message
        assert isinstance(outcomes[1], AgentServiceError)
        assert outcomes[1].code == 'AGENT_MODEL_UNAVAILABLE'
