import json

import pytest
from fastapi.routing import APIRoute

from common.annotation.log_annotation import AuditedHTTPException
from module_admin.entity.vo.user_vo import CurrentUserModel, UserInfoModel
from plugins.agent_extension.controller import work_task_controller as controller
from plugins.agent_extension.entity.vo.work_task_vo import WorkTaskPageQueryModel
from tests.plugins.agent_extension.test_skill_controller import dependency_calls
from tests.plugins.agent_extension.test_work_task_service import create_payload, work_db


def test_work_routes_require_login_and_exact_permissions():
    routes = [route for route in controller.work_task_controller.routes if isinstance(route, APIRoute)]
    assert len(routes) == 11
    for route in routes:
        calls = dependency_calls(route)
        assert any(type(call).__name__ == 'PreAuth' for call in calls)
        permissions = [call for call in calls if type(call).__name__ == 'CheckUserInterfaceAuth']
        assert permissions
        if route.path.endswith('/runs') or ('POST' in route.methods and route.path.endswith('/work-tasks')):
            assert permissions[0].is_strict
            assert 'agent_extension:agent:use' in permissions[0].perm
    assert next(i for i, route in enumerate(routes) if route.path.endswith('/available-agents')) < next(
        i for i, route in enumerate(routes) if route.path.endswith('/{task_id}')
    )


@pytest.mark.asyncio
async def test_controller_uses_real_service_and_hides_foreign_tasks(work_db):
    factory, agent_id = work_db
    user = CurrentUserModel(user=UserInfoModel(userId=10, userName='owner'), permissions=[], roles=[])
    async with factory() as db:
        response = await controller.create_task(create_payload(agent_id), db, user)
        task = json.loads(response.body)['data']
        assert task['members'][0]['agentNameSnapshot'] == 'Published'
        assert 'configSnapshot' not in task['members'][0]
        listed = json.loads((await controller.list_tasks(WorkTaskPageQueryModel(), db, user)).body)
        assert listed['total'] == 1
        assert listed['pageSize'] == 20
        other = CurrentUserModel(user=UserInfoModel(userId=11, userName='other'), permissions=[], roles=[])
        with pytest.raises(AuditedHTTPException) as missing:
            await controller.get_task(task['taskId'], db, other)
        assert missing.value.status_code == 404
