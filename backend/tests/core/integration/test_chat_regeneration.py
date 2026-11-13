import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text, update

from tests.core.integration.test_chat_runs import admit
from tests.core.integration.test_knowledge import finish_preparation
from tests.core.integration.test_knowledge_failures import ready_base, source
from tests.core.integration.test_skills import install
from tests.core.provider import ProviderServer
from zhiheng.modules.chat.orm import ChatMessage
from zhiheng.runtime.orm import Run
from zhiheng.shared.principal import Principal
from zhiheng.worker.execution import execute_run


async def completed_round(admin: httpx.AsyncClient, app: FastAPI, model_id: str) -> tuple[str, list[dict]]:
    conversation_id, run = await admit(admin, model_id)
    await execute_run(app.state.container, UUID(run['id']))
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    return conversation_id, messages


@pytest.mark.parametrize('terminal_status', ['failed', 'cancelled', 'interrupted'])
async def test_unanswered_terminal_question_can_regenerate_without_duplicate_question_or_charge(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, terminal_status: str
) -> None:
    if terminal_status == 'failed':
        assert (await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'rejected'})).status_code == 200
    conversation_id, original_run = await admit(admin, model_id)
    if terminal_status == 'failed':
        await execute_run(app.state.container, UUID(original_run['id']))
        assert (await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'local-text'})).status_code == 200
    elif terminal_status == 'cancelled':
        assert (await admin.post(f'/api/runs/{original_run["id"]}/cancel', json={})).status_code == 200
    else:
        async with app.state.container.database.sessions.begin() as session:
            claim = await app.state.container.runs.claim(session, UUID(original_run['id']), 3)
            assert claim is not None
            await session.execute(
                update(Run).where(Run.id == claim.id).values(lease_until=text("now() - interval '1 second'"))
            )
        await app.state.container.run_recovery.recover_expired()
    failed = (await admin.get(f'/api/runs/{original_run["id"]}')).json()
    assert failed['status'] == terminal_status and failed['text'] == ''
    history = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert len(history) == 1 and history[0]['role'] == 'user'
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{history[0]["id"]}/regenerate'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.post(endpoint, json={'client_request_id': str(uuid4())})).status_code == 404
    key = str(uuid4())
    accepted = await admin.post(endpoint, json={'client_request_id': key})
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()['id']
    replay = await admin.post(endpoint, json={'client_request_id': key})
    assert replay.status_code == 202 and replay.json()['id'] == run_id
    await execute_run(app.state.container, UUID(run_id))
    replay = await admin.post(endpoint, json={'client_request_id': key})
    assert replay.status_code == 202 and replay.json()['id'] == run_id
    final = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert [row['role'] for row in final] == ['user', 'assistant']
    assert final[0]['id'] == history[0]['id'] and final[0]['run_id'] == original_run['id']
    assert final[1]['reply_to_message_id'] == history[0]['id'] and final[1]['run_id'] == run_id
    assert final[1]['is_complete'] is True and final[1]['generation_index'] == 2
    assert len(provider.requests) == (2 if terminal_status == 'failed' else 1)
    assert [row['role'] for row in provider.requests[-1]['messages']] == ['user']
    usage = (await admin.get('/api/usage')).json()
    assert len(usage['records']) == 2 and usage['reserved_tokens'] == 0
    assert sum(row['charged_tokens'] for row in usage['records']) == 10
    stale = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert stale.status_code == 409


@pytest.mark.parametrize('same_key', [False, True])
async def test_parallel_unanswered_regenerations_create_one_reply(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, same_key: bool
) -> None:
    conversation_id, run = await admit(admin, model_id)
    await admin.post(f'/api/runs/{run["id"]}/cancel', json={})
    question = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()[0]
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{question["id"]}/regenerate'
    key = str(uuid4())
    responses = await asyncio.gather(
        *(admin.post(endpoint, json={'client_request_id': key if same_key else str(uuid4())}) for _ in range(2))
    )
    assert sorted(response.status_code for response in responses) == ([202, 202] if same_key else [202, 409])
    rows = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert [row['role'] for row in rows] == ['user', 'assistant']
    assert rows[1]['reply_to_message_id'] == question['id']


async def test_user_target_cannot_bypass_active_run_latest_round_or_archive(
    admin: httpx.AsyncClient, model_id: str, provider: ProviderServer
) -> None:
    conversation_id, run = await admit(admin, model_id)
    question = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()[0]
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{question["id"]}/regenerate'
    assert (await admin.post(endpoint, json={'client_request_id': str(uuid4())})).status_code == 409
    await admin.post(f'/api/runs/{run["id"]}/cancel', json={})
    next_run = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={'model_id': model_id, 'message': 'new question', 'client_request_id': str(uuid4())},
    )
    assert next_run.status_code == 202
    await admin.post(f'/api/runs/{next_run.json()["id"]}/cancel', json={})
    assert (await admin.post(endpoint, json={'client_request_id': str(uuid4())})).status_code == 409
    latest = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()[-1]
    archived = await admin.patch(
        f'/api/chat/conversations/{conversation_id}', json={'expected_version': 1, 'archived': True}
    )
    assert archived.status_code == 200
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{latest["id"]}/regenerate'
    assert (await admin.post(endpoint, json={'client_request_id': str(uuid4())})).status_code == 409
    assert provider.requests == []


async def test_regeneration_keeps_versions_and_admits_one_new_governed_run(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    conversation_id, original = await completed_round(admin, app, model_id)
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    key = str(uuid4())
    response = await admin.post(endpoint, json={'client_request_id': key})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run['id'] != original[-1]['run_id']
    replay = await admin.post(endpoint, json={'client_request_id': key})
    assert replay.status_code == 202 and replay.json()['id'] == run['id']
    pending = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert [row['role'] for row in pending] == ['user', 'assistant', 'assistant']
    assert pending[-1]['run_id'] == run['id'] and pending[-1]['is_complete'] is False
    assert [row['generation_index'] for row in pending[1:]] == [1, 2]
    assert all(row['reply_to_message_id'] == original[0]['id'] for row in pending[1:])
    await execute_run(app.state.container, UUID(run['id']))
    final = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert final[1]['id'] == original[1]['id'] and final[1]['content'] == original[1]['content']
    assert final[2]['id'] == pending[2]['id'] and final[2]['is_complete'] is True
    assert len(provider.requests) == 2
    assert [row['role'] for row in provider.requests[-1]['messages']] == ['user']
    usage = (await admin.get('/api/usage')).json()
    assert len(usage['records']) == 2 and usage['reserved_tokens'] == 0


@pytest.mark.parametrize('same_key', [False, True])
async def test_parallel_regenerations_accept_only_one_request(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, same_key: bool
) -> None:
    conversation_id, original = await completed_round(admin, app, model_id)
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    key = str(uuid4())
    responses = await asyncio.gather(
        *(admin.post(endpoint, json={'client_request_id': key if same_key else str(uuid4())}) for _ in range(2))
    )
    assert sorted(response.status_code for response in responses) == ([202, 202] if same_key else [202, 409])
    if same_key:
        assert responses[0].json()['id'] == responses[1].json()['id']
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert len(messages) == 3


async def test_regeneration_rejects_old_round_and_next_context_uses_latest_complete_version(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    conversation_id, original = await completed_round(admin, app, model_id)
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    key = str(uuid4())
    regenerated = await admin.post(endpoint, json={'client_request_id': key})
    assert regenerated.status_code == 202, regenerated.text
    await execute_run(app.state.container, UUID(regenerated.json()['id']))
    sent = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={'model_id': model_id, 'message': '继续', 'client_request_id': str(uuid4())},
    )
    assert sent.status_code == 202, sent.text
    await execute_run(app.state.container, UUID(sent.json()['id']))
    assert [row['role'] for row in provider.requests[-1]['messages']] == ['user', 'assistant', 'user']
    rejected = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert rejected.status_code == 409
    replay = await admin.post(endpoint, json={'client_request_id': key})
    assert replay.status_code == 202 and replay.json()['id'] == regenerated.json()['id']


async def test_regeneration_requires_owner_and_unarchived_conversation(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    conversation_id, original = await completed_round(admin, app, model_id)
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.post(endpoint, json={'client_request_id': str(uuid4())})).status_code == 404
    archived = await admin.patch(
        f'/api/chat/conversations/{conversation_id}', json={'expected_version': 1, 'archived': True}
    )
    assert archived.status_code == 200, archived.text
    assert (await admin.post(endpoint, json={'client_request_id': str(uuid4())})).status_code == 409


@pytest.mark.parametrize('with_knowledge', [False, True])
async def test_archived_conversation_replays_admitted_regeneration_but_not_new_requests(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, with_knowledge: bool
) -> None:
    if with_knowledge:
        base = await ready_base(admin, app, provider)
        conversation_id = (await admin.post('/api/chat/conversations', json={})).json()['id']
        sent = await admin.post(
            f'/api/chat/conversations/{conversation_id}/runs',
            json={
                'model_id': model_id,
                'message': 'Which port?',
                'client_request_id': str(uuid4()),
                'knowledge_base_ids': [base['id']],
            },
        )
        assert sent.status_code == 202, sent.text
        prepared = await finish_preparation(admin, app, sent.json())
        await execute_run(app.state.container, UUID(prepared['run']['id']))
        original = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    else:
        conversation_id, original = await completed_round(admin, app, model_id)
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    key = str(uuid4())
    accepted = await admin.post(endpoint, json={'client_request_id': key})
    assert accepted.status_code == 202, accepted.text
    if with_knowledge:
        admitted = await finish_preparation(admin, app, accepted.json())
        run_id = admitted['run']['id']
    else:
        run_id = accepted.json()['id']
    await execute_run(app.state.container, UUID(run_id))
    usage_before = (await admin.get('/api/usage')).json()
    request_count = len(provider.requests)
    archived = await admin.patch(
        f'/api/chat/conversations/{conversation_id}', json={'expected_version': 1, 'archived': True}
    )
    assert archived.status_code == 200, archived.text
    replay = await admin.post(endpoint, json={'client_request_id': key})
    assert replay.status_code == 202, replay.text
    assert replay.json()['id'] == run_id and replay.json()['status'] == 'completed'
    new_request = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert new_request.status_code == 409 and new_request.json()['error']['code'] == 'conversation_archived'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.post(endpoint, json={'client_request_id': key})).status_code == 404
    deleted = await admin.request('DELETE', f'/api/chat/conversations/{conversation_id}', json={'expected_version': 2})
    assert deleted.status_code == 204, deleted.text
    assert (await admin.post(endpoint, json={'client_request_id': key})).status_code == 404
    assert len(provider.requests) == request_count
    usage_after = (await admin.get('/api/usage')).json()
    assert usage_after == usage_before


async def test_cancelled_regeneration_keeps_its_slot_and_can_regenerate_again(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    conversation_id, original = await completed_round(admin, app, model_id)
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    response = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert response.status_code == 202, response.text
    run_id = response.json()['id']
    cancelled = await admin.post(f'/api/runs/{run_id}/cancel', json={})
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert len(messages) == 3 and messages[-1]['is_complete'] is False
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{messages[-1]["id"]}/regenerate'
    retried = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert retried.status_code == 202, retried.text
    await execute_run(app.state.container, UUID(retried.json()['id']))
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert [row['generation_index'] for row in messages[1:]] == [1, 2, 3]


async def test_regeneration_preserves_skill_and_attachment_selections_and_rechecks_availability(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    skill = await install(admin, 'regen-instruction', 'REGENERATION INSTRUCTION')
    file = await source(admin, 'context.txt', b'attachment context for regeneration')
    conversation_id = (await admin.post('/api/chat/conversations', json={})).json()['id']
    sent = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': 'read it',
            'client_request_id': str(uuid4()),
            'skill_slugs': [skill['slug']],
            'file_ids': [file['id']],
        },
    )
    assert sent.status_code == 202, sent.text
    await execute_run(app.state.container, UUID(sent.json()['id']))
    original = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    regenerated = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert regenerated.status_code == 202, regenerated.text
    await execute_run(app.state.container, UUID(regenerated.json()['id']))
    assert provider.requests[-1]['messages'] == provider.requests[-2]['messages']
    references = (await admin.get(f'/api/files/{file["id"]}/references')).json()
    assert {row['resource_id'] for row in references if row['resource_type'] == 'chat'} == {
        sent.json()['id'],
        regenerated.json()['id'],
    }
    disabled = await admin.patch(
        f'/api/skills/{skill["id"]}/status', json={'status': 'disabled', 'expected_version': 1}
    )
    assert disabled.status_code == 200, disabled.text
    denied = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert denied.status_code == 422, denied.text
    assert len(provider.requests) == 2
    enabled = await admin.patch(f'/api/skills/{skill["id"]}/status', json={'status': 'enabled', 'expected_version': 2})
    assert enabled.status_code == 200, enabled.text
    current = (await admin.get(f'/api/files/{file["id"]}')).json()
    expired = await admin.patch(
        f'/api/files/{file["id"]}', json={'expected_version': current['version'], 'expires_at': '2020-01-01T00:00:00Z'}
    )
    assert expired.status_code == 200, expired.text
    inaccessible_file = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert inaccessible_file.status_code == 404, inaccessible_file.text
    assert len(provider.requests) == 2


async def test_failed_regeneration_keeps_partial_reply_and_preserves_completed_context(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    conversation_id, original = await completed_round(admin, app, model_id)
    changed = await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'truncated'})
    assert changed.status_code == 200, changed.text
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    response = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert response.status_code == 202, response.text
    await execute_run(app.state.container, UUID(response.json()['id']))
    failed = (await admin.get(f'/api/runs/{response.json()["id"]}')).json()
    assert failed['status'] == 'failed'
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert messages[-1]['content'] == 'half answer' and messages[-1]['is_complete'] is False
    assert messages[1]['content'] == original[1]['content'] and messages[1]['is_complete'] is True
    owner = Principal(UUID((await admin.get('/api/auth/me')).json()['id']), 'admin', True)
    async with app.state.container.database.sessions() as session:
        context = await app.state.container.chat.context(session, owner, UUID(conversation_id))
    assert [message.content for message in context] == [original[0]['content'], original[1]['content']]
    assert len(provider.requests) == 2


async def test_legacy_plain_request_context_is_recovered_without_duplicating_question(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    conversation_id, original = await completed_round(admin, app, model_id)
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            update(ChatMessage).where(ChatMessage.id == UUID(original[0]['id'])).values(request_context={})
        )
        await session.execute(
            update(ChatMessage).where(ChatMessage.id == UUID(original[1]['id'])).values(reply_to_message_id=None)
        )
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert messages[1]['reply_to_message_id'] == original[0]['id']
    response = await admin.post(
        f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate',
        json={'client_request_id': str(uuid4())},
    )
    assert response.status_code == 202, response.text


async def test_knowledge_regeneration_retrieves_again_and_keeps_original_provenance(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    conversation_id = (await admin.post('/api/chat/conversations', json={})).json()['id']
    sent = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': 'Which port?',
            'client_request_id': str(uuid4()),
            'knowledge_base_ids': [base['id']],
        },
    )
    assert sent.status_code == 202, sent.text
    prepared = await finish_preparation(admin, app, sent.json())
    await execute_run(app.state.container, UUID(prepared['run']['id']))
    original = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert original[-1]['knowledge_sources']
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    key = str(uuid4())
    regenerated = await admin.post(endpoint, json={'client_request_id': key})
    assert regenerated.status_code == 202 and regenerated.json()['kind'] == 'knowledge_preparation', regenerated.text
    replay = await admin.post(endpoint, json={'client_request_id': key})
    assert replay.json()['id'] == regenerated.json()['id']
    rejected = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert rejected.status_code == 409
    admitted = await finish_preparation(admin, app, regenerated.json())
    await execute_run(app.state.container, UUID(admitted['run']['id']))
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert len(messages) == 3 and messages[-1]['knowledge_sources'] == original[-1]['knowledge_sources']
    assert messages[-1]['run_id'] != original[-1]['run_id']
    replay = await admin.post(endpoint, json={'client_request_id': key})
    assert replay.json()['id'] == admitted['run']['id']
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            update(ChatMessage).where(ChatMessage.id == UUID(original[0]['id'])).values(request_context={})
        )
    incomplete = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert incomplete.status_code == 409
    assert incomplete.json()['error']['code'] == 'regeneration_context_unavailable'


async def seed_historical_versions(app: FastAPI, conversation_id: str, question: dict, *, count: int) -> None:
    """Build historical rows for read/limit boundaries; admission is tested separately."""
    async with app.state.container.database.sessions.begin() as session:
        original = await session.get(Run, UUID(question['run_id']))
        assert original is not None
        runs = [
            Run(
                id=uuid4(),
                owner_id=original.owner_id,
                billing_owner_id=original.billing_owner_id,
                resource_type='chat',
                resource_id=UUID(conversation_id),
                client_request_id=uuid4(),
                fingerprint=f'history-fixture-{index}',
                payload=original.payload,
                status='completed',
                text=f'version {index}',
            )
            for index in range(2, count + 1)
        ]
        session.add_all(runs)
        await session.flush()
        session.add_all(
            [
                ChatMessage(
                    conversation_id=UUID(conversation_id),
                    run_id=run.id,
                    role='assistant',
                    content=run.text,
                    reply_to_message_id=UUID(question['id']),
                    generation_index=index,
                    is_complete=True,
                )
                for index, run in enumerate(runs, start=2)
            ]
        )


async def test_version_limit_counts_original_and_history_window_keeps_whole_rounds(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    conversation_id, original = await completed_round(admin, app, model_id)
    await seed_historical_versions(app, conversation_id, original[0], count=99)
    endpoint = f'/api/chat/conversations/{conversation_id}/messages/{original[-1]["id"]}/regenerate'
    key = str(uuid4())
    last = await admin.post(endpoint, json={'client_request_id': key})
    assert last.status_code == 202, last.text
    replay = await admin.post(endpoint, json={'client_request_id': key})
    assert replay.status_code == 202 and replay.json()['id'] == last.json()['id']
    cancelled = await admin.post(f'/api/runs/{last.json()["id"]}/cancel', json={})
    assert cancelled.status_code == 200, cancelled.text
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert len(messages) == 101 and messages[0]['id'] == original[0]['id']
    rejected = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'regeneration_limit_reached'
    sent = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={'model_id': model_id, 'message': 'second question', 'client_request_id': str(uuid4())},
    )
    assert sent.status_code == 202, sent.text
    await execute_run(app.state.container, UUID(sent.json()['id']))
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    question = next(row for row in messages if row['run_id'] == sent.json()['id'] and row['role'] == 'user')
    await seed_historical_versions(app, conversation_id, question, count=100)
    bounded = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert len(bounded) == 101 and bounded[0]['id'] == question['id']
    assert {row['reply_to_message_id'] for row in bounded[1:]} == {question['id']}
    assert {row['generation_index'] for row in bounded[1:]} == set(range(1, 101))
    async with app.state.container.database.sessions() as session:
        count = await session.scalar(
            select(func.count(ChatMessage.id)).where(ChatMessage.conversation_id == UUID(conversation_id))
        )
    assert count == 202
