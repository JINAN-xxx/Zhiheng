"""Public task requests through ARQ, metered native Runs and real PostgreSQL Agent checkpoints."""

import asyncio
import io
import json
import os
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select, text

from tests.core.billing_support import enable_free_billing
from tests.core.integration.test_platform_module_permissions import grant, member
from tests.core.permission_support import grants
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.work.orm import WorkSandboxLease
from zhiheng.runtime.orm import Run
from zhiheng.worker.main import build_worker

SANDBOX_VARIABLES = ('ZHIHENG_TEST_SANDBOX_URL', 'ZHIHENG_TEST_SANDBOX_API_KEY', 'ZHIHENG_TEST_SANDBOX_IMAGE')


@pytest.fixture(autouse=True)
def workspace_settings(settings: Settings) -> None:
    if all(os.getenv(key) for key in SANDBOX_VARIABLES):
        settings.sandbox_url = os.environ['ZHIHENG_TEST_SANDBOX_URL']
        settings.sandbox_api_key = SecretStr(os.environ['ZHIHENG_TEST_SANDBOX_API_KEY'])
        settings.sandbox_image = os.environ['ZHIHENG_TEST_SANDBOX_IMAGE']
    settings.sandbox_run_seconds = 120
    if os.getenv('ZHIHENG_TEST_LIVE_MODEL_CONFIG'):
        settings.provider_timeout_seconds = 120
        settings.lease_seconds = 30
        settings.sandbox_run_seconds = 240


@pytest.fixture
def workspace_provider() -> Iterator[WorkspaceProvider]:
    server = WorkspaceProvider()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def workspace_model(admin: httpx.AsyncClient, provider: WorkspaceProvider, upstream: str) -> str:
    connection = await admin.post(
        '/api/models/connections',
        json={
            'name': upstream,
            'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
            'api_key': 'test-only',
        },
    )
    assert connection.status_code == 201, connection.text
    model = await admin.post(
        '/api/models',
        json={
            'name': upstream,
            'connection_id': connection.json()['id'],
            'upstream_model': upstream,
            'max_output_tokens': 4096,
        },
    )
    assert model.status_code == 201, model.text
    await enable_free_billing(admin)
    return model.json()['id']


async def consume(app: FastAPI, *, seconds: int = 30) -> None:
    worker = build_worker(app.state.container, burst=True)
    try:
        await asyncio.wait_for(worker.async_run(), timeout=seconds)
    finally:
        await worker.close()


async def turn(
    admin: httpx.AsyncClient, task: dict[str, object], message: str, *, initial: bool = False
) -> dict[str, object]:
    response = await admin.post(
        f'/api/work/tasks/{task["id"]}/runs',
        json={
            'client_request_id': task['create_client_request_id'] if initial else str(uuid4()),
            'expected_version': task['version'],
            'message': message,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def test_greeting_uses_real_agent_checkpoints_and_metered_children_without_sandbox(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
) -> None:
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': '你好',
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    run = await turn(admin, task, '你好', initial=True)
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed', final
    assert '你好' in final['text']
    assert final['usage'] == {'input_tokens': 34, 'output_tokens': 6, 'status': 'known'}
    async with app.state.container.database.sessions() as session:
        children = (
            await session.scalars(select(Run).where(Run.payload['parent_run_id'].as_string() == run['id']))
        ).all()
        leases = (await session.scalars(select(WorkSandboxLease))).all()
        assert len(children) == 2 and all(item.status == 'completed' for item in children)
        assert all(item.resource_type == 'work_agent_model' for item in children)
        assert not leases
        assert await session.scalar(text('SELECT count(*) FROM work_checkpoints.checkpoints')) > 0
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert detail['execution_state'] == 'completed'
    assert [item['role'] for item in detail['messages']] == ['user', 'assistant']
    assert len(workspace_provider.requests) == 2
    initial_tools = [item['function']['name'] for item in workspace_provider.requests[0].get('tools', [])]
    assert 'run_python' not in initial_tools and 'load_tools' in initial_tools
    assert (await admin.get('/api/usage')).json()['used_tokens'] == 40


async def test_model_controls_are_lossless_in_checkpoint_and_safe_in_postgres_projections(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
) -> None:
    model_id = await workspace_model(admin, workspace_provider, 'workspace-controls')
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': '检查原始字符',
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    run = await turn(admin, task, '检查原始字符', initial=True)
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed', final
    assert '\\u0000' in final['text'] and '\x00' not in final['text']
    async with app.state.container.database.sessions() as session:
        children = (await session.scalars(select(Run).where(Run.resource_type == 'work_agent_model'))).all()
        assert len(children) == 2
        for child in children:
            assert '\x00' not in child.text
            assert '\x00' in json.loads(child.result['agent_message_json'])['content']
        payloads = (await session.scalars(text('SELECT checkpoint FROM work_checkpoints.checkpoints'))).all()
        assert payloads


@pytest.mark.skipif(
    not os.getenv('ZHIHENG_TEST_LIVE_MODEL_CONFIG'), reason='Private live model smoke opt-in is required'
)
async def test_live_model_generates_and_publishes_verified_artifact(
    admin: httpx.AsyncClient,
    app: FastAPI,
    settings: Settings,
) -> None:
    configuration_path = Path(os.environ['ZHIHENG_TEST_LIVE_MODEL_CONFIG'])
    configuration = json.loads(await asyncio.to_thread(configuration_path.read_text))
    connection = await admin.post(
        '/api/models/connections',
        json={
            'name': 'Isolated live model smoke',
            'base_url': configuration['base_url'],
            'api_key': configuration['api_key'],
        },
    )
    assert connection.status_code == 201, connection.status_code
    model = await admin.post(
        '/api/models',
        json={
            'name': 'Isolated live model smoke',
            'connection_id': connection.json()['id'],
            'upstream_model': configuration['upstream_model'],
            'max_output_tokens': 2048,
            'context_window_tokens': configuration.get('context_window_tokens'),
        },
    )
    assert model.status_code == 201, model.status_code
    await enable_free_billing(admin)
    goal = '使用真实 Python 读取上传 CSV，计算 amount 列总和，生成 outputs/total.txt，内容严格为 total=200 加换行。发布这个成果。不制作其他文件；完成后简短报告真实结果。'
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model.json()['id'],
            'goal': goal,
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    uploaded = await admin.post(
        f'/api/work/tasks/{task["id"]}/files',
        params={'filename': 'amount.csv'},
        content=b'department,amount\nResearch,120\nSales,80\n',
        headers={'content-type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    task = uploaded.json()
    run = await turn(admin, task, goal, initial=True)
    await consume(app, seconds=270)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    if final['status'] != 'completed' and (final.get('error') or {}).get('code') == 'work_model_failed':
        resumed = await admin.post(
            f'/api/work/tasks/{task["id"]}/runs',
            json={
                'client_request_id': str(uuid4()),
                'expected_version': detail['version'],
                'resume': True,
            },
        )
        assert resumed.status_code == 202, resumed.text
        await consume(app, seconds=270)
        final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed.json()["id"]}')).json()
        detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    async with app.state.container.database.sessions() as session:
        children = (await session.scalars(select(Run).where(Run.resource_type == 'work_agent_model'))).all()
        diagnostics = {
            'final': final,
            'steps': [
                {'name': step['name'], 'status': step['status'], 'summary': step['summary']} for step in detail['steps']
            ],
            'children': [
                {
                    'status': child.status,
                    'error': child.error_code,
                    'text_chars': len(child.text),
                    'reasoning_chars': len(child.reasoning),
                    'finish_reason': child.finish_reason,
                    'input_tokens': child.input_tokens,
                    'output_tokens': child.output_tokens,
                }
                for child in children
            ],
        }
    await asyncio.to_thread(
        configuration_path.with_suffix('.result.json').write_text, json.dumps(diagnostics, ensure_ascii=False)
    )
    assert final['status'] == 'completed', diagnostics
    artifact = next(item for item in detail['artifacts'] if item['logical_path'] == 'outputs/total.txt')
    response = await admin.get(f'/api/work/tasks/{task["id"]}/artifacts/{artifact["id"]}/download')
    assert response.status_code == 200 and response.content == b'total=200\n'
    assert any(item['name'] == 'run_python' and item['status'] == 'succeeded' for item in detail['steps'])
    assert all(item.status == 'closed' for item in (await leases_for(app)))


async def leases_for(app: FastAPI) -> list[WorkSandboxLease]:
    async with app.state.container.database.sessions() as session:
        return list((await session.scalars(select(WorkSandboxLease))).all())


@pytest.mark.skipif(not all(os.getenv(key) for key in SANDBOX_VARIABLES), reason='Real OpenSandbox opt-in is required')
async def test_real_office_artifacts_preview_and_immutable_continuation(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
) -> None:
    model_id = await workspace_model(admin, workspace_provider, 'workspace-office')
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': 'CSV OFFICE TASK',
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    source = b'department,amount\nResearch,120\nSales,80\n'
    uploaded = await admin.post(
        f'/api/work/tasks/{task["id"]}/files',
        params={'filename': 'amount.csv'},
        content=source,
        headers={'content-type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    task = uploaded.json()
    first = await turn(admin, task, 'CSV OFFICE TASK', initial=True)
    await consume(app, seconds=150)
    snapshot = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{first["id"]}')).json()
    assert snapshot['status'] == 'completed', snapshot
    task = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert task['execution_state'] == 'completed'
    assert {item['logical_path'] for item in task['artifacts']} == {'outputs/amount.xlsx', 'outputs/report.docx'}
    originals = {}
    for artifact in task['artifacts']:
        assert artifact['version'] == 1 and artifact['validation']['valid'] is True and artifact['preview_file_id']
        path = f'/api/work/tasks/{task["id"]}/artifacts/{artifact["id"]}'
        downloaded = await admin.get(path + '/download')
        preview = await admin.get(path + '/preview')
        assert downloaded.status_code == preview.status_code == 200
        assert preview.content.startswith(b'%PDF-') and preview.headers['content-type'] == 'application/pdf'
        originals[artifact['logical_path']] = (artifact, downloaded.content)
    assert_office_content(originals)
    code_step = next(item for item in task['steps'] if item['name'] == 'run_python')
    assert '\x00' not in code_step['result']['data']['stdout']
    assert '\\u0000' in code_step['result']['data']['stdout']
    second = await turn(admin, task, 'REVISION: update the Word report and preserve the spreadsheet')
    await consume(app, seconds=150)
    current = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{second["id"]}')).json()
    assert current['status'] == 'completed', current
    task = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    updated = next(item for item in task['artifacts'] if item['logical_path'] == 'outputs/report.docx')
    assert updated['version'] == 2 and updated['id'] != originals['outputs/report.docx'][0]['id']
    revised = await admin.get(f'/api/work/tasks/{task["id"]}/artifacts/{updated["id"]}/download')
    assert 'Revision two:' in office_xml_text(revised.content, 'word/document.xml')
    for path, (artifact, content) in originals.items():
        old = await admin.get(f'/api/work/tasks/{task["id"]}/artifacts/{artifact["id"]}/download')
        assert old.status_code == 200 and old.content == content
        if path.endswith('.xlsx'):
            same = next(item for item in task['artifacts'] if item['logical_path'] == path)
            assert same['id'] == artifact['id'] and same['version'] == 1
    original_download = await admin.get(f'/api/work/tasks/{task["id"]}/inputs/{task["inputs"][0]["file_id"]}/download')
    assert original_download.content == source
    async with app.state.container.database.sessions() as session:
        leases = (await session.scalars(select(WorkSandboxLease))).all()
        assert len(leases) == 2 and all(lease.status == 'closed' for lease in leases)
        children = (await session.scalars(select(Run).where(Run.resource_type == 'work_agent_model'))).all()
        assert children and all(child.status == 'completed' for child in children)
        assert all(child.usage_status == 'known' for child in children)


def assert_office_content(originals: dict[str, tuple[dict[str, object], bytes]]) -> None:
    with zipfile.ZipFile(io.BytesIO(originals['outputs/amount.xlsx'][1])) as archive:
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    namespace = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    rows = [tuple(''.join(cell.itertext()) for cell in row) for row in sheet.findall('x:sheetData/x:row', namespace)]
    assert rows == [('department', 'amount'), ('Research', '120'), ('Sales', '80'), ('Total', '200')]
    assert 'Total amount: 200' in office_xml_text(originals['outputs/report.docx'][1], 'word/document.xml')


def office_xml_text(content: bytes, path: str) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        document = ET.fromstring(archive.read(path))
    return ''.join(document.itertext())


async def test_work_use_does_not_grant_execute_code(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
) -> None:
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    await grant(app, *grants('platform:work', 'create', 'use', 'update'))
    async with member(app) as client:
        accepted = await client.post(
            '/api/work/tasks',
            json={
                'client_request_id': str(uuid4()),
                'model_id': model_id,
                'goal': 'ordinary task',
            },
        )
        assert accepted.status_code == 201, accepted.text
        options = await client.get('/api/work/options')
        assert options.status_code == 200 and options.json()['can_execute_code'] is False
        task = accepted.json()
        run = await turn(client, task, str(task['goal']), initial=True)
        await consume(app)
        final = (await client.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
        assert final['status'] == 'completed', final
    assert workspace_provider.requests
    assert all(
        'run_python' not in [item['function']['name'] for item in request.get('tools', [])]
        for request in workspace_provider.requests
    )


@pytest.mark.skipif(not all(os.getenv(key) for key in SANDBOX_VARIABLES), reason='Real OpenSandbox opt-in is required')
async def test_agent_repairs_rejected_empty_output_and_publishes_successfully(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
) -> None:
    model_id = await workspace_model(admin, workspace_provider, 'workspace-repair')
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': 'REPAIR TASK',
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    run = await turn(admin, task, 'REPAIR TASK', initial=True)
    await consume(app, seconds=150)
    snapshot = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert snapshot['status'] == 'completed', snapshot
    task = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert task['execution_state'] == 'completed'
    publications = [step for step in task['steps'] if step['name'] == 'publish_outputs']
    assert sorted(step['status'] for step in publications) == ['failed', 'succeeded']
    failed = next(step for step in publications if step['status'] == 'failed')
    assert failed['result']['data']['rejected'][0]['code'] == 'work_artifact_empty'
    assert len(task['artifacts']) == 1 and task['artifacts'][0]['version'] == 1
    artifact = task['artifacts'][0]
    downloaded = await admin.get(f'/api/work/tasks/{task["id"]}/artifacts/{artifact["id"]}/download')
    assert downloaded.status_code == 200 and downloaded.content == b'repaired report'
    async with app.state.container.database.sessions() as session:
        leases = (await session.scalars(select(WorkSandboxLease))).all()
        assert len(leases) == 1 and leases[0].status == 'closed'
