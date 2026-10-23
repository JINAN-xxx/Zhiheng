import json
from collections.abc import Sequence
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from common.annotation.log_annotation import AuditedHTTPException
from plugins.agent_extension.controller import agent_invocation_controller as controller
from plugins.agent_extension.entity.vo.agent_invocation_vo import AgentInvocationRequestModel
from plugins.ai.domain.model_runtime import ModelChatResponse, ModelUsage

INPUT_TOKENS = 5
OUTPUT_TOKENS = 3


def preview_setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    skills: Sequence[str] = (),
) -> tuple[
    SimpleNamespace,
    SimpleNamespace,
    SimpleNamespace,
    SimpleNamespace,
    SimpleNamespace,
    Request,
    AgentInvocationRequestModel,
]:
    config = SimpleNamespace(
        agent_id='00000000-0000-4000-8000-000000000001',
        owner_user_id=7,
        model_id='mdl_00000000000040008000000000000001',
        system_prompt='Draft instructions',
        skill_slugs=list(skills),
        knowledge_base_ids=[],
        temperature=0.3,
        max_tokens=512,
    )
    agents = SimpleNamespace(get_agent=AsyncMock(return_value=config))
    runtime = SimpleNamespace(
        chat=AsyncMock(
            return_value=SimpleNamespace(
                response=ModelChatResponse(
                    text='Preview answer', usage=ModelUsage(input_tokens=INPUT_TOKENS, output_tokens=OUTPUT_TOKENS)
                ),
            )
        )
    )
    monkeypatch.setattr(controller, '_agent_service', lambda db: agents)
    monkeypatch.setattr(controller, '_model_code', AsyncMock(return_value='preview-model'))
    monkeypatch.setattr(controller, 'ModelService', lambda db, engine: runtime)
    monkeypatch.setattr(controller, 'get_model_runtime_engine', lambda request: object())
    session = SimpleNamespace(in_transaction=lambda: False, rollback=AsyncMock())
    user = SimpleNamespace(user=SimpleNamespace(user_id=7, user_name='tester', admin=False), permissions=[])
    request = Request({'type': 'http', 'app': FastAPI(), 'headers': []})
    payload = AgentInvocationRequestModel.model_validate({'messages': [{'role': 'user', 'content': 'Hello'}]})
    return config, agents, runtime, session, user, request, payload


@pytest.mark.asyncio
async def test_preview_uses_owned_saved_draft_without_an_api_application(monkeypatch: pytest.MonkeyPatch) -> None:
    config, agents, runtime, session, user, request, payload = preview_setup(monkeypatch)
    response = await controller.preview_agent(config.agent_id, payload, request, session, user)
    body = json.loads(response.body)
    assert body['data']['choices'][0]['message']['content'] == 'Preview answer'
    assert body['data']['usage']['total_tokens'] == INPUT_TOKENS + OUTPUT_TOKENS
    agents.get_agent.assert_awaited_once_with(config.agent_id, actor_user_id=7, is_admin=False)
    args, kwargs = runtime.chat.await_args
    assert args[0] == config.model_id
    assert args[1][0].role == 'system'
    assert args[1][0].content == 'Draft instructions'
    assert kwargs['parameters'] == {'temperature': 0.3, 'max_tokens': 512}


@pytest.mark.asyncio
async def test_preview_cannot_use_skills_without_use_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, runtime, session, user, request, payload = preview_setup(monkeypatch, skills=['summary'])
    with pytest.raises(AuditedHTTPException) as error:
        await controller.preview_agent(config.agent_id, payload, request, session, user)
    assert error.value.status_code == HTTPStatus.FORBIDDEN
    runtime.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_rejects_unsupported_stream_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    config, agents, runtime, session, user, request, payload = preview_setup(monkeypatch)
    payload.stream = True
    with pytest.raises(AuditedHTTPException) as error:
        await controller.preview_agent(config.agent_id, payload, request, session, user)
    assert error.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    agents.get_agent.assert_not_awaited()
    runtime.chat.assert_not_awaited()
