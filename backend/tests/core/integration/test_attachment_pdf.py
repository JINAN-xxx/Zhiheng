from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.unit.test_attachment_pdf import mixed_pdf
from zhiheng.modules.files.public import ATTACHMENT_PARSER_VERSION
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError


async def test_chat_keeps_warning_in_history_and_model_input_without_reusing_partial_cache_for_strict_reader(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    c = app.state.container
    upload = await admin.post(
        '/api/chat/attachments?filename=book.pdf',
        content=mixed_pdf(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert upload.status_code == 201, upload.text
    fid = upload.json()['id']
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    admitted = await admin.post(
        f'/api/chat/conversations/{conversation}/runs',
        json={
            'model_id': model_id,
            'message': 'Analyze the body',
            'client_request_id': str(uuid4()),
            'file_ids': [fid],
        },
    )
    assert admitted.status_code == 202, admitted.text
    history = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    items = history['items'] if isinstance(history, dict) else history
    attachment = next(item for item in items if item['role'] == 'user')['attachments'][0]
    assert attachment['parser_version'] == ATTACHMENT_PARSER_VERSION
    assert '第 1 页' in attachment['parse_warning'] and '未识别' in attachment['parse_warning']
    async with c.database.sessions() as session:
        run = await session.get(Run, UUID(admitted.json()['id']))
        user_message = next(message for message in run.payload['messages'] if message['role'] == 'user')
        assert 'Readable body' in user_message['content'] and attachment['parse_warning'] in user_message['content']
        owner = await c.identity.principal_for_user(session, UUID(upload.json()['owner_id']))
    with pytest.raises(AppError) as caught:
        await c.file_inputs.prepare(owner, UUID(fid))
    assert caught.value.code == 'ocr_required'
    cached = await c.attachment_inputs.prepare(owner, UUID(fid))
    assert cached.summary.parse_warning == attachment['parse_warning']
    with pytest.raises(AppError) as caught:
        await c.file_inputs.from_artifact(owner, UUID(fid), cached.summary.artifact_file_id)
    assert caught.value.code == 'ocr_required'
