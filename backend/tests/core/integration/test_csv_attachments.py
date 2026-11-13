from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from zhiheng.runtime.orm import Run


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'gbk', 'gb18030'])
async def test_chinese_csv_upload_parse_and_chat_input(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, encoding: str
) -> None:
    source = '商品,销量,备注\r\n手机,12,"含逗号,与换行\n第二行"'
    uploaded = await admin.post(
        '/api/chat/attachments',
        params={'filename': '电商产品数据表.csv'},
        content=source.encode(encoding),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()['id']
    assert uploaded.json()['mime'] == 'text/csv'
    parsed = await admin.post(f'/api/files/{file_id}/parse', json={})
    assert parsed.status_code == 200, parsed.text
    expected = '商品\t销量\t备注\n手机\t12\t含逗号,与换行\n第二行'
    assert parsed.json()['text'] == expected
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    admitted = await admin.post(
        f'/api/chat/conversations/{conversation}/runs',
        json={
            'model_id': model_id,
            'message': '分析表格',
            'client_request_id': str(uuid4()),
            'file_ids': [file_id],
        },
    )
    assert admitted.status_code == 202, admitted.text
    async with app.state.container.database.sessions() as session:
        payload = await session.scalar(select(Run.payload).where(Run.id == UUID(admitted.json()['id'])))
    assert payload is not None
    assert expected in payload['messages'][-1]['content']


@pytest.mark.parametrize('content', [b'bad\x00csv', b'\x81'])
async def test_invalid_csv_is_rejected_before_model_admission(
    admin: httpx.AsyncClient, model_id: str, content: bytes
) -> None:
    uploaded = await admin.post(
        '/api/chat/attachments',
        params={'filename': 'bad.csv'},
        content=content,
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    result = await admin.post(
        f'/api/chat/conversations/{conversation}/runs',
        json={
            'model_id': model_id,
            'message': '分析表格',
            'client_request_id': str(uuid4()),
            'file_ids': [uploaded.json()['id']],
        },
    )
    assert result.status_code == 422, result.text
    assert result.json()['error']['code'] == 'invalid_document'
    assert (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json() == []
