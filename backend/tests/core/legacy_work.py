"""Explicit pre-upgrade fixtures for accepted member-based Work invariants.

These helpers call the retained application/domain services to seed historical
facts. They do not restore retired production HTTP creation or execution routes.
Read, history, delete and cancellation requests still use the real public API.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, ValidationError

from tests.core.integration.test_agents import config
from zhiheng.modules.work.public import AddMember, CreateTask, UpdateTask, UpgradeMember, WorkTurn
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability


async def published(admin: httpx.AsyncClient, model_id: str, **kwargs: Any) -> dict[str, Any]:
    created = await admin.post(
        '/api/developer/applications', json={'code': 'work-' + uuid4().hex, 'name': 'Work publication'}
    )
    assert created.status_code == 201, created.text
    application = created.json()['id']
    agent = (await admin.post('/api/agents', json=config(model_id, application_id=application, **kwargs))).json()
    response = await admin.post(f'/api/agents/{agent["id"]}/enable', json={'expected_version': 1})
    assert response.status_code == 200, response.text
    return response.json()


@asynccontextmanager
async def member_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    from zhiheng.api.common.dependencies import COOKIE_NAME

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
    result = await client.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
    assert result.status_code == 200 and COOKIE_NAME in client.cookies
    try:
        yield client
    finally:
        await client.aclose()


async def legacy_task(admin: httpx.AsyncClient, agent: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    body = {
        'goal': '共同目标',
        'agent_ids': [agent['id']],
        'active_agent_id': agent['id'],
        'client_request_id': str(uuid4()),
    }
    body.update(kwargs)
    response = await legacy_work_request(admin, 'POST', '/api/work/tasks', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def legacy_turn(task: dict[str, Any], agent: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return dict(
        agent_id=agent['id'],
        message=task['goal'],
        client_request_id=task['create_client_request_id'],
        expected_version=task['version'],
        **kwargs,
    )


def _response(
    client: httpx.AsyncClient, method: str, url: str, result: BaseModel | dict[str, object], status: int
) -> httpx.Response:
    return httpx.Response(
        status,
        json=result.model_dump(mode='json') if isinstance(result, BaseModel) else result,
        request=httpx.Request(method, client.base_url.join(url)),
    )


async def legacy_work_request(  # noqa: PLR0912, PLR0915 - keep pre-upgrade fixture authorization and transactions together
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """Seed pre-upgrade mutations through common services; keep all current HTTP reads/halts real."""
    path = urlsplit(str(url)).path
    pieces = path.strip('/').split('/')
    method = method.upper()
    is_work = pieces[:3] == ['api', 'work', 'tasks']
    creation = is_work and len(pieces) == 3 and method == 'POST'
    admission = is_work and len(pieces) == 5 and pieces[4] == 'runs' and method == 'POST'
    membership = is_work and len(pieces) in {5, 6} and pieces[4] == 'members' and method in {'POST', 'PATCH', 'DELETE'}
    updating = is_work and len(pieces) == 4 and method == 'PATCH'
    if not (creation or admission or membership or updating):
        return await client.request(method, url, **kwargs)
    identity = await client.get('/api/auth/me')
    if identity.status_code != 200:
        return identity
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise AssertionError('Legacy fixtures require an explicit local ASGI application')
    app = transport.app
    container = app.state.container
    body = kwargs.get('json')
    try:
        if body is None:
            body = json.loads(kwargs.get('content', '{}'))
        async with container.database.sessions.begin() as session:
            principal = await container.identity.principal_for_user(session, UUID(identity.json()['id']))
            require_capability(principal, 'work', 'create' if creation else 'use' if admission else 'update')
            if creation:
                result = await container.work_runs.create(session, principal, CreateTask.model_validate(body))
                return _response(client, method, url, result, 201)
            task_id = UUID(pieces[3])
            if updating:
                result = await container.work_runs.update(session, principal, task_id, UpdateTask.model_validate(body))
                return _response(client, method, url, result, 200)
            if membership:
                if method == 'POST':
                    member = AddMember.model_validate(body)
                    result = await container.work_runs.change_member(
                        session, principal, task_id, member.agent_id, member.expected_version
                    )
                elif method == 'PATCH':
                    member_update = UpgradeMember.model_validate(body)
                    result = await container.work_runs.change_member(
                        session,
                        principal,
                        task_id,
                        UUID(pieces[5]),
                        member_update.expected_version,
                        member_update.expected_agent_revision,
                    )
                else:
                    params = dict(kwargs.get('params', {}))
                    value = str(
                        params.get(
                            'expected_version', parse_qs(urlsplit(str(url)).query).get('expected_version', [''])[0]
                        )
                    )
                    if not value.isdecimal() or value.startswith('0'):
                        raise ValueError('Invalid expected version')
                    await container.work.require(session, principal, task_id, lock=True)
                    await container.work_runs.ensure_preparation_idle(session, task_id)
                    await container.work.change_member(session, principal, task_id, int(value), UUID(pieces[5]), None)
                    result = await container.work_runs.detail(session, principal, task_id)
                return _response(client, method, url, result, 200)
            turn = WorkTurn.model_validate(body)
            existing = await container.work_runs.replay(session, principal, task_id, turn)
            if existing is not None:
                return _response(client, method, url, existing, 202)
            knowledge = await container.work_runs.requires_knowledge(session, principal, task_id, turn.agent_id)
        if knowledge:
            prepared = await container.work_runs.prepare_knowledge_work(principal, task_id, turn)
            return _response(client, method, url, prepared, 202)
        async with container.database.sessions.begin() as session:
            principal = await container.identity.principal_for_user(session, UUID(identity.json()['id']))
            require_capability(principal, 'work', 'use')
            admitted = await container.work_runs.admit(session, principal, task_id, turn)
        return _response(client, method, url, admitted, 202)
    except AppError as error:
        details: dict[str, object] = {'code': error.code, 'message': error.message}
        if error.details is not None:
            details['details'] = error.details
        return _response(client, method, url, {'error': details}, error.status_code)
    except (ValidationError, ValueError, TypeError):
        return _response(client, method, url, {'error': {'code': 'invalid_request', 'message': '请求字段无效'}}, 422)
