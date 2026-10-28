import asyncio
import hashlib
import re
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, func, select, text, update

from zhiheng.modules.chat.orm import ChatMessage
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow


@pytest_asyncio.fixture
async def saved_chat(admin: httpx.AsyncClient, app: FastAPI) -> dict[str, str]:
    conversation = (await admin.post('/api/chat/conversations', json={'title': '分享测试'})).json()
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    run_id, question_id, answer_id = uuid4(), uuid4(), uuid4()
    async with app.state.container.database.sessions.begin() as session:
        session.add(
            Run(
                id=run_id,
                owner_id=owner_id,
                billing_owner_id=owner_id,
                resource_type='chat',
                resource_id=UUID(conversation['id']),
                client_request_id=uuid4(),
                fingerprint='a' * 64,
                payload={},
                status='completed',
            )
        )
        await session.flush()
        session.add(
            ChatMessage(
                id=question_id,
                conversation_id=UUID(conversation['id']),
                run_id=run_id,
                role='user',
                content='原始问题',
                is_complete=True,
            )
        )
        await session.flush()
        session.add(
            ChatMessage(
                id=answer_id,
                conversation_id=UUID(conversation['id']),
                run_id=run_id,
                reply_to_message_id=question_id,
                role='assistant',
                content='公开回答 **Markdown**',
                reasoning='PRIVATE REASONING',
                is_complete=True,
            )
        )
    return {
        'conversation_id': conversation['id'],
        'message_id': str(answer_id),
        'question_id': str(question_id),
        'owner_id': str(owner_id),
        'run_id': str(run_id),
    }


async def preview(
    client: httpx.AsyncClient, saved_chat: dict[str, str], *, include_question: bool = True
) -> dict[str, object]:
    response = await client.get(
        f'/api/chat/messages/{saved_chat["message_id"]}/share-preview',
        params={'include_question': str(include_question).lower()},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def create_share(
    client: httpx.AsyncClient, saved_chat: dict[str, str], *, include_question: bool = True
) -> dict[str, object]:
    snapshot = await preview(client, saved_chat, include_question=include_question)
    response = await client.post(
        f'/api/chat/messages/{saved_chat["message_id"]}/shares',
        json={
            'include_question': include_question,
            'expires_in_days': 7,
            'expected_content_hash': snapshot['content_hash'],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_feedback_is_absolute_mutually_exclusive_and_clearable(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    from zhiheng.modules.chat.actions.orm import MessageFeedback

    path = f'/api/chat/messages/{saved_chat["message_id"]}/feedback'
    for value in ('up', 'up', 'down', None, None):
        response = await admin.put(path, json={'value': value})
        assert response.status_code == 200, response.text
        assert response.json() == {'message_id': saved_chat['message_id'], 'value': value}
        listed = await admin.get(f'/api/chat/conversations/{saved_chat["conversation_id"]}/feedback')
        assert listed.status_code == 200, listed.text
        expected = [] if value is None else [response.json()]
        assert listed.json() == expected
        messages = (await admin.get(f'/api/chat/conversations/{saved_chat["conversation_id"]}/messages')).json()
        assert next(item for item in messages if item['id'] == saved_chat['message_id'])['feedback'] == value
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(MessageFeedback)) == 0


async def test_feedback_parallel_updates_have_one_row_and_no_duplicates(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    from zhiheng.modules.chat.actions.orm import MessageFeedback

    path = f'/api/chat/messages/{saved_chat["message_id"]}/feedback'
    responses = await asyncio.wait_for(
        asyncio.gather(*(admin.put(path, json={'value': 'up'}) for _ in range(8))), timeout=15
    )
    assert [response.status_code for response in responses] == [200] * 8
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(MessageFeedback)) == 1


async def test_other_member_and_other_admin_cannot_operate_on_owned_messages(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    from zhiheng.shared.principal import Principal

    container = app.state.container
    async with container.database.sessions.begin() as session:
        await container.identity.create_user(
            session, username='other-admin', password='platform-test-password', is_admin=True
        )
    for username in ('member', 'other-admin'):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as other:
            login = await other.post(
                '/api/auth/login', json={'username': username, 'password': 'platform-test-password'}
            )
            assert login.status_code == 200
            message = saved_chat['message_id']
            assert (await other.put(f'/api/chat/messages/{message}/feedback', json={'value': 'up'})).status_code == 404
            assert (await other.get(f'/api/chat/messages/{message}/share-preview')).status_code == 404
            assert (await other.get(f'/api/chat/messages/{message}/shares')).status_code == 404
            assert (
                await other.post(f'/api/chat/messages/{message}/shares', json={'expected_content_hash': 'a' * 64})
            ).status_code == 404
            assert (
                await other.get(f'/api/chat/conversations/{saved_chat["conversation_id"]}/feedback')
            ).status_code == 404
    async with container.database.sessions() as session:
        principal = Principal(UUID(saved_chat['owner_id']), '', True)
        values = await container.message_actions.feedback_for_messages(
            session, principal, conversation_id=UUID(saved_chat['conversation_id']), message_ids=[uuid4()]
        )
        assert values == {}


async def test_sharing_preview_is_exact_and_snapshot_is_frozen(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    from zhiheng.modules.chat.actions.orm import MessageShare

    snapshot = await preview(admin, saved_chat)
    assert set(snapshot) == {'message_id', 'question', 'answer', 'content_hash'}
    assert snapshot['question'] == '原始问题' and snapshot['answer'] == '公开回答 **Markdown**'
    share = await create_share(admin, saved_chat)
    assert re.fullmatch(r'[A-Za-z0-9_-]{43}', share['token'])
    assert set(share) == {'id', 'message_id', 'token', 'created_at', 'expires_at', 'revoked_at'}
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(MessageShare, UUID(share['id']))
        assert row.token_hash == hashlib.sha256(share['token'].encode()).hexdigest()
        assert share['token'] not in row.token_hash and row.content_hash == snapshot['content_hash']
        await session.execute(
            update(ChatMessage).where(ChatMessage.id == UUID(saved_chat['message_id'])).values(content='修改后的回答')
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        result = await anonymous.get(f'/api/shared/messages/{share["token"]}')
        assert result.status_code == 200, result.text
        assert set(result.json()) == {'question', 'answer', 'created_at', 'expires_at'}
        assert result.json()['question'] == snapshot['question'] and result.json()['answer'] == snapshot['answer']
        assert result.headers['cache-control'] == 'no-store'
        assert result.headers['x-robots-tag'] == 'noindex'
        assert 'PRIVATE' not in result.text and saved_chat['owner_id'] not in result.text
    listed = await admin.get(f'/api/chat/messages/{saved_chat["message_id"]}/shares')
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert 'token' not in listed.text and 'question' not in listed.text and 'answer' not in listed.text


async def test_changed_content_or_question_choice_requires_new_preview(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    original = await preview(admin, saved_chat)
    path = f'/api/chat/messages/{saved_chat["message_id"]}/shares'
    changed_choice = await admin.post(
        path, json={'include_question': False, 'expected_content_hash': original['content_hash']}
    )
    assert changed_choice.status_code == 409
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            update(ChatMessage).where(ChatMessage.id == UUID(saved_chat['question_id'])).values(content='问题已变更')
        )
    changed = await admin.post(path, json={'expected_content_hash': original['content_hash']})
    assert changed.status_code == 409
    assert (await admin.get(path)).json() == []
    omitted = await preview(admin, saved_chat, include_question=False)
    assert omitted['question'] is None
    share = await create_share(admin, saved_chat, include_question=False)
    assert (await admin.get(f'/api/shared/messages/{share["token"]}')).json()['question'] is None


async def test_original_question_reference_and_legacy_same_run_fallback(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    container = app.state.container
    regenerated_run_id = uuid4()
    async with container.database.sessions.begin() as session:
        owner_id = UUID(saved_chat['owner_id'])
        session.add(
            Run(
                id=regenerated_run_id,
                owner_id=owner_id,
                billing_owner_id=owner_id,
                resource_type='chat',
                resource_id=UUID(saved_chat['conversation_id']),
                client_request_id=uuid4(),
                fingerprint='b' * 64,
                payload={},
                status='completed',
            )
        )
        await session.flush()
        session.add(
            ChatMessage(
                conversation_id=UUID(saved_chat['conversation_id']),
                run_id=regenerated_run_id,
                role='user',
                content='同一运行的旧记录问题',
                is_complete=True,
            )
        )
        answer = await session.get(ChatMessage, UUID(saved_chat['message_id']))
        answer.run_id = regenerated_run_id
    assert (await preview(admin, saved_chat))['question'] == '原始问题'
    async with container.database.sessions.begin() as session:
        answer = await session.get(ChatMessage, UUID(saved_chat['message_id']))
        answer.reply_to_message_id = None
    assert (await preview(admin, saved_chat))['question'] == '同一运行的旧记录问题'


async def test_feedback_bulk_lookup_has_constant_queries_and_filters_requested_ids(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    from zhiheng.shared.principal import Principal

    container = app.state.container
    message_id = UUID(saved_chat['message_id'])
    principal = Principal(UUID(saved_chat['owner_id']), '', True)
    await admin.put(f'/api/chat/messages/{message_id}/feedback', json={'value': 'down'})
    statements: list[str] = []

    def record(
        connection: object, cursor: object, statement: str, parameters: object, context: object, executemany: bool
    ) -> None:
        statements.append(statement)

    event.listen(container.database.engine.sync_engine, 'before_cursor_execute', record)
    try:
        async with container.database.sessions() as session:
            result = await container.message_actions.feedback_for_messages(
                session,
                principal,
                conversation_id=UUID(saved_chat['conversation_id']),
                message_ids=[message_id, *[uuid4() for _ in range(200)]],
            )
            assert result == {message_id: 'down'}
            assert len(statements) == 2
    finally:
        event.remove(container.database.engine.sync_engine, 'before_cursor_execute', record)


@pytest.mark.parametrize('unavailable', ['revoked', 'expired', 'deleted', 'disabled'])
async def test_unavailable_shares_always_return_same_404(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI, unavailable: str
) -> None:
    from zhiheng.modules.chat.actions.orm import MessageShare

    share = await create_share(admin, saved_chat)
    if unavailable == 'revoked':
        for _ in range(2):
            response = await admin.request('DELETE', f'/api/chat/shares/{share["id"]}', json={})
            assert response.status_code == 204, response.text
    elif unavailable == 'deleted':
        response = await admin.request(
            'DELETE', f'/api/chat/conversations/{saved_chat["conversation_id"]}', json={'expected_version': 1}
        )
        assert response.status_code == 204, response.text
    else:
        async with app.state.container.database.sessions.begin() as session:
            if unavailable == 'expired':
                await session.execute(
                    update(MessageShare)
                    .where(MessageShare.id == UUID(share['id']))
                    .values(created_at=utcnow() - timedelta(days=8), expires_at=utcnow() - timedelta(seconds=1))
                )
            else:
                await session.execute(
                    text('UPDATE identity_users SET is_active = false WHERE id = :owner_id'),
                    {'owner_id': UUID(saved_chat['owner_id'])},
                )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        actual = await anonymous.get(f'/api/shared/messages/{share["token"]}')
        unknown = await anonymous.get('/api/shared/messages/' + 'a' * 43)
        malformed = await anonymous.get('/api/shared/messages/invalid-token')
        assert actual.status_code == unknown.status_code == malformed.status_code == 404
        assert actual.json() == unknown.json() == malformed.json()
        for result in (actual, unknown, malformed):
            assert result.headers['cache-control'] == 'no-store'
            assert result.headers['x-robots-tag'] == 'noindex'


async def test_unowned_share_cannot_be_revoked(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    share = await create_share(admin, saved_chat)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.request('DELETE', f'/api/chat/shares/{share["id"]}', json={})).status_code == 404
    assert (await admin.get(f'/api/shared/messages/{share["token"]}')).status_code == 200


async def test_user_messages_and_empty_assistant_cannot_be_shared(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    question = await admin.get(f'/api/chat/messages/{saved_chat["question_id"]}/share-preview')
    assert question.status_code == 422
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            update(ChatMessage).where(ChatMessage.id == UUID(saved_chat['message_id'])).values(content=' \n\t')
        )
    assert (await admin.get(f'/api/chat/messages/{saved_chat["message_id"]}/share-preview')).status_code == 422


async def test_invalid_action_requests_and_unauthenticated_writes_are_rejected(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    message = saved_chat['message_id']
    for body in ({}, {'value': 'like'}, {'value': 'up', 'owner_id': saved_chat['owner_id']}):
        assert (await admin.put(f'/api/chat/messages/{message}/feedback', json=body)).status_code == 422
    for body in ({}, {'expected_content_hash': 'x' * 64}, {'expected_content_hash': 'a' * 64, 'expires_in_days': 31}):
        assert (await admin.post(f'/api/chat/messages/{message}/shares', json=body)).status_code == 422
    assert (await admin.get('/api/chat/messages/not-a-uuid/share-preview')).status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        assert (await anonymous.put(f'/api/chat/messages/{message}/feedback', json={'value': 'up'})).status_code == 401
        assert (
            await anonymous.post(f'/api/chat/messages/{message}/shares', json={'expected_content_hash': 'a' * 64})
        ).status_code == 401


async def test_malformed_share_tokens_have_uniform_404_and_no_cache(app: FastAPI) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        baseline = await anonymous.get('/api/shared/messages/' + 'a' * 43)
        for token in ('a' * 42, 'a' * 44, 'a' * 42 + '!', 'a' * 42 + '中', 'a' * 20 + '/' + 'a' * 22, ''):
            response = await anonymous.get('/api/shared/messages/' + token)
            assert response.status_code == 404
            assert response.json() == baseline.json()
            assert response.headers['cache-control'] == 'no-store'
            assert response.headers['x-robots-tag'] == 'noindex'


async def test_transaction_owner_can_rollback_feedback_and_share(
    admin: httpx.AsyncClient, saved_chat: dict[str, str], app: FastAPI
) -> None:
    from zhiheng.modules.chat.actions.orm import MessageFeedback, MessageShare
    from zhiheng.shared.principal import Principal

    container = app.state.container
    principal = Principal(UUID(saved_chat['owner_id']), '', True)
    message_id = UUID(saved_chat['message_id'])
    with pytest.raises(RuntimeError, match='rollback requested'):
        async with container.database.sessions.begin() as session:
            await container.message_actions.set_feedback(session, principal, message_id, value='up')
            snapshot = await container.message_actions.share_preview(session, principal, message_id)
            await container.message_actions.create_share(
                session,
                principal,
                message_id,
                include_question=True,
                expires_in_days=7,
                expected_content_hash=snapshot.content_hash,
            )
            raise RuntimeError('rollback requested')
    async with container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(MessageFeedback)) == 0
        assert await session.scalar(select(func.count()).select_from(MessageShare)) == 0


async def test_delete_and_actions_complete_without_deadlocks(
    admin: httpx.AsyncClient, saved_chat: dict[str, str]
) -> None:
    snapshot = await preview(admin, saved_chat)
    share = await create_share(admin, saved_chat)
    results = await asyncio.wait_for(
        asyncio.gather(
            admin.put(f'/api/chat/messages/{saved_chat["message_id"]}/feedback', json={'value': 'up'}),
            admin.post(
                f'/api/chat/messages/{saved_chat["message_id"]}/shares',
                json={'expected_content_hash': snapshot['content_hash']},
            ),
            admin.request('DELETE', f'/api/chat/shares/{share["id"]}', json={}),
            admin.request(
                'DELETE', f'/api/chat/conversations/{saved_chat["conversation_id"]}', json={'expected_version': 1}
            ),
        ),
        timeout=15,
    )
    assert all(result.status_code in {200, 201, 204, 404} for result in results)
    assert (await admin.get(f'/api/shared/messages/{share["token"]}')).status_code == 404
