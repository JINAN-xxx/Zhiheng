from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from plugins.agent_extension.dao.agent_dao import AgentDao
from plugins.agent_extension.dao.skill_dao import SkillDao
from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.vo.agent_vo import (
    AgentConfigModel,
    AgentConfigResponseModel,
    AgentDetailModel,
    AgentPageQueryModel,
    AgentPublishedModel,
    AgentPublishModel,
    AgentUpdateModel,
)
from plugins.agent_extension.entity.vo.work_task_vo import WorkAgentOptionModel
from plugins.ai.public.application_queries import is_active_owned_application
from plugins.ai.public.model_queries import (
    PublishedChatModel,
    get_published_chat_model_for_update,
    lock_model_reference,
)


class AgentService:
    async def get_usable_published_agent(
        self, agent_id: str, *, actor_user_id: int, is_admin: bool, protect_reference: bool = True
    ) -> AgentPublishedModel:
        # Transaction ownership stays with the caller: work creation must remain atomic.
        agent = await self._dao.get_by_id(self._db, agent_id)
        if (
            agent is None
            or (not is_admin and agent.owner_user_id != actor_user_id)
            or agent.status != 'published'
            or not agent.published_config
            or not agent.published_version
        ):
            raise AgentServiceError('AGENT_NOT_FOUND', '智能体不可用或无权使用')
        config = AgentConfigResponseModel.model_validate(agent.published_config)
        if protect_reference and not await lock_model_reference(self._db, config.model_id):
            raise AgentServiceError('AGENT_MODEL_UNAVAILABLE', '智能体模型不可用')
        return AgentPublishedModel(
            **config.model_dump(),
            agent_id=agent.agent_id,
            owner_user_id=agent.owner_user_id,
            published_version=agent.published_version,
        )

    async def list_usable_published_agents(self, *, actor_user_id: int, is_admin: bool) -> list[WorkAgentOptionModel]:
        try:
            agents = await self._dao.list_usable_published(self._db, owner_user_id=None if is_admin else actor_user_id)
            return [
                WorkAgentOptionModel(
                    agent_id=agent.agent_id,
                    name=agent.published_config['name'],
                    description=agent.published_config.get('description', ''),
                    published_version=agent.published_version,
                    status='published',
                )
                for agent in agents
            ]
        finally:
            await self._rollback()

    def __init__(
        self,
        db: AsyncSession,
        *,
        dao: type[AgentDao] = AgentDao,
        model_query: Callable[
            [AsyncSession, str], Awaitable[PublishedChatModel | None]
        ] = get_published_chat_model_for_update,
        skill_dao: type[SkillDao] = SkillDao,
        application_query: Callable[[AsyncSession, str, int], Awaitable[bool]] = is_active_owned_application,
        max_selected_skills: int = 5,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._db = db
        self._dao = dao
        self._model_query = model_query
        self._skill_dao = skill_dao
        self._application_query = application_query
        self._max_selected_skills = max_selected_skills
        self._clock = clock

    async def list_agents(
        self,
        query: AgentPageQueryModel,
        *,
        actor_user_id: int,
        is_admin: bool,
        is_page: bool = True,
    ) -> PageModel | list[dict[str, Any]]:
        try:
            return await self._dao.list_agents(
                self._db,
                query,
                owner_user_id=None if is_admin else actor_user_id,
                is_page=is_page,
            )
        finally:
            await self._rollback()

    async def get_agent(self, agent_id: str, *, actor_user_id: int, is_admin: bool) -> AgentDetailModel:
        try:
            agent = await self._require_agent(agent_id, actor_user_id, is_admin)
            return AgentDetailModel.model_validate(agent)
        finally:
            await self._rollback()

    async def get_published_agent(self, agent_id: str, application_id: str) -> AgentPublishedModel:
        try:
            agent = await self._dao.get_by_id(self._db, agent_id)
            if agent is None or agent.status != 'published' or not agent.published_config:
                raise AgentServiceError('AGENT_NOT_FOUND', 'Agent not found')
            config = AgentConfigResponseModel.model_validate(agent.published_config)
            if config.application_id != application_id:
                raise AgentServiceError('AGENT_NOT_FOUND', 'Agent not found')
            return AgentPublishedModel(
                **config.model_dump(),
                agent_id=agent.agent_id,
                owner_user_id=agent.owner_user_id,
                published_version=agent.published_version,
            )
        finally:
            await self._rollback()

    async def create_agent(self, payload: AgentConfigModel, *, actor_user_id: int) -> AgentDetailModel:
        try:
            await self._validate_config(payload, owner_user_id=actor_user_id)
            now = self._clock()
            agent = AgentConfiguration(
                **self._config_values(payload),
                agent_id=str(uuid4()),
                owner_user_id=actor_user_id,
                version=1,
                status='draft',
                created_at=now,
                updated_at=now,
            )
            await self._dao.create_agent(self._db, agent)
            result = AgentDetailModel.model_validate(agent)
            await self._db.commit()
            return result
        except BaseException:
            await self._rollback()
            raise

    async def update_agent(
        self,
        agent_id: str,
        payload: AgentUpdateModel,
        *,
        actor_user_id: int,
        is_admin: bool,
    ) -> AgentDetailModel:
        try:
            agent = await self._lock_configuration_write(
                agent_id, actor_user_id, is_admin, payload.expected_version, model_id=payload.model_id
            )
            await self._validate_config(payload, owner_user_id=agent.owner_user_id)
            return await self._write(agent, payload.expected_version, self._config_values(payload))
        except BaseException:
            await self._rollback()
            raise

    async def publish_agent(
        self,
        agent_id: str,
        payload: AgentPublishModel,
        *,
        actor_user_id: int,
        is_admin: bool,
    ) -> AgentDetailModel:
        try:
            agent = await self._lock_configuration_write(agent_id, actor_user_id, is_admin, payload.expected_version)
            config = AgentConfigResponseModel.model_validate(agent)
            if config.application_id is None:
                raise AgentServiceError('AGENT_APPLICATION_REQUIRED', 'Bind an application before publishing')
            await self._validate_config(config, owner_user_id=agent.owner_user_id)
            return await self._write(
                agent,
                payload.expected_version,
                {
                    'status': 'published',
                    'published_config': config.model_dump(mode='json', by_alias=True),
                    'published_version': agent.version + 1,
                    'published_at': self._clock(),
                },
            )
        except BaseException:
            await self._rollback()
            raise

    async def unpublish_agent(
        self,
        agent_id: str,
        payload: AgentPublishModel,
        *,
        actor_user_id: int,
        is_admin: bool,
    ) -> AgentDetailModel:
        try:
            agent = await self._require_agent(agent_id, actor_user_id, is_admin, for_update=True)
            self._require_version(agent, payload.expected_version)
            return await self._write(
                agent,
                payload.expected_version,
                {
                    'status': 'draft',
                    'published_config': None,
                    'published_version': None,
                    'published_at': None,
                },
            )
        except BaseException:
            await self._rollback()
            raise

    async def delete_agent(self, agent_id: str, *, actor_user_id: int, is_admin: bool) -> None:
        try:
            agent = await self._require_agent(agent_id, actor_user_id, is_admin, for_update=True)
            if agent.status == 'published':
                raise AgentServiceError('AGENT_DELETE_REQUIRES_UNPUBLISH', 'Please unpublish the agent before deleting')
            if await self._dao.delete_draft(self._db, agent_id, expected_version=agent.version) != 1:
                raise AgentServiceError('AGENT_VERSION_CONFLICT', 'Agent changed; refresh and retry')
            await self._db.commit()
        except BaseException:
            await self._rollback()
            raise

    async def _validate_config(self, config: AgentConfigModel, *, owner_user_id: int) -> None:
        model = await self._model_query(self._db, config.model_id)
        if model is None:
            raise AgentServiceError('AGENT_MODEL_UNAVAILABLE', 'Select a published, active chat model')
        if model.max_output_tokens is not None and config.max_tokens > model.max_output_tokens:
            raise AgentServiceError('AGENT_MAX_TOKENS_EXCEEDED', 'Output token limit exceeds the selected model limit')
        if len(config.skill_slugs) > self._max_selected_skills:
            raise AgentServiceError('AGENT_SKILL_SELECTION_LIMIT', 'Too many selected skills')
        skills = await self._skill_dao.get_by_slugs(self._db, config.skill_slugs)
        if len(skills) != len(config.skill_slugs) or any(skill.status != 'enabled' for skill in skills):
            raise AgentServiceError('AGENT_SKILL_UNAVAILABLE', 'Selected skills must exist and be enabled')
        if config.application_id is not None and not await self._application_query(
            self._db, config.application_id, owner_user_id
        ):
            raise AgentServiceError(
                'AGENT_APPLICATION_UNAVAILABLE', 'Select an active application belonging to the agent owner'
            )

    async def _require_agent(
        self,
        agent_id: str,
        actor_user_id: int,
        is_admin: bool,
        *,
        for_update: bool = False,
    ) -> AgentConfiguration:
        agent = await self._dao.get_by_id(self._db, agent_id, for_update=for_update)
        if agent is None or (not is_admin and agent.owner_user_id != actor_user_id):
            raise AgentServiceError('AGENT_NOT_FOUND', 'Agent not found')
        return agent

    async def _lock_configuration_write(
        self, agent_id: str, actor_user_id: int, is_admin: bool, expected_version: int, *, model_id: str | None = None
    ) -> AgentConfiguration:
        # Check access first, then lock in the same model -> Agent order as deletion.
        preview = await self._require_agent(agent_id, actor_user_id, is_admin)
        self._require_version(preview, expected_version)
        target_model_id = model_id if model_id is not None else preview.model_id
        await lock_model_reference(self._db, target_model_id)
        agent = await self._require_agent(agent_id, actor_user_id, is_admin, for_update=True)
        self._require_version(agent, expected_version)
        if model_id is None and agent.model_id != target_model_id:
            raise AgentServiceError('AGENT_VERSION_CONFLICT', 'Agent changed; refresh and retry')
        return agent

    @staticmethod
    def _require_version(agent: AgentConfiguration, expected_version: int) -> None:
        if agent.version != expected_version:
            raise AgentServiceError('AGENT_VERSION_CONFLICT', 'Agent changed; refresh and retry')

    @staticmethod
    def _config_values(config: AgentConfigModel) -> dict[str, Any]:
        return config.model_dump(include=set(AgentConfigModel.model_fields))

    async def _write(
        self, agent: AgentConfiguration, expected_version: int, values: dict[str, Any]
    ) -> AgentDetailModel:
        changed = await self._dao.update_with_version(
            self._db,
            agent.agent_id,
            expected_version=expected_version,
            values={**values, 'updated_at': self._clock()},
        )
        if changed != 1:
            raise AgentServiceError('AGENT_VERSION_CONFLICT', 'Agent changed; refresh and retry')
        await self._db.refresh(agent)
        result = AgentDetailModel.model_validate(agent)
        await self._db.commit()
        return result

    async def _rollback(self) -> None:
        if self._db.in_transaction():
            await self._db.rollback()
