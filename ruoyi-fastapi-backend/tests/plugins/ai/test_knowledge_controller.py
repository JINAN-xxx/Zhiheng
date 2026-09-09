import json
from http import HTTPStatus
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException, Request

from plugins.ai.controller import ai_knowledge_controller as controller
from plugins.ai.knowledge.models import KnowledgeError


def test_every_knowledge_route_has_login_and_specific_operation_permission() -> None:
    router = controller.ai_knowledge_controller
    assert any('PreAuth' in repr(dependency.dependency) for dependency in router.dependencies)
    routes = {(method, route.path): route for route in router.routes for method in route.methods}
    assert ('GET', '/ai/knowledge/available') in routes
    assert ('POST', '/ai/knowledge/files/upload') in routes
    for route in router.routes:
        dependencies = [dependency.dependency for dependency in route.dependencies]
        assert any('UserInterfaceAuth' in repr(dependency) for dependency in dependencies), route.path
    available = routes['GET', '/ai/knowledge/available']
    assert any(
        getattr(dependency.dependency, 'perms', None) == 'ai:knowledge:query'
        or 'ai:knowledge:query' in repr(vars(dependency.dependency))
        for dependency in available.dependencies
    )
    assert not any('preview' in route.path and '{doc_id}' not in route.path for route in router.routes)


@pytest.mark.asyncio
async def test_knowledge_controller_uses_standard_envelope_and_sanitized_errors() -> None:
    async def successful() -> dict[str, Any]:
        return {'databases': [{'db_id': 'kb_demo'}]}

    response = await controller._call(successful())
    payload = json.loads(response.body)
    assert payload['code'] == HTTPStatus.OK
    assert payload['data']['databases'][0]['db_id'] == 'kb_demo'

    async def failing() -> None:
        raise RuntimeError('secret backend connection')

    with pytest.raises(HTTPException) as caught:
        await controller._call(failing())
    assert caught.value.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert 'secret' not in json.dumps(caught.value.detail)

    async def denied() -> None:
        raise KnowledgeError('KNOWLEDGE_FORBIDDEN', '没有权限', 403)

    with pytest.raises(HTTPException) as caught:
        await controller._call(denied())
    assert caught.value.status_code == HTTPStatus.FORBIDDEN


def test_missing_runtime_has_explicit_unavailable_response() -> None:
    request = Request({'type': 'http', 'app': SimpleNamespace(state=SimpleNamespace())})
    with pytest.raises(HTTPException) as caught:
        controller._runtime(request)
    assert caught.value.status_code == HTTPStatus.SERVICE_UNAVAILABLE
