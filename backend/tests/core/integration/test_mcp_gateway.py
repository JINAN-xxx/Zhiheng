"""Exercise MCP publication, discovery, delegation and audit against a real local upstream."""

from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_mcp_workspace import QUERY, TOKEN, TestServer, register_endpoint, running_mcp_endpoint
from tests.core.integration.test_mcp_workspace import mcp_endpoint as mcp_endpoint  # noqa: PLC0414
from zhiheng.modules.mcp.orm import McpServer


@pytest.fixture
def other_endpoint() -> Iterator[TestServer]:
    with running_mcp_endpoint() as server:
        yield server


async def test_publication_distinguishes_missing_and_false_read_only_hints(
    app: FastAPI,
    admin: httpx.AsyncClient,
    mcp_endpoint: TestServer,
) -> None:
    path, _ = await register_endpoint(admin, app, mcp_endpoint)
    server = (await admin.get('/api/mcp/servers')).json()[0]
    application = (await admin.post('/api/developer/applications', json={'name': '发布审核'})).json()
    publish_to = f'/api/developer/applications/{application["id"]}/mcp-publications'
    body = {
        'server_id': server['id'],
        'tool_name': 'policy_lookup',
        'name': 'policy',
        'expected_version': server['version'],
        'confirm_read_only': True,
    }
    async with app.state.container.database.sessions.begin() as session:
        row = await session.scalar(select(McpServer))
        assert row is not None
        row.tools = [
            {
                'name': 'policy_lookup',
                'description': mcp_endpoint.description,
                'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'}}},
                'read_only': False,
            }
        ]
    assert (await admin.get('/api/mcp/publication-sources')).json() == []
    legacy = await admin.post(publish_to, json=body)
    assert legacy.status_code == 409 and 'mcp_publication_sync_required' in legacy.text

    mcp_endpoint.read_only_hint = False
    sync = await admin.post(path + '/sync', json={'expected_version': server['version']})
    assert sync.status_code == 200, sync.text
    assert (await admin.get('/api/mcp/publication-sources')).json() == []
    rejected = await admin.post(publish_to, json={**body, 'expected_version': sync.json()['version']})
    assert rejected.status_code == 422 and 'mcp_publication_not_read_only' in rejected.text
    assert mcp_endpoint.calls == []

    mcp_endpoint.read_only_hint = None
    refreshed = await admin.post(path + '/sync', json={'expected_version': sync.json()['version']})
    assert refreshed.status_code == 200, refreshed.text
    assert [tool['name'] for tool in (await admin.get('/api/mcp/publication-sources')).json()] == ['policy_lookup']
    allowed = await admin.post(publish_to, json={**body, 'expected_version': refreshed.json()['version']})
    assert allowed.status_code == 201, allowed.text


async def test_mcp_gateway_scopes_results_and_revocation(  # noqa: PLR0915 - exercise the application and key lifecycle
    app: FastAPI, admin: httpx.AsyncClient, mcp_endpoint: TestServer, other_endpoint: TestServer
) -> None:
    path, server_id = await register_endpoint(admin, app, mcp_endpoint)
    _, second_server_id = await register_endpoint(admin, app, other_endpoint)
    servers = {row['id']: row for row in (await admin.get('/api/mcp/servers')).json()}
    created = await admin.post('/api/developer/applications', json={'name': 'MCP 测试应用'})
    assert created.status_code == 201, created.text
    application_id = created.json()['id']
    console = f'/api/developer/applications/{application_id}'
    body = {
        'server_id': server_id,
        'expected_version': servers[server_id]['version'],
        'tool_name': 'policy_lookup',
        'name': 'company_policy',
        'confirm_read_only': True,
    }
    first = await admin.post(console + '/mcp-publications', json=body)
    assert first.status_code == 201, first.text
    first_id = first.json()['id']
    second = await admin.post(
        console + '/mcp-publications',
        json={
            **body,
            'name': 'other_policy',
            'server_id': second_server_id,
            'expected_version': servers[second_server_id]['version'],
        },
    )
    assert second.status_code == 201, second.text
    assert (await admin.post(console + '/mcp-publications', json=body)).status_code == 409
    denied = await admin.post(
        console + '/keys', json={'name': 'Invalid tool', 'scopes': ['mcp:invoke'], 'mcp_tool_ids': [str(uuid4())]}
    )
    assert denied.status_code == 404, denied.text
    another = await admin.post('/api/developer/applications', json={'name': 'Another MCP app'})
    assert another.status_code == 201, another.text
    assert (
        await admin.post(
            '/api/developer/applications/' + another.json()['id'] + '/keys',
            json={'name': 'Foreign publication', 'scopes': ['mcp:invoke'], 'mcp_tool_ids': [first_id]},
        )
    ).status_code == 404
    issued = await admin.post(
        console + '/keys', json={'name': 'MCP integration', 'scopes': ['mcp:invoke'], 'mcp_tool_ids': [first_id]}
    )
    assert issued.status_code == 201, issued.text
    key = issued.json()
    headers = {'Authorization': f'Bearer {key["api_key"]}', 'MCP-Protocol-Version': '2025-06-18'}

    async def rpc(method: str, params: dict[str, object] | None = None) -> dict:
        response = await admin.post(
            '/v1/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}
        )
        assert response.status_code == 200, response.text
        assert TOKEN not in response.text
        return response.json()

    assert (await rpc('initialize', {'protocolVersion': '2025-06-18'}))['result']['protocolVersion'] == '2025-06-18'
    notification = await admin.post(
        '/v1/mcp', headers=headers, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}
    )
    assert notification.status_code == 202
    assert [tool['name'] for tool in (await rpc('tools/list'))['result']['tools']] == ['company_policy']
    changed = await admin.put(
        path + '/tools/policy_lookup',
        json={'enabled': False, 'role_ids': [], 'expected_version': servers[server_id]['version']},
    )
    assert changed.status_code == 200, changed.text
    assert [tool['name'] for tool in (await rpc('tools/list'))['result']['tools']] == ['company_policy']
    assert (await rpc('tools/call', {'name': 'other_policy', 'arguments': QUERY}))['error']['code'] == -32602
    assert mcp_endpoint.calls == []
    assert (await rpc('tools/call', {'name': 'company_policy', 'arguments': QUERY}))['error']['code'] == -32000
    mcp_endpoint.result = {
        'content': [{'type': 'text', 'text': '制度查询结果'}],
        'structuredContent': {'approved': True},
    }
    result = (await rpc('tools/call', {'name': 'company_policy', 'arguments': QUERY}))['result']
    assert result['structuredContent'] == {'approved': True}
    assert mcp_endpoint.calls[-1] == {'name': 'policy_lookup', 'arguments': QUERY}
    audits = (await admin.get(path + '/calls')).json()
    assert audits[0]['key_id'] == key['id'] and audits[0]['application_id'] == application_id
    assert audits[0]['run_id'] is None and audits[0]['status'] == 'succeeded'
    removed = await admin.request('DELETE', console + '/mcp-publications/' + first_id, json={})
    assert removed.status_code == 204, removed.text
    assert (await rpc('tools/list'))['result'] == {'tools': []}
    assert (await rpc('tools/call', {'name': 'company_policy', 'arguments': QUERY}))['error']['code'] == -32602
    assert len(mcp_endpoint.calls) == 2
    assert other_endpoint.calls == []
    second_key = await admin.post(
        console + '/keys',
        json={'name': 'Second upstream', 'scopes': ['mcp:invoke'], 'mcp_tool_ids': [second.json()['id']]},
    )
    assert second_key.status_code == 201, second_key.text
    headers['Authorization'] = 'Bearer ' + second_key.json()['api_key']
    other_endpoint.result = {'content': [{'type': 'text', 'text': 'second result'}]}
    assert [tool['name'] for tool in (await rpc('tools/list'))['result']['tools']] == ['other_policy']
    assert (await rpc('tools/call', {'name': 'other_policy', 'arguments': QUERY}))['result']['content'] == [
        {'type': 'text', 'text': 'second result'}
    ]
    assert other_endpoint.calls == [{'name': 'policy_lookup', 'arguments': QUERY}]
    assert (await admin.post('/v1/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})).status_code == 401
