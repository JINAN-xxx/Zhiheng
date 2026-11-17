"""Private assistant attachments use the caller's files and durable native Runs."""

from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from tests.core.integration.test_assistant_conversations import assistant, conversation
from tests.core.permission_support import account, grants
from tests.core.provider import ProviderServer
from zhiheng.modules.files.orm import FileRecord, FileReference
from zhiheng.worker.execution import execute_run


async def upload(
    client: httpx.AsyncClient,
    agent_id: str,
    content: bytes = b'ATTACHMENT_PRIVATE_MARKER: revenue is 42',
    name: str = 'report.txt',
) -> dict:
    result = await client.post(
        f'/api/agents/{agent_id}/conversations/attachments?filename={name}',
        content=content,
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert result.status_code == 201, result.text
    return result.json()


async def send(
    client: httpx.AsyncClient,
    agent: str,
    session: str,
    files: list[str],
    *,
    message: str = 'Summarize the material',
    request_id: str | None = None,
) -> httpx.Response:
    return await client.post(
        f'/api/agents/{agent}/conversations/{session}/runs',
        json={'message': message, 'client_request_id': request_id or str(uuid4()), 'file_ids': files},
    )


async def test_documents_reach_provider_history_and_followup_memory(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    agent = await assistant(admin, model_id)
    session = await conversation(admin, agent['id'])
    file = await upload(admin, agent['id'])
    result = await send(admin, agent['id'], session['id'], [file['id']])
    assert result.status_code == 202, result.text
    await execute_run(app.state.container, UUID(result.json()['id']))
    assert 'ATTACHMENT_PRIVATE_MARKER' in str(provider.requests)
    detail = (await admin.get(f'/api/agents/{agent["id"]}/conversations/{session["id"]}')).json()
    assert detail['turns'][0]['attachments'][0]['file_id'] == file['id']
    assert detail['turns'][0]['content'] == 'Summarize the material'
    download = await admin.get(
        f'/api/agents/{agent["id"]}/conversations/{session["id"]}/attachments/{file["id"]}/download'
    )
    assert download.status_code == 200 and b'ATTACHMENT_PRIVATE_MARKER' in download.content
    follow = await send(admin, agent['id'], session['id'], [], message='What is the revenue?')
    assert follow.status_code == 202, follow.text
    await execute_run(app.state.container, UUID(follow.json()['id']))
    assert 'ATTACHMENT_PRIVATE_MARKER' in str(provider.requests[-1]['messages'])
    async with app.state.container.database.sessions() as database:
        row = await database.get(FileRecord, UUID(file['id']))
        assert row.expires_at is None
        assert await database.scalar(
            select(FileReference.id).where(FileReference.file_id == row.id, FileReference.resource_type == 'agent_chat')
        )


async def test_attachment_only_replay_and_changed_files_are_distinct(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    agent = await assistant(admin, model_id)
    session = await conversation(admin, agent['id'])
    first = await upload(admin, agent['id'])
    second = await upload(admin, agent['id'], b'other', 'second.txt')
    request = str(uuid4())
    result = await send(admin, agent['id'], session['id'], [first['id']], message='', request_id=request)
    assert result.status_code == 202, result.text
    replay = await send(admin, agent['id'], session['id'], [first['id']], message='', request_id=request)
    assert replay.status_code == 202 and replay.json()['id'] == result.json()['id']
    changed = await send(admin, agent['id'], session['id'], [second['id']], message='', request_id=request)
    assert changed.status_code == 409, changed.text
    duplicate = await send(admin, agent['id'], session['id'], [first['id'], first['id']])
    assert duplicate.status_code == 422
    async with app.state.container.database.sessions.begin() as database:
        row = await database.get(FileRecord, UUID(first['id']))
        row.status = 'deleted'
    replay_after_file_removal = await send(
        admin, agent['id'], session['id'], [first['id']], message='', request_id=request
    )
    assert replay_after_file_removal.status_code == 202
    assert replay_after_file_removal.json()['id'] == result.json()['id']


async def test_agents_only_user_needs_no_chat_or_storage_grants(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    permissions = grants('platform:agents', 'use', 'create', 'publish')
    async with account(admin, app, *permissions) as (member, _role, _user):
        agent = await assistant(member, model_id)
        session = await conversation(member, agent['id'])
        file = await upload(member, agent['id'])
        result = await send(member, agent['id'], session['id'], [file['id']])
        assert result.status_code == 202, result.text
        assert (
            await member.post(
                '/api/chat/attachments?filename=x.txt',
                content=b'no',
                headers={'Content-Type': 'application/octet-stream'},
            )
        ).status_code == 403
        assert (
            await admin.get(
                f'/api/agents/{agent["id"]}/conversations/{session["id"]}/attachments/{file["id"]}/download'
            )
        ).status_code == 404


async def test_foreign_private_files_rejected_and_unbound_download_hidden(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    agent = await assistant(admin, model_id)
    session = await conversation(admin, agent['id'])
    own = await upload(admin, agent['id'])
    path = f'/api/agents/{agent["id"]}/conversations/{session["id"]}'
    assert (await admin.get(path + f'/attachments/{own["id"]}/download')).status_code == 404
    async with account(admin, app, *grants('platform:agents', 'use', 'create', 'publish')) as (member, _role, _user):
        other = await assistant(member, model_id)
        foreign = await upload(member, other['id'])
        denied = await send(admin, agent['id'], session['id'], [foreign['id']])
        assert denied.status_code in {403, 404}, denied.text
    assert not provider.requests


async def test_knowledge_preparation_keeps_attachments_and_delete_releases_refs(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    from tests.core.integration.test_knowledge import finish_preparation
    from tests.core.integration.test_knowledge_failures import ready_base

    base = await ready_base(admin, app, provider)
    agent = await assistant(admin, model_id, knowledge_base_ids=[base['id']])
    session = await conversation(admin, agent['id'])
    file = await upload(admin, agent['id'])
    result = await send(admin, agent['id'], session['id'], [file['id']])
    assert result.status_code == 202, result.text
    assert result.json()['kind'] == 'knowledge_preparation'
    path = f'/api/agents/{agent["id"]}/conversations/{session["id"]}'
    during = (await admin.get(path)).json()
    assert during['preparing_message']['attachments'][0]['file_id'] == file['id']
    prepared = await finish_preparation(admin, app, result.json())
    assert prepared['status'] == 'admitted', prepared
    await execute_run(app.state.container, UUID(prepared['run']['id']))
    assert 'ATTACHMENT_PRIVATE_MARKER' in str(provider.requests[-1]['messages'])
    detail = (await admin.get(path)).json()
    removed = await admin.request('DELETE', path, json={'expected_version': detail['conversation']['version']})
    assert removed.status_code == 204, removed.text
    async with app.state.container.database.sessions() as database:
        assert not await database.scalar(
            select(FileReference.id).where(
                FileReference.resource_id == UUID(session['id']), FileReference.resource_type == 'agent_chat'
            )
        )


async def test_image_model_validation_and_queued_revocation(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    from io import BytesIO

    from PIL import Image

    agent = await assistant(admin, model_id)
    session = await conversation(admin, agent['id'])
    buffer = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(buffer, format='PNG')
    rejected = await admin.post(
        f'/api/agents/{agent["id"]}/conversations/attachments?filename=image.png',
        content=buffer.getvalue(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert rejected.status_code == 422
    file = await upload(admin, agent['id'])
    accepted = await send(admin, agent['id'], session['id'], [file['id']])
    assert accepted.status_code == 202, accepted.text
    async with app.state.container.database.sessions.begin() as database:
        row = await database.get(FileRecord, UUID(file['id']))
        row.owner_id = await database.scalar(text("SELECT id FROM identity_users WHERE username='member'"))
        row.uploader_access_enabled = False
    await execute_run(app.state.container, UUID(accepted.json()['id']))
    snapshot = (await admin.get('/api/runs/' + accepted.json()['id'])).json()
    assert snapshot['status'] == 'failed'
    assert not provider.requests


async def test_vision_attachment_reaches_model_and_agent_delete_releases_files(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    from io import BytesIO

    from PIL import Image

    assert (await admin.patch('/api/models/' + model_id, json={'vision_enabled': True})).status_code == 200
    agent = await assistant(admin, model_id)
    session = await conversation(admin, agent['id'])
    buffer = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(buffer, format='PNG')
    file = await upload(admin, agent['id'], buffer.getvalue(), 'image.png')
    result = await send(admin, agent['id'], session['id'], [file['id']], message='What is in this image?')
    assert result.status_code == 202, result.text
    await execute_run(app.state.container, UUID(result.json()['id']))
    assert 'data:image/' in str(provider.requests[-1]['messages'])
    removed = await admin.delete('/api/agents/' + agent['id'], headers={'Content-Type': 'application/json'})
    assert removed.status_code == 200, removed.text
    async with app.state.container.database.sessions() as database:
        assert not await database.scalar(
            select(FileReference.id).where(
                FileReference.resource_id == UUID(session['id']), FileReference.resource_type == 'agent_chat'
            )
        )


@pytest.mark.parametrize('queued_followup', [False, True])
async def test_historical_attachment_revocation_blocks_checkpoint_reuse(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, queued_followup: bool
) -> None:
    agent = await assistant(admin, model_id)
    session = await conversation(admin, agent['id'])
    file = await upload(admin, agent['id'])
    accepted = await send(admin, agent['id'], session['id'], [file['id']])
    assert accepted.status_code == 202, accepted.text
    await execute_run(app.state.container, UUID(accepted.json()['id']))
    snapshot = (await admin.get('/api/runs/' + accepted.json()['id'])).json()
    assert snapshot['status'] == 'completed', snapshot
    calls_before = len(provider.requests)
    if queued_followup:
        followup = await send(admin, agent['id'], session['id'], [], message='Continue from the previous file')
        assert followup.status_code == 202, followup.text
    async with app.state.container.database.sessions.begin() as database:
        row = await database.get(FileRecord, UUID(file['id']))
        row.owner_id = await database.scalar(text("SELECT id FROM identity_users WHERE username='member'"))
        row.uploader_access_enabled = False
    if queued_followup:
        await execute_run(app.state.container, UUID(followup.json()['id']))
        snapshot = (await admin.get('/api/runs/' + followup.json()['id'])).json()
        assert snapshot['status'] == 'failed', snapshot
    else:
        followup = await send(admin, agent['id'], session['id'], [], message='Continue from the previous file')
        assert followup.status_code == 404, followup.text
    download = await admin.get(
        f'/api/agents/{agent["id"]}/conversations/{session["id"]}/attachments/{file["id"]}/download'
    )
    assert download.status_code == 404
    assert len(provider.requests) == calls_before
