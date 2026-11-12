"""All history pages are complete, scoped and retrievable without model execution."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.runtime.orm import Run


async def test_conversations_page_search_activity_and_scope(admin: httpx.AsyncClient, app: FastAPI) -> None:
    container = app.state.container
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with container.database.sessions.begin() as session:
        member = await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'member'))
        rows = [
            Conversation(owner_id=owner, title=f'History-{i:03}', created_at=stamp + timedelta(seconds=i))
            for i in range(131)
        ]
        session.add_all(rows)
        hidden = Conversation(owner_id=member, title='History-PRIVATE', created_at=stamp + timedelta(days=1))
        archived = Conversation(owner_id=owner, title='History-archived', archived=True)
        deleted = Conversation(owner_id=owner, title='History-deleted', deleted_at=stamp)
        literal = Conversation(owner_id=owner, title='literal 100%_done\\yes', created_at=stamp)
        session.add_all([hidden, archived, deleted, literal])
        await session.flush()
        activity = stamp + timedelta(days=2)
        run = Run(
            owner_id=owner,
            billing_owner_id=owner,
            resource_type='chat',
            resource_id=rows[0].id,
            client_request_id=uuid4(),
            fingerprint='history-activity',
            payload={},
            status='completed',
        )
        session.add(run)
        await session.flush()
        session.add(
            ChatMessage(conversation_id=rows[0].id, run_id=run.id, role='user', content='Activity', created_at=activity)
        )
        expected = {str(row.id) for row in [*rows, literal]}
    endpoint = '/api/chat/conversations'
    all_rows = []
    cursor = None
    while True:
        params = {'limit': 17}
        if cursor:
            params['cursor'] = cursor
        response = await admin.get(endpoint, params=params)
        assert response.status_code == 200, response.text
        assert len(response.json()) <= 17
        all_rows.extend(response.json())
        cursor = response.headers.get('X-Next-Cursor')
        if not cursor:
            break
    assert len(all_rows) == 132 and {row['id'] for row in all_rows} == expected
    assert all_rows[0]['title'] == 'History-000'
    assert datetime.fromisoformat(all_rows[0]['last_activity_at'].replace('Z', '+00:00')) == activity
    assert [row['title'] for row in (await admin.get(endpoint, params={'search': 'History-001'})).json()] == [
        'History-001'
    ]
    assert (await admin.get(endpoint, params={'search': 'PRIVATE'})).json() == []
    assert [row['title'] for row in (await admin.get(endpoint, params={'search': '%_done\\'})).json()] == [
        literal.title
    ]
    assert (await admin.get(endpoint, params={'search': 'archived'})).json() == []
    assert len((await admin.get(endpoint, params={'archived': 'true', 'search': 'archived'})).json()) == 1
    page = await admin.get(endpoint, params={'limit': 1})
    cursor = page.headers['X-Next-Cursor']
    assert (await admin.get(endpoint, params={'cursor': cursor, 'search': 'different'})).status_code == 422
    assert (await admin.get(endpoint, params={'cursor': cursor, 'archived': 'true'})).status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as reader:
        await reader.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await reader.get(endpoint, params={'cursor': cursor})).status_code == 422
        assert (await reader.get(endpoint)).json()[0]['title'] == 'History-PRIVATE'


async def seed_messages(
    admin: httpx.AsyncClient, app: FastAPI, *, rounds: int, versions: int = 1
) -> tuple[str, set[str]]:
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    conversation = (await admin.post('/api/chat/conversations', json={'title': 'Long history'})).json()['id']
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ids = set()
    async with app.state.container.database.sessions.begin() as session:
        for index in range(rounds):
            run = Run(
                owner_id=owner,
                billing_owner_id=owner,
                resource_type='chat',
                resource_id=UUID(conversation),
                client_request_id=uuid4(),
                fingerprint='history-turn',
                payload={},
                status='completed',
            )
            session.add(run)
            await session.flush()
            question = ChatMessage(
                conversation_id=UUID(conversation),
                run_id=run.id,
                role='user',
                content=f'Question-{index:03}',
                created_at=stamp + timedelta(seconds=index),
                attachments=[
                    {
                        'file_id': str(uuid4()),
                        'original_filename': 'history.txt',
                        'mime': 'text/plain',
                        'size': 12,
                        'sha256': 'a' * 64,
                        'artifact_file_id': None,
                        'parser_version': None,
                    }
                ],
            )
            session.add(question)
            await session.flush()
            ids.add(str(question.id))
            for generation in range(1, versions + 1):
                if generation > 1:
                    run = Run(
                        owner_id=owner,
                        billing_owner_id=owner,
                        resource_type='chat',
                        resource_id=UUID(conversation),
                        client_request_id=uuid4(),
                        fingerprint='history-reply',
                        payload={},
                        status='completed',
                    )
                    session.add(run)
                    await session.flush()
                reply = ChatMessage(
                    conversation_id=UUID(conversation),
                    run_id=run.id,
                    role='assistant',
                    content=f'Answer-{index:03}-{generation}',
                    reply_to_message_id=question.id if generation > 1 or index % 2 else None,
                    generation_index=generation,
                    is_complete=True,
                    created_at=stamp + timedelta(seconds=index, microseconds=generation),
                )
                session.add(reply)
                await session.flush()
                ids.add(str(reply.id))
    return conversation, ids


@pytest.mark.parametrize('rounds,versions,limit', [(115, 1, 17), (3, 100, 100)])
async def test_message_paging_preserves_whole_rounds_versions_and_attachments(
    admin: httpx.AsyncClient, app: FastAPI, rounds: int, versions: int, limit: int
) -> None:
    conversation, expected = await seed_messages(admin, app, rounds=rounds, versions=versions)
    endpoint = f'/api/chat/conversations/{conversation}/messages'
    seen = []
    cursor = None
    while True:
        params = {'limit': limit}
        if cursor:
            params['cursor'] = cursor
        response = await admin.get(endpoint, params=params)
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) <= 200
        questions = [row for row in rows if row['role'] == 'user']
        assert 0 < len(questions) <= limit
        for question in questions:
            assert question['attachments'][0]['original_filename'] == 'history.txt'
            replies = [row for row in rows if row['reply_to_message_id'] == question['id']]
            assert len(replies) == versions
            assert {row['generation_index'] for row in replies} == set(range(1, versions + 1))
        seen.extend(row['id'] for row in rows)
        cursor = response.headers.get('X-Next-Cursor')
        if not cursor:
            break
    assert len(seen) == len(set(seen)) and set(seen) == expected
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as reader:
        await reader.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await reader.get(endpoint)).status_code == 404
    other = (await admin.post('/api/chat/conversations', json={})).json()['id']
    first = await admin.get(endpoint, params={'limit': 1})
    assert (
        await admin.get(f'/api/chat/conversations/{other}/messages', params={'cursor': first.headers['X-Next-Cursor']})
    ).status_code == 422


@pytest.mark.parametrize(
    'params', [{'limit': 0}, {'limit': 101}, {'search': 'a' * 201}, {'search': '\x00'}, {'cursor': 'invalid'}]
)
async def test_history_rejects_unbounded_or_invalid_requests(admin: httpx.AsyncClient, params: dict) -> None:
    assert (await admin.get('/api/chat/conversations', params=params)).status_code == 422
