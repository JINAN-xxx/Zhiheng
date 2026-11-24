import io
import json
import zipfile
from collections import Counter
from dataclasses import replace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from langchain.agents.middleware import SummarizationMiddleware
from sqlalchemy import select

from tests.core.integration.test_work_agent_execution import consume, turn, workspace_model
from tests.core.integration.test_work_agent_execution import (
    workspace_provider as workspace_provider,  # noqa: PLC0414 - register shared fixture
)
from tests.core.integration.test_work_agent_resume import continue_task
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.infrastructure.agents.adapters import workspace as adapter
from zhiheng.modules.models.public import ModelMessage, estimate_messages
from zhiheng.modules.work.public import ModelInvoker
from zhiheng.runtime.orm import Run

MARKERS = ('REPORT_HEAD', 'REPORT_MIDDLE', 'REPORT_TAIL')
TRUNCATED_SUMMARY = '这段摘要没有结束'


class ReportResponder:
    def __init__(self) -> None:
        self.summary_mode = 'valid'
        self.summary_requests: list[str] = []
        self.poisoned_requests = 0
        self.final_contexts: list[str] = []

    def __call__(self, body: dict[str, object]) -> dict[str, object]:
        messages = body['messages']
        text = '\n'.join(str(message.get('content', '')) for message in messages)
        if not body.get('tools'):
            if messages[0]['content'].startswith('用中文压缩历史'):
                self.summary_requests.append(text)
                if self.summary_mode == 'empty':
                    return {'role': 'assistant', 'content': ''}
                included = [marker for marker in MARKERS if marker in text]
                return {
                    'role': 'assistant',
                    'content': '用户要求阅读完整报告。已读事实：'
                    + '、'.join(included)
                    + '。保留资料编号及已完成读取，继续处理剩余段落。',
                }
            facts = json.loads(messages[-1]['content'])
            return {'role': 'assistant', 'content': facts['candidate']}
        if 'Previous conversation was too long to summarize.' in text:
            self.poisoned_requests += 1
            return WorkspaceProvider.tool('invalid_tool', {}, 'fail-on-corrupt-context')
        returned = []
        for message in messages:
            if message['role'] == 'tool':
                result = json.loads(message['content'])
                data = result.get('data') or {}
                if 'loaded_toolsets' not in data:
                    returned.append(data)
        if not returned:
            if 'inspect_file' not in {tool['function']['name'] for tool in body.get('tools', [])}:
                return WorkspaceProvider.tool('load_tools', {'groups': ['inspection']}, 'load-inspection')
            metadata = json.JSONDecoder().raw_decode(messages[0]['content'].split('任务目标与资料：\n', 1)[1])[0]
            return WorkspaceProvider.tool(
                'inspect_file', {'file_id': metadata['inputs'][0]['file_id']}, 'inspect-report'
            )
        last = returned[-1]
        if 'unit' not in last:
            return WorkspaceProvider.tool(
                'read_document', {'file_id': last['file_id'], 'start': 1, 'limit': 60}, 'read-1'
            )
        if last.get('next') is not None:
            start = last['next']
            return WorkspaceProvider.tool(
                'read_document',
                {'file_id': last['file_id'], 'start': start, 'limit': 55 if start == 61 else 50},
                f'read-{start}',
            )
        assert all(marker in text for marker in MARKERS), 'The model lost a previously read report section'
        assert '阅读完整报告' in text
        self.final_contexts.append(text)
        return {'role': 'assistant', 'content': '报告阅读完成：' + '、'.join(MARKERS)}


async def report_task(admin: httpx.AsyncClient, model_id: str) -> dict[str, object]:
    paragraphs = []
    for index in range(164):
        marker = {0: MARKERS[0], 60: MARKERS[1], 115: MARKERS[2]}.get(index, '')
        paragraphs.append(
            '<w:p><w:r><w:t>' + f'第{index + 1}段 {marker} ' + '研究内容与验证依据。' * 12 + '</w:t></w:r></w:p>'
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            'word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + ''.join(paragraphs)
            + '</w:body></w:document>',
        )
    created = await admin.post(
        '/api/work/tasks', json={'client_request_id': str(uuid4()), 'model_id': model_id, 'goal': '阅读完整报告'}
    )
    assert created.status_code == 201, created.text
    task = created.json()
    uploaded = await admin.post(
        f'/api/work/tasks/{task["id"]}/files?filename=report.docx',
        content=buffer.getvalue(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()


async def test_paginated_report_preserves_head_middle_tail_through_real_summaries(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responder = ReportResponder()
    monkeypatch.setattr(workspace_provider, 'response', responder)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await report_task(admin, model_id)
    run = await turn(admin, task, '阅读完整报告')
    await consume(app, seconds=60)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed' and all(marker in final['text'] for marker in MARKERS), final
    assert responder.summary_requests and responder.final_contexts and responder.poisoned_requests == 0
    assert any(estimate_messages((ModelMessage('user', prompt),)) > 4000 for prompt in responder.summary_requests)
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    summaries = [step for step in detail['steps'] if step['name'] == 'summary']
    assert summaries and all(step['status'] == 'succeeded' for step in summaries)


async def test_empty_summary_pauses_without_discarding_report_and_explicit_continue_can_retry(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responder = ReportResponder()
    responder.summary_mode = 'empty'
    monkeypatch.setattr(workspace_provider, 'response', responder)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await report_task(admin, model_id)
    run = await turn(admin, task, '阅读完整报告')
    await consume(app, seconds=60)
    failed = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert failed['status'] == 'interrupted' and failed['error']['code'] == 'work_summary_failed', failed
    assert len(responder.summary_requests) == 1 and not responder.poisoned_requests
    responder.summary_mode = 'valid'
    resumed = await continue_task(admin, task['id'])
    await consume(app, seconds=60)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed["id"]}')).json()
    assert final['status'] == 'completed' and responder.final_contexts, final
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    starts = [step['details']['arguments'].get('start') for step in detail['steps'] if step['name'] == 'read_document']
    assert len(starts) == len(set(starts)), starts
    async with app.state.container.database.sessions() as session:
        models = list(await session.scalars(select(Run).where(Run.resource_type == 'work_agent_model')))
    assert (
        sum(
            model.status == 'completed' and not model.payload.get('tools') and not model.text.strip()
            for model in models
        )
        == 1
    )


@pytest.mark.parametrize('fixed_cap', [False, True])
async def test_truncated_summary_retries_without_replaying_reads(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
    fixed_cap: bool,
) -> None:
    context = app.state.container.work_agent.context
    if fixed_cap:
        monkeypatch.setattr(
            context, 'policy', replace(context.policy, summary=replace(context.policy.summary, output_tokens=1024))
        )
    responder = ReportResponder()
    caps: list[object] = []

    def respond(body: dict[str, object]) -> dict[str, object]:
        if not body.get('tools') and body['messages'][0]['content'].startswith('用中文压缩历史'):
            caps.append(body.get('max_completion_tokens'))
            if len(caps) == 1:
                return {'role': 'assistant', 'content': TRUNCATED_SUMMARY, '_finish_reason': 'length'}
        return responder(body)

    monkeypatch.setattr(workspace_provider, 'response', respond)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await report_task(admin, model_id)
    run = await turn(admin, task, '阅读完整报告')
    await consume(app, seconds=60)
    url = f'/api/work/tasks/{task["id"]}'
    if fixed_cap:
        stopped = (await admin.get(f'{url}/runs/{run["id"]}')).json()
        assert stopped['status'] == 'interrupted' and stopped['error']['code'] == 'work_summary_output_limit', stopped
        assert TRUNCATED_SUMMARY not in stopped['text']
        assert caps == [1024]
        run = await continue_task(admin, task['id'])
        await consume(app, seconds=60)
    final = (await admin.get(f'{url}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed' and all(marker in final['text'] for marker in MARKERS), final
    assert caps[:2] == ([1024, 1024] if fixed_cap else [2048, 4096])
    assert all(TRUNCATED_SUMMARY not in text for text in responder.final_contexts)
    detail = (await admin.get(url)).json()
    steps = detail['steps']
    summary_steps = [step for step in steps if step['name'] == 'summary']
    assert [step['status'] for step in summary_steps][:2] == ['failed', 'succeeded']
    starts = [step['details']['arguments'].get('start') for step in steps if step['name'] == 'read_document']
    assert starts and len(starts) == len(set(starts)), starts


async def test_legacy_poisoned_checkpoint_recovers_report_without_replaying_confirmed_reads(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responder = ReportResponder()
    monkeypatch.setattr(workspace_provider, 'response', responder)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await report_task(admin, model_id)
    factory = adapter._summary_middleware

    def old_middleware(
        invoke_model: ModelInvoker, trigger_tokens: int, keep_tokens: int
    ) -> SummarizationMiddleware[Any, Any]:
        middleware = factory(invoke_model, trigger_tokens, keep_tokens)
        middleware.trim_tokens_to_summarize = 4000
        return middleware

    monkeypatch.setattr(adapter, '_summary_middleware', old_middleware)
    run = await turn(admin, task, '阅读完整报告')
    await consume(app, seconds=60)
    failed = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert failed['status'] == 'failed' and responder.poisoned_requests == 1, failed
    before = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert any(step['name'] == 'read_document' for step in before['steps'])
    monkeypatch.setattr(adapter, '_summary_middleware', factory)
    resumed = await continue_task(admin, task['id'])
    await consume(app, seconds=60)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed["id"]}')).json()
    assert final['status'] == 'completed' and responder.poisoned_requests == 1, final
    assert responder.final_contexts and all(marker in final['text'] for marker in MARKERS)
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    reads = Counter(
        step['details']['arguments']['start'] for step in detail['steps'] if step['name'] == 'read_document'
    )
    assert reads and all(count == 1 for count in reads.values()), reads
    assert (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()['status'] == 'failed'
