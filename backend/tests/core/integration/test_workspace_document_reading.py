"""Budgeted document reads through native Runs and actual durable Agent checkpoints."""

import io
import json
import zipfile
from collections import Counter
from uuid import uuid4
from xml.sax.saxutils import escape

import httpx
import pytest
from fastapi import FastAPI

from tests.core.billing_support import enable_free_billing
from tests.core.integration.test_work_agent_execution import consume, turn
from tests.core.integration.test_work_agent_execution import workspace_provider as workspace_provider  # noqa: PLC0414
from tests.core.integration.test_work_agent_resume import continue_task
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.application.work.execution import ExecutionProgress, WorkAgentExecutor
from zhiheng.modules.models.public import AgentModelMessage
from zhiheng.modules.work.public import ModelTurn
from zhiheng.runtime.contracts import ClaimedRun
from zhiheng.shared.errors import AppError


class DocumentResponder:
    def __init__(self) -> None:
        self.file_id = ''
        self.latest: dict[str, object] | None = None
        self.pages: dict[tuple[int, int], dict[str, object]] = {}
        self.summaries = 0

    def remember(self, text: str) -> None:
        for index in range(len(text)):
            if not text.startswith('{"success":', index):
                continue
            try:
                envelope = json.JSONDecoder().raw_decode(text, index)[0]
            except ValueError:
                continue
            data = envelope.get('data') or {}
            if data.get('read_mode') != 'budgeted':
                continue
            assert 'preview' not in data
            self.pages[(data['start'], data['offset'])] = data
            self.latest = data

    def __call__(self, body: dict[str, object]) -> dict[str, object]:
        messages = body['messages']
        for message in messages:
            self.remember(str(message.get('content', '')))
        if not body.get('tools'):
            if messages[0]['content'].startswith('用中文压缩历史'):
                self.summaries += 1
                return {
                    'role': 'assistant',
                    'content': '继续阅读原报告；已确认的读取结果保留。游标：'
                    + json.dumps(
                        {
                            'file_id': self.file_id,
                            'next': self.latest['next'] if self.latest else 1,
                            'next_offset': self.latest['next_offset'] if self.latest else 0,
                        }
                    ),
                }
            return {'role': 'assistant', 'content': json.loads(messages[-1]['content'])['candidate']}
        if not self.file_id:
            metadata = json.JSONDecoder().raw_decode(messages[0]['content'].split('任务目标与资料：\n', 1)[1])[0]
            self.file_id = metadata['inputs'][0]['file_id']
        if self.latest is None:
            return WorkspaceProvider.tool('read_document', {'file_id': self.file_id}, 'read-full')
        if self.latest['has_more']:
            start, offset = self.latest['next'], self.latest['next_offset']
            return WorkspaceProvider.tool(
                'read_document',
                {
                    'file_id': self.file_id,
                    'start': start,
                    'offset': offset,
                },
                f'read-{start}-{offset}',
            )
        return {'role': 'assistant', 'content': '已读取全部提取文本，保留原文来源。'}


async def task_with_document(
    admin: httpx.AsyncClient, provider: WorkspaceProvider, blocks: list[str], window: int | None
) -> dict[str, object]:
    connection = await admin.post(
        '/api/models/connections',
        json={
            'name': 'document fixture',
            'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
            'api_key': 'test-only',
        },
    )
    assert connection.status_code == 201, connection.text
    model = await admin.post(
        '/api/models',
        json={
            'name': 'document fixture',
            'connection_id': connection.json()['id'],
            'upstream_model': 'workspace-greeting',
            'max_output_tokens': 4096,
            'context_window_tokens': window,
        },
    )
    assert model.status_code == 201, model.text
    await enable_free_billing(admin)
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model.json()['id'],
            'goal': '读一下这一篇报告',
        },
    )
    assert created.status_code == 201, created.text
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            'word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + ''.join('<w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>' for text in blocks)
            + '</w:body></w:document>',
        )
    task = created.json()
    uploaded = await admin.post(
        f'/api/work/tasks/{task["id"]}/files?filename=report.docx',
        content=output.getvalue(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()


async def test_164_block_report_reaches_model_in_one_structured_read_without_inspection_or_preview(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responder = DocumentResponder()
    monkeypatch.setattr(workspace_provider, 'response', responder)
    blocks = [f'Paragraph {index + 1}. ' + 'Original source facts. ' * 3 for index in range(164)]
    task = await task_with_document(admin, workspace_provider, blocks, None)
    run = await turn(admin, task, '读一下这一篇报告')
    await consume(app, seconds=60)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed', final
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    tools = [step for step in detail['steps'] if step['kind'] == 'tool']
    assert [step['name'] for step in tools] == ['read_document']
    assert responder.latest['complete'] is True and len(responder.pages) == 1
    assert [item['text'] for item in responder.latest['items']] == blocks


async def test_small_context_and_explicit_resume_preserve_long_block_cursors_without_replaying_reads(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responder = DocumentResponder()
    monkeypatch.setattr(workspace_provider, 'response', responder)
    original = 'HEAD' + '文档中的完整原始依据。' * 350 + 'TAIL'
    task = await task_with_document(admin, workspace_provider, [original], 16384)
    invoke = WorkAgentExecutor.model_call
    interrupted = False

    async def interrupt_once(
        self: WorkAgentExecutor,
        claim: ClaimedRun,
        progress: ExecutionProgress,
        messages: tuple[AgentModelMessage, ...],
        schemas: tuple[dict[str, object], ...],
        key: str,
        purpose: str,
        *,
        project_text: bool = False,
    ) -> ModelTurn:
        nonlocal interrupted
        read_in_context = any(
            message.role == 'tool' and '"read_mode": "budgeted"' in message.content for message in messages
        )
        if purpose == 'agent' and (read_in_context or responder.latest is not None) and not interrupted:
            interrupted = True
            raise AppError('model_idle_timeout', '本地夹具模拟模型停流', 504)
        return await invoke(self, claim, progress, messages, schemas, key, purpose, project_text=project_text)

    monkeypatch.setattr(WorkAgentExecutor, 'model_call', interrupt_once)
    run = await turn(admin, task, '读一下这一篇报告')
    await consume(app, seconds=60)
    stopped = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert stopped['status'] == 'interrupted', stopped
    for _ in range(3):
        resumed = await continue_task(admin, task['id'])
        await consume(app, seconds=60)
        final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed["id"]}')).json()
        if final['status'] == 'completed':
            break
        assert final['status'] == 'interrupted' and final['error']['code'] == 'work_budget_exhausted', final
    diagnostics = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert final['status'] == 'completed', json.dumps(
        {
            'error': final.get('error'),
            'summaries': responder.summaries,
            'pages': list(responder.pages),
            'steps': [(step['name'], step['status'], step['summary']) for step in diagnostics['steps']],
        },
        ensure_ascii=False,
    )
    assert len(responder.pages) > 1
    pieces = [page['items'][0]['text'] for _, page in sorted(responder.pages.items())]
    assert ''.join(pieces) == original
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    reads = [step for step in detail['steps'] if step['name'] == 'read_document']
    cursors = Counter(
        (step['details']['arguments'].get('start', 1), step['details']['arguments'].get('offset', 0)) for step in reads
    )
    assert all(count == 1 for count in cursors.values()), cursors
    assert (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()['status'] == 'interrupted'
