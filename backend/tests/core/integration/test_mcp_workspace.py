"""Exercise the console and workspace worker against a local MCP endpoint."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_platform_module_permissions import grant, member
from tests.core.integration.test_work_agent_execution import consume, workspace_model
from tests.core.integration.test_work_agent_execution import workspace_provider as workspace_provider  # noqa: PLC0414
from tests.core.integration.test_workspace_search import create_task
from tests.core.permission_support import account, grants
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.modules.identity.admin_orm import IdentityRole

TOKEN = 'fixture-mcp-secret'
QUERY = {'query': '办公制度'}


class McpHandler(BaseHTTPRequestHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        pass

    def do_DELETE(self) -> None:
        assert self.headers['MCP-Session-Id'] == 'sample-session'
        self.send_response(204)
        self.end_headers()

    def do_POST(self) -> None:
        assert self.headers['Authorization'] == f'Bearer {TOKEN}'
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        name = request['method']
        if name != 'initialize':
            assert self.headers['MCP-Session-Id'] == 'sample-session'
            assert self.headers['MCP-Protocol-Version'] == '2025-06-18'
        if name == 'notifications/initialized':
            self.send_response(202)
            self.end_headers()
            return
        if name == 'initialize':
            result = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}}}
        elif name == 'tools/list':
            result = {
                'tools': [
                    {
                        'name': 'policy_lookup',
                        'description': self.server.description,
                        'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}},
                        'annotations': {'readOnlyHint': True},
                    }
                ]
            }
        else:
            assert name == 'tools/call'
            self.server.calls.append(request['params'])
            result = {'content': [{'type': 'text', 'text': f'制度查询结果 {TOKEN}'}]}
        body = json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}).encode()
        self.send_response(200)
        if name == 'initialize':
            self.send_header('MCP-Session-Id', 'sample-session')
        sse = name == 'tools/call'
        self.send_header('Content-Type', 'text/event-stream' if sse else 'application/json')
        self.end_headers()
        self.wfile.write(b'data: ' + body + b'\n\n' if sse else body)


class TestServer(ThreadingHTTPServer):
    __test__ = False
    daemon_threads = True
    description = '查询已公开的制度'

    def __init__(self) -> None:
        super().__init__(('127.0.0.1', 0), McpHandler)
        self.calls: list[dict[str, object]] = []


@pytest.fixture
def mcp_endpoint() -> Iterator[TestServer]:
    server = TestServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def provider_reply(body: dict[str, object]) -> dict[str, object]:
    tools = body.get('tools', [])
    if not tools:
        return {'role': 'assistant', 'content': json.loads(body['messages'][-1]['content'])['candidate']}
    assert any(tool['function']['name'] == 'query_mcp' for tool in tools)
    messages = body['messages']
    user_index = max(index for index, item in enumerate(messages) if item['role'] == 'user')
    returned = [item for item in messages[user_index + 1 :] if item['role'] == 'tool']
    if not returned:
        return WorkspaceProvider.tool('query_mcp', {}, 'mcp-once')
    result = json.loads(returned[-1]['content'])
    if result['success'] is False:
        return {'role': 'assistant', 'content': result['error']['message']}
    assert '制度查询结果' in returned[-1]['content'] and TOKEN not in returned[-1]['content']
    return {'role': 'assistant', 'content': '已根据制度查询结果完成。'}


async def register_endpoint(admin: httpx.AsyncClient, app: FastAPI, mcp_endpoint: TestServer) -> tuple[str, str]:
    endpoint = f'http://127.0.0.1:{mcp_endpoint.server_port}/mcp'
    registered = {'name': '制度服务', 'endpoint': endpoint}
    response = await admin.post('/api/mcp/servers', json=registered)
    assert response.status_code == 403, response.text
    mcp = app.state.container.mcp
    mcp.transport.allowed_endpoints = frozenset({endpoint})
    response = await admin.post('/api/mcp/servers', json={**registered, 'token': TOKEN})
    assert response.status_code == 201, response.text
    server = response.json()
    path = f'/api/mcp/servers/{server["id"]}'
    assert server['has_token'] and TOKEN not in response.text
    response = await admin.post(path + '/sync', json={'expected_version': server['version']})
    assert response.status_code == 200, response.text
    server = response.json()
    assert server['tools'][0]['enabled'] is False and mcp_endpoint.calls == []
    response = await admin.get('/api/mcp/roles')
    assert response.status_code == 200, response.text
    role_ids = [role['id'] for role in response.json()]
    assert role_ids
    response = await admin.put(
        path + '/tools/policy_lookup',
        json={'enabled': True, 'role_ids': role_ids, 'expected_version': server['version']},
    )
    assert response.status_code == 200, response.text
    response = await admin.patch(
        path, json={**registered, 'enabled': True, 'expected_version': response.json()['version']}
    )
    assert response.status_code == 200, response.text
    return path, server['id']


async def test_mcp_console_works_with_no_servers_configured(admin: httpx.AsyncClient, app: FastAPI) -> None:
    assert app.state.container.mcp.transport.allowed_endpoints == frozenset()
    for path in ('/api/mcp/tools', '/api/mcp/servers'):
        response = await admin.get(path)
        assert response.status_code == 200, response.text
        assert response.json() == []


@pytest.mark.parametrize('operation', ['configure', 'grant', 'audit'])
async def test_mcp_management_roles_have_independent_rights(
    admin: httpx.AsyncClient, app: FastAPI, mcp_endpoint: TestServer, operation: str
) -> None:
    path, server_id = await register_endpoint(admin, app, mcp_endpoint)
    endpoint = f'http://127.0.0.1:{mcp_endpoint.server_port}/mcp'
    async with account(admin, app, *grants('platform:mcp', operation)) as (client, role, _):
        response = await client.get('/api/mcp/servers')
        assert response.status_code == 200, response.text
        server = response.json()[0]
        assert server['id'] == server_id
        assert server['endpoint'] == (endpoint if operation == 'configure' else None)
        assert server['has_token'] == (True if operation == 'configure' else None)
        assert TOKEN not in response.text
        assert (await client.get('/api/mcp/tools')).json() == []
        if operation == 'configure':
            assert server['tools'][0]['role_ids'] == []
        elif operation == 'grant':
            assert server['tools'][0]['role_ids']
        else:
            assert server['tools'] == []
        assert (await client.get(path + '/calls')).status_code == (200 if operation == 'audit' else 403)
        assert (await client.get('/api/mcp/roles')).status_code == (200 if operation == 'grant' else 403)
        response = await client.post(path + '/sync', json={'expected_version': server['version']})
        assert response.status_code == (200 if operation == 'configure' else 403), response.text
        if operation == 'configure':
            server = response.json()
            assert server['tools'][0]['role_ids'] == []
        response = await client.put(
            path + '/tools/policy_lookup',
            json={'enabled': True, 'role_ids': [role['id']], 'expected_version': server['version']},
        )
        assert response.status_code == (200 if operation == 'grant' else 403), response.text
        if operation == 'grant':
            server = response.json()
            assert server['endpoint'] is None and server['has_token'] is None
            assert server['tools'][0]['role_ids'] == [role['id']]
        response = await client.patch(
            path,
            json={'name': '制度服务', 'endpoint': endpoint, 'enabled': True, 'expected_version': server['version']},
        )
        assert response.status_code == (200 if operation == 'configure' else 403), response.text
        response = await client.post('/api/mcp/servers', json={'name': '另一个服务', 'endpoint': endpoint})
        assert response.status_code == (201 if operation == 'configure' else 403), response.text


async def test_authorized_workspace_uses_only_confirmed_mcp_parameters(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    mcp_endpoint: TestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await grant(app, *grants('platform:work', 'use', 'create'), *grants('platform:mcp'))
    use_role = await grant(app, *grants('platform:mcp', 'use'))
    path, server_id = await register_endpoint(admin, app, mcp_endpoint)
    model = await workspace_model(admin, workspace_provider, 'workspace-mcp')
    monkeypatch.setattr(workspace_provider, 'response', provider_reply)
    async with member(app) as client:
        assert (await client.get('/api/mcp/servers')).status_code == 403
        tools = (await client.get('/api/mcp/tools')).json()
        assert [tool['name'] for tool in tools] == ['policy_lookup']
        task = await create_task(client, model)
        run_path = f'/api/work/tasks/{task["id"]}/runs'
        selection = {
            'server_id': server_id,
            'name': 'policy_lookup',
            'version': tools[0]['version'],
            'arguments': QUERY,
        }
        admitted = await client.post(
            run_path,
            json={
                'client_request_id': str(uuid4()),
                'expected_version': task['version'],
                'message': '本轮还包含不应发送到 MCP 的工作区信息',
                'mcp_tool': selection,
            },
        )
        assert admitted.status_code == 202, admitted.text
        await consume(app)
        final = (await client.get(run_path + '/' + admitted.json()['id'])).json()
        assert final['status'] == 'completed', final
        assert '制度查询结果' in final['text']
        assert mcp_endpoint.calls == [{'name': 'policy_lookup', 'arguments': QUERY}]
        assert TOKEN not in json.dumps(workspace_provider.requests)
        mcp_endpoint.description = '远端已变更，待重新审核'
        detail = (await client.get(f'/api/work/tasks/{task["id"]}')).json()
        changed = await client.post(
            run_path,
            json={
                'client_request_id': str(uuid4()),
                'expected_version': detail['version'],
                'message': '继续查询',
                'mcp_tool': selection,
            },
        )
        assert changed.status_code == 202, changed.text
        await consume(app)
        final = (await client.get(run_path + '/' + changed.json()['id'])).json()
        assert '工具定义已变化' in final['text']
        assert len(mcp_endpoint.calls) == 1
        async with app.state.container.database.sessions.begin() as session:
            role = await session.get(IdentityRole, use_role)
            assert role is not None
            role.is_active = False
        assert (await client.get('/api/mcp/tools')).json() == []
        detail = (await client.get(f'/api/work/tasks/{task["id"]}')).json()
        denied = await client.post(
            run_path,
            json={
                'client_request_id': str(uuid4()),
                'expected_version': detail['version'],
                'message': '再次查询',
                'mcp_tool': selection,
            },
        )
        assert denied.status_code == 403, denied.text
    log = (await admin.get(path + '/calls')).json()
    assert {row['status'] for row in log} == {'succeeded', 'failed'}
    assert 'arguments' not in log[0] and 'result' not in log[0]
