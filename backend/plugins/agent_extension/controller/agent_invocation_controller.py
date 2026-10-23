import asyncio
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import Path, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.annotation.log_annotation import AuditedHTTPException
from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.router import APIRouterPro
from module_admin.entity.vo.user_vo import CurrentUserModel
from plugins.agent_extension.controller.agent_controller import _actor, _require_skill_use
from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.agent_extension.domain.agent_knowledge import KnowledgeRetrievalError
from plugins.agent_extension.entity.vo.agent_invocation_vo import AgentInvocationRequestModel
from plugins.agent_extension.entity.vo.agent_vo import AgentDetailModel, AgentId, AgentPublishedModel
from plugins.agent_extension.service.agent_invocation_service import prepare_agent_call
from plugins.agent_extension.service.agent_service import AgentService
from plugins.ai.config import LiteLLMSettings
from plugins.ai.domain.model_runtime import ModelMessage
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.entity.vo.ai_openai_compat_vo import OpenAIChatCompletionRequestModel, OpenAIChatCompletionResponseModel
from plugins.ai.infrastructure.model_runtime_provider import get_model_runtime_engine
from plugins.ai.infrastructure.public_api_provider import get_public_api_services
from plugins.ai.infrastructure.skill_context_adapter import SkillContextAdapter
from plugins.ai.public import api_transport as public_transport
from plugins.ai.public.model_queries import get_published_chat_model
from plugins.ai.service.model_service import ModelService
from plugins.ai.service.platform_chat_service import PlatformChatError
from utils.response_util import ResponseUtil

agent_public_controller = APIRouterPro(prefix='/v1/agents', order_num=27, tags=['智能体调用'])
agent_preview_controller = APIRouterPro(
    prefix='/agent_extension/agents',
    order_num=26,
    tags=['智能体扩展-试聊'],
    dependencies=[PreAuthDependency(), UserInterfaceAuthDependency('agent_extension:agent:test')],
)
_settings_factory = LiteLLMSettings
_public_api_services = get_public_api_services
_agent_service = AgentService


async def _model_code(db: AsyncSession, model_id: str) -> str:
    model = await get_published_chat_model(db, model_id)
    if model is None:
        raise AgentServiceError('AGENT_MODEL_UNAVAILABLE', '智能体模型不可用', status_code=422)
    return str(model.model_code)


async def _prepare(
    request: Request,
    db: AsyncSession,
    config: AgentDetailModel | AgentPublishedModel,
    payload: AgentInvocationRequestModel,
    application_id: str | None,
    *,
    model_code: str | None = None,
) -> OpenAIChatCompletionRequestModel:
    if model_code is None:
        model_code = await _model_code(db, config.model_id)
    if db.in_transaction():
        await db.rollback()
    skills = None
    if config.skill_slugs:
        skills = SkillContextAdapter(
            async_sessionmaker(bind=db.bind),
            getattr(request.app.state, 'agent_skill_context_provider', None),
        )
    return await prepare_agent_call(
        config,
        payload,
        agent_id=config.agent_id,
        owner_user_id=config.owner_user_id,
        application_id=application_id,
        model_code=model_code,
        skill_resolver=skills,
        knowledge_provider=getattr(request.app.state, 'agent_knowledge_provider', None),
    )


def _agent_error_response(
    error: AgentServiceError | KnowledgeRetrievalError | PlatformChatError | ModelServiceError,
) -> JSONResponse:
    detail = error.public_dict()
    return JSONResponse(
        status_code=getattr(error, 'status_code', 503),
        content={'error': {'code': detail['code'], 'message': detail['message'], 'type': 'agent_error', 'param': None}},
    )


@agent_public_controller.post('/{agent_id}/chat/completions')
async def invoke_published_agent(
    agent_id: Annotated[AgentId, Path()],
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    try:
        settings = _settings_factory()
        if not settings.public_api_enabled:
            raise PublicApiError.feature_disabled()
        services = await _public_api_services(request, query_db, settings)
        identity = await public_transport.authenticate_request(request, services.authenticator)
        body = await public_transport.read_json_request(
            request, settings.api_max_request_bytes, AgentInvocationRequestModel
        )
        if body.cancelled:
            raise asyncio.CancelledError
        if body.value is None:
            raise PublicApiError.invalid_request()
        config = await _agent_service(query_db).get_published_agent(agent_id, application_id=identity.application_id)
        model_code = await _model_code(query_db, config.model_id)
        if query_db.in_transaction():
            await query_db.rollback()
        await services.coordinator.resolve_invocation(identity, model_code, 'chat')
        prepared = await _prepare(request, query_db, config, body.value, identity.application_id, model_code=model_code)
        # The existing public service remains responsible for model grants, quota and usage settlement.
        if prepared.stream:
            stream = await services.compat.stream(identity, prepared)
            return public_transport.streaming_response(stream)
        result = await services.compat.complete(identity, prepared)
        return JSONResponse(result.model_dump(mode='json'))
    except (AgentServiceError, KnowledgeRetrievalError, PlatformChatError, ModelServiceError) as error:
        return _agent_error_response(error)
    except PublicApiError as error:
        return public_transport.error_response(error)
    except Exception:
        error = PublicApiError.service_unavailable()
        return public_transport.error_response(error)


@agent_preview_controller.post('/{agent_id}/preview')
async def preview_agent(
    agent_id: Annotated[AgentId, Path()],
    payload: AgentInvocationRequestModel,
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    try:
        if payload.stream:
            raise AgentServiceError('AGENT_PREVIEW_INVALID', '试聊暂不支持流式请求', status_code=422)
        user_id, is_admin = _actor(current_user)
        config = await _agent_service(query_db).get_agent(agent_id, actor_user_id=user_id, is_admin=is_admin)
        _require_skill_use(current_user, config.skill_slugs)
        prepared = await _prepare(request, query_db, config, payload, None)
        model_service = ModelService(query_db, get_model_runtime_engine(request))
        result = await model_service.chat(
            config.model_id,
            [ModelMessage(role=message.role, content=message.content) for message in prepared.messages],
            parameters={'temperature': prepared.temperature, 'max_tokens': prepared.max_tokens},
        )
        usage = result.response.usage
        response = OpenAIChatCompletionResponseModel.model_validate(
            {
                'id': f'chatcmpl_{uuid4().hex}',
                'created': int(datetime.now(timezone.utc).timestamp()),
                'model': prepared.model,
                'choices': [
                    {
                        'index': 0,
                        'message': {'role': 'assistant', 'content': result.response.text},
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {
                    'prompt_tokens': usage.input_tokens,
                    'completion_tokens': usage.output_tokens,
                    'total_tokens': usage.input_tokens + usage.output_tokens,
                },
            }
        )
        return ResponseUtil.success(data=response)
    except (AgentServiceError, KnowledgeRetrievalError, PlatformChatError, ModelServiceError) as error:
        detail = error.public_dict()
        raise AuditedHTTPException(
            status_code=getattr(error, 'status_code', 503),
            detail=detail,
            audit_code=detail['code'],
            audit_message=detail['message'],
        ) from None
