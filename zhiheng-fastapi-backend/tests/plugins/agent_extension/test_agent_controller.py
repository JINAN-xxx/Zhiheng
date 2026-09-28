# ruff: noqa: PLR2004
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.routing import APIRoute

from common.annotation.log_annotation import AuditedHTTPException
from module_admin.entity.vo.user_vo import CurrentUserModel, UserInfoModel
from plugins.agent_extension.controller.agent_controller import _actor, _require_skill_use, agent_controller
from plugins.agent_extension.entity.vo.agent_vo import AgentConfigModel, AgentPublishModel, AgentUpdateModel
from tests.plugins.agent_extension.test_agent_schema import config_payload
from tests.plugins.agent_extension.test_skill_controller import dependency_calls


def test_agent_management_routes_have_login_and_exact_permissions() -> None:
    expected = {
        ('GET', '/agent_extension/agents'): 'list',
        ('POST', '/agent_extension/agents'): 'add',
        ('GET', '/agent_extension/agents/{agent_id}'): 'query',
        ('PUT', '/agent_extension/agents/{agent_id}'): 'edit',
        ('DELETE', '/agent_extension/agents/{agent_id}'): 'remove',
        ('POST', '/agent_extension/agents/{agent_id}/publish'): 'publish',
        ('POST', '/agent_extension/agents/{agent_id}/unpublish'): 'publish',
    }
    routes = {
        (next(iter(route.methods)), route.path): route
        for route in agent_controller.routes
        if isinstance(route, APIRoute)
    }
    assert set(routes) == set(expected)
    for key, permission in expected.items():
        calls = dependency_calls(routes[key])
        assert any(type(call).__name__ == 'PreAuth' for call in calls)
        assert [call.perm for call in calls if type(call).__name__ == 'CheckUserInterfaceAuth'] == [
            f'agent_extension:agent:{permission}'
        ]


def test_actor_uses_authenticated_user_identity_and_admin_marker() -> None:
    assert _actor(CurrentUserModel(user=UserInfoModel(userId=10, userName='owner'), permissions=[], roles=[])) == (
        10,
        False,
    )
    assert _actor(CurrentUserModel(user=UserInfoModel(userId=1, userName='admin'), permissions=[], roles=[])) == (
        1,
        True,
    )
    with pytest.raises(AuditedHTTPException) as missing:
        _actor(CurrentUserModel(user=None, permissions=[], roles=[]))
    assert missing.value.status_code == 401


def test_selected_skills_require_skill_use_permission() -> None:
    user = CurrentUserModel(user=UserInfoModel(userId=10), permissions=[], roles=[])
    _require_skill_use(user, [])
    with pytest.raises(AuditedHTTPException) as forbidden:
        _require_skill_use(user, ['research'])
    assert forbidden.value.status_code == 403
    _require_skill_use(user.model_copy(update={'permissions': ['agent_extension:skill:use']}), ['research'])
    _require_skill_use(user.model_copy(update={'permissions': ['*:*:*']}), ['research'])


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['create', 'update', 'publish'])
async def test_mutating_routes_enforce_skill_use_before_writing(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    module = import_module('plugins.agent_extension.controller.agent_controller')
    service = SimpleNamespace(
        create_agent=AsyncMock(),
        update_agent=AsyncMock(),
        publish_agent=AsyncMock(),
        get_agent=AsyncMock(return_value=SimpleNamespace(skill_slugs=['research'])),
    )
    monkeypatch.setattr(module, '_agent_service', lambda _: service)
    user = CurrentUserModel(user=UserInfoModel(userId=10, userName='owner'), permissions=[], roles=[])
    with pytest.raises(AuditedHTTPException) as forbidden:
        if operation == 'create':
            await module.create_agent(AgentConfigModel(**config_payload(skillSlugs=['research'])), None, user)
        elif operation == 'update':
            await module.update_agent(
                'agent-id', AgentUpdateModel(**config_payload(skillSlugs=['research'], expectedVersion=1)), None, user
            )
        else:
            await module.publish_agent('agent-id', AgentPublishModel(expectedVersion=1), None, user)
    assert forbidden.value.status_code == 403
    service.create_agent.assert_not_awaited()
    service.update_agent.assert_not_awaited()
    service.publish_agent.assert_not_awaited()
