# ruff: noqa: PLR2004
from collections.abc import AsyncIterator
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plugins.agent_extension.dao.agent_dao import AgentDao
from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.do.skill_do import AgentSkill
from plugins.agent_extension.entity.vo.agent_vo import (
    AgentConfigModel,
    AgentPageQueryModel,
    AgentPublishModel,
    AgentUpdateModel,
)
from plugins.agent_extension.service.agent_service import AgentService
from plugins.ai.dao.ai_model_catalog_dao import AiModelCatalogDao
from plugins.ai.entity.do.ai_developer_api_do import AiApplication
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.public.application_queries import is_active_owned_application
from tests.plugins.agent_extension.test_agent_schema import APPLICATION_ID, MODEL_ID, config_payload


@pytest_asyncio.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    maker = async_sessionmaker(engine, expire_on_commit=True)
    async with engine.begin() as connection:
        for table in [
            AgentConfiguration.__table__,
            AiModelCatalog.__table__,
            AiApplication.__table__,
            AgentSkill.__table__,
        ]:
            await connection.run_sync(table.create)
    async with maker() as db:
        db.add(
            AiModelCatalog(
                model_id=MODEL_ID,
                model_code='chat',
                display_name='Chat',
                model_type='chat',
                lifecycle_status='active',
                published_at=datetime.now(),
                max_output_tokens=8192,
            )
        )
        db.add(
            AiApplication(
                application_id=APPLICATION_ID,
                application_code='agent-app',
                application_name='Agent App',
                owner_user_id=10,
            )
        )
        await db.commit()
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_welcome_message_persists_across_sessions_and_can_be_updated_or_cleared(
    database: async_sessionmaker[AsyncSession],
) -> None:
    greeting = '  欢迎使用！\n请描述你的问题。  '
    async with database() as db:
        created = await AgentService(db).create_agent(
            AgentConfigModel(**config_payload(welcomeMessage=greeting)), actor_user_id=10
        )
        assert created.welcome_message == greeting
    async with database() as db:
        service = AgentService(db)
        reloaded = await service.get_agent(created.agent_id, actor_user_id=10, is_admin=False)
        assert reloaded.welcome_message == greeting
        assert reloaded.model_dump(by_alias=True)['welcomeMessage'] == greeting
        updated = await service.update_agent(
            created.agent_id,
            AgentUpdateModel(**config_payload(welcomeMessage='新的欢迎语', expectedVersion=reloaded.version)),
            actor_user_id=10,
            is_admin=False,
        )
        assert updated.welcome_message == '新的欢迎语'
    async with database() as db:
        service = AgentService(db)
        reloaded = await service.get_agent(created.agent_id, actor_user_id=10, is_admin=False)
        assert reloaded.welcome_message == '新的欢迎语'
        cleared = await service.update_agent(
            created.agent_id,
            AgentUpdateModel(**config_payload(welcomeMessage='', expectedVersion=reloaded.version)),
            actor_user_id=10,
            is_admin=False,
        )
        assert cleared.welcome_message == ''
    async with database() as db:
        reloaded = await AgentService(db).get_agent(created.agent_id, actor_user_id=10, is_admin=False)
        assert reloaded.welcome_message == ''


@pytest.mark.asyncio
async def test_published_welcome_message_stays_frozen_until_republication(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as db:
        service = AgentService(db)
        agent = await service.create_agent(
            AgentConfigModel(**config_payload(welcomeMessage='发布的欢迎语')), actor_user_id=10
        )
        published = await service.publish_agent(
            agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
        )
        assert published.published_config.welcome_message == '发布的欢迎语'
        edited = await service.update_agent(
            agent.agent_id,
            AgentUpdateModel(**config_payload(welcomeMessage='草稿欢迎语', expectedVersion=published.version)),
            actor_user_id=10,
            is_admin=False,
        )
        assert edited.welcome_message == '草稿欢迎语'
        assert edited.published_config.welcome_message == '发布的欢迎语'
    async with database() as db:
        service = AgentService(db)
        snapshot = await service.get_published_agent(agent.agent_id, APPLICATION_ID)
        assert snapshot.welcome_message == '发布的欢迎语'
        refreshed = await service.publish_agent(
            agent.agent_id, AgentPublishModel(expectedVersion=edited.version), actor_user_id=10, is_admin=False
        )
        assert refreshed.published_config.welcome_message == '草稿欢迎语'
        assert (await service.get_published_agent(agent.agent_id, APPLICATION_ID)).welcome_message == '草稿欢迎语'


@pytest.mark.asyncio
async def test_legacy_published_snapshot_has_empty_welcome_message_despite_new_draft_greeting(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as db:
        service = AgentService(db)
        agent = await service.create_agent(
            AgentConfigModel(**config_payload(welcomeMessage='新的草稿欢迎语')), actor_user_id=10
        )
        legacy_config = config_payload()
        assert 'welcomeMessage' not in legacy_config
        await db.execute(
            update(AgentConfiguration)
            .where(AgentConfiguration.agent_id == agent.agent_id)
            .values(status='published', published_config=legacy_config, published_version=1)
        )
        await db.commit()
    async with database() as db:
        service = AgentService(db)
        detail = await service.get_agent(agent.agent_id, actor_user_id=10, is_admin=False)
        assert detail.welcome_message == '新的草稿欢迎语'
        assert detail.published_config.welcome_message == ''
        assert detail.model_dump(by_alias=True)['publishedConfig']['welcomeMessage'] == ''
        snapshot = await service.get_published_agent(agent.agent_id, APPLICATION_ID)
        assert snapshot.welcome_message == ''
        assert snapshot.model_dump(by_alias=True)['welcomeMessage'] == ''


@pytest.mark.asyncio
async def test_published_snapshot_survives_draft_edits_and_unpublish_rejects_calls(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as db:
        service = AgentService(db)
        created = await service.create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        assert (created.version, created.status, created.published_config) == (1, 'draft', None)
        published = await service.publish_agent(
            created.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
        )
        assert published.status == 'published'
        assert published.published_version == published.version == 2
        assert published.published_config.system_prompt == 'Be precise.'
        edited = await service.update_agent(
            created.agent_id,
            AgentUpdateModel(**config_payload(systemPrompt='New draft', expectedVersion=published.version)),
            actor_user_id=10,
            is_admin=False,
        )
        assert edited.version == published.version + 1
        assert edited.system_prompt == 'New draft'
        assert edited.published_config.system_prompt == 'Be precise.'
        snapshot = await service.get_published_agent(created.agent_id, APPLICATION_ID)
        assert snapshot.system_prompt == 'Be precise.'
        assert snapshot.owner_user_id == 10
        assert not db.in_transaction()
        with pytest.raises(AgentServiceError, match='unpublish'):
            await service.delete_agent(created.agent_id, actor_user_id=10, is_admin=False)
        refreshed = await service.publish_agent(
            created.agent_id, AgentPublishModel(expectedVersion=edited.version), actor_user_id=10, is_admin=False
        )
        assert refreshed.published_config.system_prompt == 'New draft'
        assert refreshed.published_version == refreshed.version
        unpublished = await service.unpublish_agent(
            created.agent_id, AgentPublishModel(expectedVersion=refreshed.version), actor_user_id=10, is_admin=False
        )
        assert unpublished.status == 'draft'
        with pytest.raises(AgentServiceError) as missing:
            await service.get_published_agent(created.agent_id, APPLICATION_ID)
        assert missing.value.code == 'AGENT_NOT_FOUND'
        await service.delete_agent(created.agent_id, actor_user_id=10, is_admin=False)
        assert (
            await service.list_agents(AgentPageQueryModel(), actor_user_id=10, is_admin=False, is_page=True)
        ).total == 0


@pytest.mark.asyncio
async def test_owner_isolation_admin_access_and_application_mismatch(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as db:
        service = AgentService(db)
        agent = await service.create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        assert (
            await service.list_agents(AgentPageQueryModel(), actor_user_id=11, is_admin=False, is_page=True)
        ).total == 0
        assert (
            await service.list_agents(AgentPageQueryModel(), actor_user_id=11, is_admin=True, is_page=True)
        ).total == 1
        operations = [
            service.get_agent(agent.agent_id, actor_user_id=11, is_admin=False),
            service.update_agent(
                agent.agent_id, AgentUpdateModel(**config_payload(expectedVersion=1)), actor_user_id=11, is_admin=False
            ),
            service.publish_agent(
                agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=11, is_admin=False
            ),
            service.unpublish_agent(
                agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=11, is_admin=False
            ),
            service.delete_agent(agent.agent_id, actor_user_id=11, is_admin=False),
        ]
        for operation in operations:
            with pytest.raises(AgentServiceError) as missing:
                await operation
            assert missing.value.code == 'AGENT_NOT_FOUND'
        assert (await service.get_agent(agent.agent_id, actor_user_id=11, is_admin=True)).owner_user_id == 10
        await service.publish_agent(
            agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=11, is_admin=True
        )
        with pytest.raises(AgentServiceError) as mismatch:
            await service.get_published_agent(agent.agent_id, 'another-app')
        assert mismatch.value.code == 'AGENT_NOT_FOUND'


@pytest.mark.asyncio
async def test_stale_update_and_publish_do_not_overwrite_saved_draft(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as db:
        service = AgentService(db)
        agent = await service.create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        await service.update_agent(
            agent.agent_id,
            AgentUpdateModel(**config_payload(name='Saved', expectedVersion=1)),
            actor_user_id=10,
            is_admin=False,
        )
        for operation in [
            service.update_agent(
                agent.agent_id,
                AgentUpdateModel(**config_payload(name='Stale', expectedVersion=1)),
                actor_user_id=10,
                is_admin=False,
            ),
            service.publish_agent(
                agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
            ),
        ]:
            with pytest.raises(AgentServiceError) as conflict:
                await operation
            assert conflict.value.code == 'AGENT_VERSION_CONFLICT'
        detail = await service.get_agent(agent.agent_id, actor_user_id=10, is_admin=False)
        assert (detail.name, detail.version, detail.published_config) == ('Saved', 2, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'change',
    [
        {'lifecycle_status': 'disabled'},
        {'published_at': None},
        {'model_type': 'embedding'},
    ],
)
async def test_configuration_requires_a_published_active_chat_model(
    database: async_sessionmaker[AsyncSession], change: dict[str, object]
) -> None:
    async with database() as db:
        await db.execute(update(AiModelCatalog).values(**change))
        await db.commit()
        with pytest.raises(AgentServiceError) as invalid:
            await AgentService(db).create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        assert invalid.value.code == 'AGENT_MODEL_UNAVAILABLE'


@pytest.mark.asyncio
async def test_publish_revalidates_model_and_skill_selection(database: async_sessionmaker[AsyncSession]) -> None:
    async with database() as db:
        service = AgentService(db)
        agent = await service.create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        await db.execute(update(AiModelCatalog).values(lifecycle_status='disabled'))
        await db.commit()
        with pytest.raises(AgentServiceError) as unavailable:
            await service.publish_agent(
                agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
            )
        assert unavailable.value.code == 'AGENT_MODEL_UNAVAILABLE'
        await db.execute(update(AiModelCatalog).values(lifecycle_status='active'))
        await db.commit()
        with pytest.raises(AgentServiceError) as missing:
            await service.update_agent(
                agent.agent_id,
                AgentUpdateModel(**config_payload(skillSlugs=['missing'], expectedVersion=1)),
                actor_user_id=10,
                is_admin=False,
            )
        assert missing.value.code == 'AGENT_SKILL_UNAVAILABLE'
        db.add(
            AgentSkill(
                skill_id='skl_123',
                skill_slug='disabled',
                skill_name='Disabled',
                storage_path='disabled',
                content_hash='a' * 64,
                status='disabled',
            )
        )
        await db.commit()
        with pytest.raises(AgentServiceError) as disabled:
            await service.create_agent(AgentConfigModel(**config_payload(skillSlugs=['disabled'])), actor_user_id=10)
        assert disabled.value.code == 'AGENT_SKILL_UNAVAILABLE'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'owner_user_id': 11}, {'status': 'disabled'}, {'deleted_at': datetime.now()}])
async def test_binding_must_remain_active_and_belong_to_agent_owner_even_for_admin(
    database: async_sessionmaker[AsyncSession], change: dict[str, object]
) -> None:
    async with database() as db:
        service = AgentService(db)
        agent = await service.create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        await db.execute(update(AiApplication).values(**change))
        await db.commit()
        with pytest.raises(AgentServiceError) as invalid:
            await service.publish_agent(
                agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=11, is_admin=True
            )
        assert invalid.value.code == 'AGENT_APPLICATION_UNAVAILABLE'


@pytest.mark.asyncio
async def test_admin_updates_and_publishes_using_original_owner_in_public_application_query(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as db:
        agent = await AgentService(db).create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        query = AsyncMock(wraps=is_active_owned_application)
        service = AgentService(db, application_query=query)
        updated = await service.update_agent(
            agent.agent_id, AgentUpdateModel(**config_payload(expectedVersion=1)), actor_user_id=11, is_admin=True
        )
        query.assert_awaited_once_with(db, APPLICATION_ID, 10)
        assert not db.in_transaction()
        query.reset_mock()
        published = await service.publish_agent(
            agent.agent_id, AgentPublishModel(expectedVersion=updated.version), actor_user_id=11, is_admin=True
        )
        query.assert_awaited_once_with(db, APPLICATION_ID, 10)
        assert published.owner_user_id == 10
        assert published.status == 'published'
        assert not db.in_transaction()


@pytest.mark.asyncio
async def test_unavailable_application_query_keeps_error_and_caller_rollback(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as db:
        query = AsyncMock(return_value=False)
        with pytest.raises(AgentServiceError) as invalid:
            await AgentService(db, application_query=query).create_agent(
                AgentConfigModel(**config_payload()), actor_user_id=10
            )
        query.assert_awaited_once_with(db, APPLICATION_ID, 10)
        assert invalid.value.public_dict() == {
            'code': 'AGENT_APPLICATION_UNAVAILABLE',
            'message': 'Select an active application belonging to the agent owner',
        }
        assert invalid.value.status_code == 422
        assert not db.in_transaction()
        assert (await AgentService(db).list_agents(AgentPageQueryModel(), actor_user_id=10, is_admin=False)).total == 0


@pytest.mark.asyncio
async def test_unbound_draft_skips_public_application_query_and_cannot_publish(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as db:
        query = AsyncMock(return_value=False)
        service = AgentService(db, application_query=query)
        agent = await service.create_agent(AgentConfigModel(**config_payload(applicationId=None)), actor_user_id=10)
        assert agent.application_id is None
        with pytest.raises(AgentServiceError) as invalid:
            await service.publish_agent(
                agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
            )
        assert invalid.value.code == 'AGENT_APPLICATION_REQUIRED'
        query.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['update', 'publish'])
async def test_configuration_writes_lock_model_before_agent_row(
    database: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    async with database() as db:
        agent = await AgentService(db).create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        events = []
        original_agent = AgentDao.get_by_id
        original_model = AiModelCatalogDao.get_model_for_update

        async def agent_query(session: AsyncSession, agent_id: str, *, for_update: bool = False) -> AgentConfiguration:
            if for_update:
                events.append('agent')
            return await original_agent(session, agent_id, for_update=for_update)

        async def model_query(session: AsyncSession, model_id: str) -> AiModelCatalog:
            events.append('model')
            return await original_model(session, model_id)

        monkeypatch.setattr(AgentDao, 'get_by_id', agent_query)
        monkeypatch.setattr(AiModelCatalogDao, 'get_model_for_update', model_query)
        service = AgentService(db)
        if operation == 'update':
            await service.update_agent(
                agent.agent_id, AgentUpdateModel(**config_payload(expectedVersion=1)), actor_user_id=10, is_admin=False
            )
        else:
            await service.publish_agent(
                agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
            )
        assert events.index('model') < events.index('agent')


@pytest.mark.asyncio
async def test_knowledge_bindings_can_publish_without_a_provider_and_unbound_agents_can_preview(
    database: async_sessionmaker[AsyncSession],
) -> None:
    async with database() as db:
        service = AgentService(db)
        agent = await service.create_agent(
            AgentConfigModel(**config_payload(knowledgeBaseIds=['kb-1'])), actor_user_id=10
        )
        published = await service.publish_agent(
            agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
        )
        assert published.published_config.knowledge_base_ids == ['kb-1']
        unbound = await service.create_agent(AgentConfigModel(**config_payload(applicationId=None)), actor_user_id=10)
        assert unbound.application_id is None
        with pytest.raises(AgentServiceError) as invalid:
            await service.publish_agent(
                unbound.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
            )
        assert invalid.value.code == 'AGENT_APPLICATION_REQUIRED'


@pytest.mark.asyncio
async def test_stale_unpublish_cannot_remove_a_new_publication(database: async_sessionmaker[AsyncSession]) -> None:
    async with database() as db:
        service = AgentService(db)
        agent = await service.create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        published = await service.publish_agent(
            agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
        )
        republished = await service.publish_agent(
            agent.agent_id, AgentPublishModel(expectedVersion=published.version), actor_user_id=10, is_admin=False
        )
        with pytest.raises(AgentServiceError) as stale:
            await service.unpublish_agent(
                agent.agent_id, AgentPublishModel(expectedVersion=published.version), actor_user_id=10, is_admin=False
            )
        assert stale.value.code == 'AGENT_VERSION_CONFLICT'
        assert (
            await service.get_published_agent(agent.agent_id, APPLICATION_ID)
        ).published_version == republished.version


@pytest.mark.asyncio
async def test_application_binding_stays_frozen_until_republication(database: async_sessionmaker[AsyncSession]) -> None:
    other_application = 'app_' + uuid4().hex
    async with database() as db:
        db.add(
            AiApplication(
                application_id=other_application, application_code='second', application_name='Second', owner_user_id=10
            )
        )
        await db.commit()
        service = AgentService(db)
        agent = await service.create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        published = await service.publish_agent(
            agent.agent_id, AgentPublishModel(expectedVersion=1), actor_user_id=10, is_admin=False
        )
        await service.update_agent(
            agent.agent_id,
            AgentUpdateModel(**config_payload(applicationId=other_application, expectedVersion=published.version)),
            actor_user_id=10,
            is_admin=False,
        )
        assert (await service.get_published_agent(agent.agent_id, APPLICATION_ID)).application_id == APPLICATION_ID
        with pytest.raises(AgentServiceError) as mismatch:
            await service.get_published_agent(agent.agent_id, other_application)
        assert mismatch.value.status_code == 404


@pytest.mark.asyncio
async def test_conditional_write_conflict_rolls_back_and_leaves_existing_values(
    database: async_sessionmaker[AsyncSession],
) -> None:
    class ConflictingDao(AgentDao):
        @classmethod
        async def update_with_version(
            cls, db: AsyncSession, agent_id: str, *, expected_version: int, values: dict[str, object]
        ) -> int:
            return 0

    async with database() as db:
        service = AgentService(db)
        agent = await service.create_agent(AgentConfigModel(**config_payload()), actor_user_id=10)
        with pytest.raises(AgentServiceError) as stale:
            await AgentService(db, dao=ConflictingDao).update_agent(
                agent.agent_id,
                AgentUpdateModel(**config_payload(name='Lost', expectedVersion=1)),
                actor_user_id=10,
                is_admin=False,
            )
        assert stale.value.code == 'AGENT_VERSION_CONFLICT'
        assert not db.in_transaction()
        assert (await service.get_agent(agent.agent_id, actor_user_id=10, is_admin=False)).name == 'Research agent'


@pytest.mark.asyncio
async def test_selection_count_and_model_output_limit_are_enforced(database: async_sessionmaker[AsyncSession]) -> None:
    async with database() as db:
        service = AgentService(db, max_selected_skills=1)
        with pytest.raises(AgentServiceError) as limit:
            await service.create_agent(
                AgentConfigModel(**config_payload(skillSlugs=['first', 'second'])), actor_user_id=10
            )
        assert limit.value.code == 'AGENT_SKILL_SELECTION_LIMIT'
        with pytest.raises(AgentServiceError) as tokens:
            await service.create_agent(AgentConfigModel(**config_payload(maxTokens=8193)), actor_user_id=10)
        assert tokens.value.code == 'AGENT_MAX_TOKENS_EXCEEDED'
