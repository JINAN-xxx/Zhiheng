import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from tests.core.integration.test_public_protocol import drive, provision
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


def config(model_id: str, **changes: object) -> dict[str, object]:
    return {'name': '研究助手', 'model_id': model_id, 'max_tokens': 32, **changes}


@pytest.mark.asyncio
async def test_agent_lifecycle_isolates_publication_and_protects_model(admin: httpx.AsyncClient, model_id: str) -> None:
    application, _ = await provision(admin, model_id)
    created = await admin.post('/api/agents', json=config(model_id, application_id=application))
    assert created.status_code == 201, created.text
    agent = created.json()
    assert agent['version'] == 1 and agent['published_version'] is None
    published = await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})
    assert published.status_code == 200, published.text
    assert published.json()['published_version'] == 1
    edited = await admin.patch(f'/api/agents/{agent["id"]}', json=config(model_id, name='草稿', expected_version=2))
    assert edited.status_code == 200, edited.text
    assert edited.json()['published_config']['name'] == '研究助手'
    assert (await admin.post(f'/api/agents/{agent["id"]}/unpublish', json={'expected_version': 2})).status_code == 409
    assert (
        await admin.delete(f'/api/agents/{agent["id"]}', headers={'Content-Type': 'application/json'})
    ).status_code == 200
    assert (await admin.get(f'/api/agents/{agent["id"]}')).status_code == 404
    assert (
        await admin.delete(f'/api/models/{model_id}', headers={'Content-Type': 'application/json'})
    ).status_code == 409


@pytest.mark.asyncio
async def test_preview_freezes_context_and_idempotency(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    agent = (
        await admin.post(
            '/api/agents',
            json=config(
                model_id,
                system_prompt='SYSTEM',
                welcome_message='NEVER_SEND',
                skill_slugs=['document-summary'],
                temperature=0.25,
            ),
        )
    ).json()
    request = {
        'client_request_id': str(uuid4()),
        'messages': [{'role': 'assistant', 'content': 'history'}, {'role': 'user', 'content': 'question'}],
    }
    admitted = await admin.post(f'/api/agents/{agent["id"]}/preview', json=request)
    assert admitted.status_code == 202, admitted.text
    assert admitted.json()['resource_type'] == 'agent_preview'
    assert (await admin.post(f'/api/agents/{agent["id"]}/preview', json=request)).json()['id'] == admitted.json()['id']
    assert (
        await admin.post(
            f'/api/agents/{agent["id"]}/preview', json={**request, 'messages': [{'role': 'user', 'content': 'changed'}]}
        )
    ).status_code == 409
    assert (
        await admin.patch(f'/api/agents/{agent["id"]}', json=config(model_id, expected_version=1, system_prompt='NEW'))
    ).status_code == 200
    await execute_run(app.state.container, UUID(admitted.json()['id']))
    wire = provider.requests[-1]
    assert wire['messages'][0]['content'] == 'SYSTEM'
    assert '文档总结' in wire['messages'][1]['content']
    assert wire['messages'][2:] == [
        {'role': 'assistant', 'content': 'history'},
        {'role': 'user', 'content': 'question'},
    ]
    assert wire['temperature'] == 0.25 and wire['max_completion_tokens'] == 32
    assert 'NEVER_SEND' not in str(wire)
    result = await admin.get(f'/api/runs/{admitted.json()["id"]}')
    assert result.json()['text'] == '你好' and result.json()['reasoning'] == ''


@pytest.mark.asyncio
async def test_public_agent_uses_publication_and_hides_binding(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    application, key = await provision(admin, model_id)
    agent = (
        await admin.post('/api/agents', json=config(model_id, application_id=application, system_prompt='PUBLISHED'))
    ).json()
    endpoint = f'/v1/agents/{agent["id"]}/chat/completions'
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    body = {'messages': [{'role': 'user', 'content': 'hello'}]}
    assert (await admin.post(endpoint, headers=headers, json=body)).status_code == 404
    assert (await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})).status_code == 200
    assert (
        await admin.patch(
            f'/api/agents/{agent["id"]}', json=config(model_id, expected_version=2, system_prompt='DRAFT')
        )
    ).status_code == 200
    response = await drive(app.state.container, asyncio.create_task(admin.post(endpoint, headers=headers, json=body)))
    assert response.status_code == 200, response.text
    assert response.json()['choices'][0]['message']['content'] == '你好'
    assert provider.requests[-1]['messages'][0]['content'] == 'PUBLISHED'
    async with app.state.container.database.sessions() as session:
        run = await session.scalar(select(Run).where(Run.resource_type == 'agent_invocation'))
        assert str(run.resource_id) == agent['id']
    _, other = await provision(admin, model_id)
    assert (
        await admin.post(endpoint, headers={'Authorization': 'Bearer ' + other['api_key']}, json=body)
    ).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'changes',
    [
        {'name': '\x00'},
        {'system_prompt': '\ud800'},
        {'temperature': True},
        {'temperature': float('inf')},
        {'max_tokens': True},
        {'max_tokens': 65},
        {'history_rounds': True},
        {'history_rounds': 1.0},
        {'history_rounds': -1},
        {'history_rounds': 21},
        {'skill_slugs': ['document-summary'] * 2},
    ],
)
async def test_agent_strict_validation(admin: httpx.AsyncClient, model_id: str, changes: dict[str, object]) -> None:
    import json

    result = await admin.post(
        '/api/agents', content=json.dumps(config(model_id, **changes)), headers={'Content-Type': 'application/json'}
    )
    assert result.status_code == 422, result.text


@pytest.mark.asyncio
async def test_knowledge_fails_explicitly_and_owner_is_hidden(admin: httpx.AsyncClient, model_id: str) -> None:
    response = await admin.post('/api/agents', json=config(model_id, knowledge_base_ids=[str(uuid4())]))
    assert response.status_code == 404 and response.json()['error']['code'] == 'knowledge_not_found'
    agent = (await admin.post('/api/agents', json=config(model_id))).json()
    await admin.post('/api/auth/logout')
    await admin.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
    assert (await admin.get(f'/api/agents/{agent["id"]}')).status_code == 404


@pytest.mark.asyncio
async def test_model_restores_131072_boundary(admin: httpx.AsyncClient, model_id: str) -> None:
    assert (await admin.patch(f'/api/models/{model_id}', json={'max_output_tokens': 131072})).status_code == 200
    assert (await admin.patch(f'/api/models/{model_id}', json={'max_output_tokens': 131073})).status_code == 422
    assert (await admin.patch(f'/api/models/{model_id}', json={'max_output_tokens': True})).status_code == 422


@pytest.mark.asyncio
async def test_reasoning_projects_before_content_and_survives_cancel(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'reasoning-silent'})
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    response = await admin.post(
        f'/api/chat/conversations/{conversation["id"]}/runs',
        json={'model_id': model_id, 'message': 'q', 'client_request_id': str(uuid4())},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()['id']
    task = asyncio.create_task(execute_run(app.state.container, UUID(run_id)))
    for _ in range(100):
        snapshot = (await admin.get(f'/api/runs/{run_id}')).json()
        if snapshot.get('reasoning'):
            break
        await asyncio.sleep(0.02)
    assert snapshot.get('reasoning') == '先思考', snapshot
    assert snapshot['text'] == ''
    assert (await admin.post(f'/api/runs/{run_id}/cancel', json={})).status_code == 200
    await asyncio.wait_for(task, 2)
    final = (await admin.get(f'/api/runs/{run_id}')).json()
    assert final['status'] == 'cancelled' and final['reasoning'] == '先思考'
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_admin_uses_original_owner_for_application_and_preview_billing(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    from zhiheng.modules.agents.orm import AgentRunAudit
    from zhiheng.modules.developer.orm import Invocation

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        member_app = (
            await member.post('/api/developer/applications', json={'name': 'Owned', 'code': uuid4().hex})
        ).json()
        agent = (await member.post('/api/agents', json=config(model_id, application_id=member_app['id']))).json()
        admin_app, _ = await provision(admin, model_id)
        assert (
            await admin.patch(
                f'/api/agents/{agent["id"]}', json=config(model_id, application_id=admin_app, expected_version=1)
            )
        ).status_code == 404
        assert (await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})).status_code == 200
        request = {'client_request_id': str(uuid4()), 'messages': [{'role': 'user', 'content': 'admin preview'}]}
        preview = await admin.post(f'/api/agents/{agent["id"]}/preview', json=request)
        assert preview.status_code == 202, preview.text
        await execute_run(app.state.container, UUID(preview.json()['id']))
        result = (await admin.get(f'/api/runs/{preview.json()["id"]}')).json()
        assert result['status'] == 'completed', result
        assert (await member.get(f'/api/runs/{preview.json()["id"]}')).status_code == 404
        async with app.state.container.database.sessions() as session:
            invocation = await session.scalar(select(Invocation).where(Invocation.run_id == UUID(preview.json()['id'])))
            run = await session.get(Run, UUID(preview.json()['id']))
            audit = await session.get(AgentRunAudit, UUID(preview.json()['id']))
            assert str(invocation.user_id) == agent['owner_id'] == str(audit.owner_id) == str(run.billing_owner_id)
            assert run.owner_id != invocation.user_id


@pytest.mark.asyncio
async def test_agent_versions_immutable_republish_and_history_references(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    from tests.core.integration.test_skills import install
    from zhiheng.modules.agents.orm import AgentVersion

    skill = await install(admin, 'bound-agent')
    application, _ = await provision(admin, model_id)
    agent = (
        await admin.post('/api/agents', json=config(model_id, application_id=application, skill_slugs=[skill['slug']]))
    ).json()
    for version, publication in [(1, 1), (2, 2)]:
        response = await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': version})
        assert response.status_code == 200 and response.json()['published_version'] == publication
    await admin.patch(f'/api/agents/{agent["id"]}', json=config(model_id, expected_version=3))
    await admin.post(f'/api/agents/{agent["id"]}/unpublish', json={'expected_version': 4})
    await admin.delete(f'/api/agents/{agent["id"]}', headers={'content-type': 'application/json'})
    await admin.patch(f'/api/skills/{skill["id"]}/status', json={'status': 'disabled', 'expected_version': 1})
    response = await admin.delete(f'/api/skills/{skill["id"]}', headers={'content-type': 'application/json'})
    assert response.status_code == 200, response.text
    async with app.state.container.database.sessions.begin() as session:
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    update(AgentVersion).where(AgentVersion.agent_id == UUID(agent['id'])).values(config={})
                )


@pytest.mark.asyncio
async def test_model_delete_and_agent_create_are_serialized(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    container = app.state.container
    # Force delete to hold the model lock while the independent create tries to bind it.
    async with container.database.sessions.begin() as session:
        principal = await container.identity.authenticate(session, admin.cookies['zhiheng_session'])
        await container.model_management.delete_model(session, principal, UUID(model_id))
        task = asyncio.create_task(admin.post('/api/agents', json=config(model_id)))
        await asyncio.sleep(0.05)
        assert not task.done()
    result = await asyncio.wait_for(task, 2)
    assert result.status_code == 404, result.text
    assert (await admin.get('/api/agents')).json()['total'] == 0


@pytest.mark.asyncio
async def test_publish_concurrent_with_model_disable_is_serialized(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    application, _ = await provision(admin, model_id)
    agent = (await admin.post('/api/agents', json=config(model_id, application_id=application))).json()
    container = app.state.container
    async with container.database.sessions.begin() as session:
        principal = await container.identity.authenticate(session, admin.cookies['zhiheng_session'])
        await container.models.update_model(session, principal, UUID(model_id), {'enabled': False})
        task = asyncio.create_task(admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1}))
        await asyncio.sleep(0.05)
        assert not task.done()
    assert (await asyncio.wait_for(task, 2)).status_code == 404
    assert (await admin.get(f'/api/agents/{agent["id"]}')).json()['published_version'] is None


@pytest.mark.asyncio
async def test_public_agent_reasoning_json_sse_and_revocation(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'reasoning'})
    application, key = await provision(admin, model_id)
    agent = (await admin.post('/api/agents', json=config(model_id, application_id=application))).json()
    await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})
    endpoint = f'/v1/agents/{agent["id"]}/chat/completions'
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    for stream in (False, True):
        response = await drive(
            app.state.container,
            asyncio.create_task(
                admin.post(
                    endpoint, headers=headers, json={'messages': [{'role': 'user', 'content': 'q'}], 'stream': stream}
                )
            ),
        )
        assert response.status_code == 200, response.text
        if stream:
            import json

            chunks = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
            assert ''.join(chunk['choices'][0]['delta'].get('reasoning_content', '') for chunk in chunks) == '先思考'
            assert response.text.endswith('data: [DONE]\n\n')
        else:
            assert response.json()['choices'][0]['message']['reasoning_content'] == '先思考'
    await admin.post(f'/api/agents/{agent["id"]}/unpublish', json={'expected_version': 2})
    assert (
        await admin.post(endpoint, headers=headers, json={'messages': [{'role': 'user', 'content': 'q'}]})
    ).status_code == 404


@pytest.mark.asyncio
async def test_public_chat_cannot_exceed_selected_model(admin: httpx.AsyncClient, model_id: str, app: object) -> None:
    _, key = await provision(admin, model_id)
    result = await drive(
        app.state.container,
        asyncio.create_task(
            admin.post(
                '/v1/chat/completions',
                headers={'Authorization': 'Bearer ' + key['api_key']},
                json={'model': model_id, 'messages': [{'role': 'user', 'content': 'q'}], 'max_tokens': 65},
            )
        ),
    )
    assert result.status_code == 400, result.text


@pytest.mark.asyncio
async def test_hidden_public_agent_errors_are_identical(admin: httpx.AsyncClient, model_id: str) -> None:
    application, key = await provision(admin, model_id)
    agent = (await admin.post('/api/agents', json=config(model_id, application_id=application))).json()
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    body = {'messages': [{'role': 'user', 'content': 'q'}]}
    missing = await admin.post(f'/v1/agents/{uuid4()}/chat/completions', headers=headers, json=body)
    unpublished = await admin.post(f'/v1/agents/{agent["id"]}/chat/completions', headers=headers, json=body)
    assert missing.status_code == unpublished.status_code == 404
    assert missing.json() == unpublished.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['failed', 'interrupted', 'overflow'])
async def test_reasoning_partial_terminal_results_keep_bounded_utf8(
    admin: httpx.AsyncClient, model_id: str, app: object, outcome: str
) -> None:
    from collections.abc import AsyncIterator
    from datetime import timedelta

    from zhiheng.application.inference import InferenceExecutor
    from zhiheng.modules.models.public import ModelBinding, ModelDelta, ModelMessage, ProviderError
    from zhiheng.shared.time import utcnow

    container = app.state.container
    agent = (await admin.post('/api/agents', json=config(model_id))).json()
    preview = (
        await admin.post(
            f'/api/agents/{agent["id"]}/preview',
            json={'client_request_id': str(uuid4()), 'messages': [{'role': 'user', 'content': 'q'}]},
        )
    ).json()
    run_id = UUID(preview['id'])
    if outcome == 'interrupted':
        async with container.database.sessions.begin() as session:
            claim = await container.runs.claim(session, run_id, 3)
            await container.runs.start_provider(session, run_id, claim.attempt_id)
            await container.runs.project(session, run_id, claim.attempt_id, 'partial', '思考')
            run = await session.get(Run, run_id)
            run.lease_until = utcnow() - timedelta(seconds=1)
        from zhiheng.worker.main import recover_expired

        await recover_expired(container)
    else:

        class Provider:
            async def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
                yield ModelDelta(text='partial', reasoning='思考')
                if outcome == 'overflow':
                    yield ModelDelta(reasoning='想' * (1024 * 1024))
                raise ProviderError

        async def factory(binding: ModelBinding) -> Provider:
            return Provider()

        await InferenceExecutor(
            sessions=container.database.sessions,
            runs=container.runs,
            usage=container.usage,
            chat=container.chat,
            provider_factory=factory,
            lease_seconds=3,
            timeout_seconds=2,
        ).execute(run_id)
    result = (await admin.get(f'/api/runs/{run_id}')).json()
    assert result['status'] == ('interrupted' if outcome == 'interrupted' else 'failed'), result
    assert result['text'] == 'partial' and result['reasoning'] == '思考'
    assert result['usage']['status'] == 'unknown'
    assert len(result['text'].encode()) + len(result['reasoning'].encode()) <= 2 * 1024 * 1024


@pytest.mark.asyncio
async def test_public_governance_precedes_knowledge_context(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    application, key = await provision(admin, model_id)
    from tests.core.integration.test_knowledge_failures import ready_base

    base = await ready_base(admin, app, provider)
    provider.requests.clear()
    agent = (
        await admin.post(
            '/api/agents', json=config(model_id, application_id=application, knowledge_base_ids=[base['id']])
        )
    ).json()
    await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})
    endpoint = f'/v1/agents/{agent["id"]}/chat/completions'
    body = {'messages': [{'role': 'user', 'content': 'q'}]}
    assert (await admin.post(endpoint, headers={'Authorization': 'Bearer invalid'}, json=body)).status_code == 401
    _, other = await provision(admin, model_id)
    wrong = await admin.post(endpoint, headers={'Authorization': 'Bearer ' + other['api_key']}, json=body)
    assert wrong.status_code == 404
    limited = (
        await admin.post(
            f'/api/developer/applications/{application}/keys',
            json={'name': 'zero budget', 'model_ids': [model_id], 'monthly_budget': '0'},
        )
    ).json()
    plain = (await admin.post('/api/agents', json=config(model_id, application_id=application))).json()
    await admin.post(f'/api/agents/{plain["id"]}/publish', json={'expected_version': 1})
    quota = await admin.post(
        f'/v1/agents/{plain["id"]}/chat/completions',
        headers={'Authorization': 'Bearer ' + limited['api_key']},
        json=body,
    )
    assert quota.status_code == 429 and quota.json()['error']['code'] == 'rate_limit_exceeded'
    unavailable = await admin.post(endpoint, headers={'Authorization': 'Bearer ' + key['api_key']}, json=body)
    assert unavailable.status_code == 422 and unavailable.json()['error']['code'] == 'stable_request_id_required'
    missing_grant = await admin.post(
        endpoint,
        headers={'Authorization': 'Bearer ' + key['api_key']},
        json={**body, 'client_request_id': str(uuid4())},
    )
    assert missing_grant.status_code == 404 and missing_grant.json()['error']['code'] == 'model_not_found'
    assert provider.requests == []
    async with app.state.container.database.sessions() as session:
        assert (await session.scalars(select(Run).where(Run.resource_type == 'agent_invocation'))).all() == []


@pytest.mark.asyncio
async def test_concurrent_publish_and_skill_disable_have_no_deadlock(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from tests.core.integration.test_skills import install

    skill = await install(admin, 'publish-lock')
    application, _ = await provision(admin, model_id)
    agent = (
        await admin.post('/api/agents', json=config(model_id, application_id=application, skill_slugs=[skill['slug']]))
    ).json()
    container = app.state.container
    async with container.database.sessions.begin() as session:
        principal = await container.identity.authenticate(session, admin.cookies['zhiheng_session'])
        await container.skills.set_status(session, principal, UUID(skill['id']), status='disabled', expected_version=1)
        task = asyncio.create_task(admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1}))
        await asyncio.sleep(0.05)
        assert not task.done()
    response = await asyncio.wait_for(task, 2)
    assert response.status_code == 422 and response.json()['error']['code'] == 'skill_unavailable'
    await admin.patch(f'/api/skills/{skill["id"]}/status', json={'status': 'enabled', 'expected_version': 2})
    pair = await asyncio.wait_for(
        asyncio.gather(
            *(admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1}) for _ in range(2))
        ),
        2,
    )
    assert sorted(row.status_code for row in pair) == [200, 409]


@pytest.mark.asyncio
async def test_available_directory_excludes_unusable_configs_and_hides_prompt(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    application, _ = await provision(admin, model_id)
    agent = (
        await admin.post('/api/agents', json=config(model_id, application_id=application, system_prompt='PRIVATE'))
    ).json()
    await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})
    directory = (await admin.get('/api/agents/available')).json()
    assert directory == [{'id': agent['id'], 'name': '研究助手', 'description': '', 'published_version': 1}]
    await admin.patch(f'/api/models/{model_id}', json={'max_output_tokens': 16})
    assert (await admin.get('/api/agents/available')).json() == []


@pytest.mark.asyncio
async def test_queue_freezes_model_connection_and_skill_audit(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    from zhiheng.modules.agents.orm import AgentRunAudit

    agent = (
        await admin.post('/api/agents', json=config(model_id, system_prompt='FROZEN', skill_slugs=['document-summary']))
    ).json()
    body = {'client_request_id': str(uuid4()), 'messages': [{'role': 'user', 'content': 'q'}]}
    preview = (await admin.post(f'/api/agents/{agent["id"]}/preview', json=body)).json()
    model = (await admin.get('/api/models')).json()[0]
    await admin.patch(f'/api/models/{model_id}', json={'enabled': False, 'upstream_model': 'DONT_USE'})
    await admin.patch(
        f'/api/models/connections/{model["connection_id"]}', json={'enabled': False, 'api_key': 'rotated-secret'}
    )
    skills = (await admin.get('/api/skills/available')).json()
    skill = next(item for item in skills if item['slug'] == 'document-summary')
    await admin.patch(f'/api/skills/{skill["id"]}/status', json={'status': 'disabled', 'expected_version': 1})
    assert (
        await admin.post(f'/api/agents/{agent["id"]}/preview', json={**body, 'client_request_id': str(uuid4())})
    ).status_code == 404
    await execute_run(app.state.container, UUID(preview['id']))
    result = (await admin.get(f'/api/runs/{preview["id"]}')).json()
    assert result['status'] == 'completed', result
    assert provider.requests[0]['model'] == 'local-text'
    assert provider.request_headers[0]['authorization'] == 'Bearer local-test-key'
    async with app.state.container.database.sessions() as session:
        run = await session.get(Run, UUID(preview['id']))
        audit = await session.get(AgentRunAudit, UUID(preview['id']))
        assert audit.config_version == 1 and audit.skills[0]['revision_id'] == skill['revision_id']
        assert audit.skills[0]['content_hash'] == skill['content_hash']
        assert audit.skills[0]['content'] == run.payload['messages'][1]['content']


@pytest.mark.asyncio
async def test_inactive_application_blocks_publish_but_allows_owner_draft_preview(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    application, _ = await provision(admin, model_id)
    assert (await admin.post(f'/api/developer/applications/{application}/disable', json={})).status_code == 200
    agent = await admin.post('/api/agents', json=config(model_id, application_id=application))
    assert agent.status_code == 201, agent.text
    published = await admin.post(f'/api/agents/{agent.json()["id"]}/publish', json={'expected_version': 1})
    assert published.status_code == 409
    preview = await admin.post(
        f'/api/agents/{agent.json()["id"]}/preview',
        json={'client_request_id': str(uuid4()), 'messages': [{'role': 'user', 'content': 'q'}]},
    )
    assert preview.status_code == 202, preview.text
    await execute_run(app.state.container, UUID(preview.json()['id']))
    assert (await admin.get(f'/api/runs/{preview.json()["id"]}')).json()['status'] == 'completed'


@pytest.mark.asyncio
async def test_stale_agent_cas_precedes_changed_dependency_errors(admin: httpx.AsyncClient, model_id: str) -> None:
    application, _ = await provision(admin, model_id)
    agent = (await admin.post('/api/agents', json=config(model_id, application_id=application))).json()
    await admin.patch(
        f'/api/agents/{agent["id"]}', json=config(model_id, application_id=application, expected_version=1)
    )
    await admin.patch(f'/api/models/{model_id}', json={'enabled': False})
    for operation in ('update', 'publish'):
        response = (
            (await admin.patch(f'/api/agents/{agent["id"]}', json=config(model_id, expected_version=1)))
            if operation == 'update'
            else (await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1}))
        )
        assert response.status_code == 409 and response.json()['error']['code'] == 'agent_version_conflict', (
            response.text
        )


async def switch_current_skill_revision(app: object, skill: dict, replacement: dict) -> None:
    from zhiheng.modules.skills.orm import Skill, SkillRevision

    container = app.state.container
    async with container.database.sessions.begin() as session:
        row = await session.get(Skill, UUID(skill['id']))
        revision = await session.get(SkillRevision, UUID(replacement['revision_id']))
        owner = await container.identity.principal_for_user(session, row.owner_id)
        payload = revision.content.replace(f'name: {replacement["slug"]}\n', f'name: {skill["slug"]}\n', 1).encode()
        draft = await container.skills.prepare(session, owner, payload, filename='SKILL.md')
        changed = await container.skills.confirm(session, owner, draft.draft_id)
        replacement.update(changed.model_dump(mode='json'))


@pytest.mark.asyncio
async def test_public_skill_content_changes_only_after_republish(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    from tests.core.integration.test_skills import install
    from zhiheng.modules.agents.orm import AgentRunAudit
    from zhiheng.modules.agents.public import PreviewRequest

    original = await install(admin, 'published-original', 'PUBLISHED SKILL CONTENT')
    current = await install(admin, 'future-current', 'CURRENT SKILL CONTENT')
    application, key = await provision(admin, model_id)
    agent = (
        await admin.post(
            '/api/agents', json=config(model_id, application_id=application, skill_slugs=[original['slug']])
        )
    ).json()
    endpoint = f'/v1/agents/{agent["id"]}/chat/completions'
    assert (await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})).status_code == 200
    await switch_current_skill_revision(app, original, current)
    body = {'messages': [{'role': 'user', 'content': 'q'}]}
    preview = await admin.post(f'/api/agents/{agent["id"]}/preview', json={**body, 'client_request_id': str(uuid4())})
    assert preview.status_code == 202, preview.text
    await execute_run(app.state.container, UUID(preview.json()['id']))
    assert 'CURRENT SKILL CONTENT' in provider.requests[-1]['messages'][0]['content']
    public = await drive(
        app.state.container,
        asyncio.create_task(admin.post(endpoint, headers={'Authorization': 'Bearer ' + key['api_key']}, json=body)),
    )
    assert public.status_code == 200, public.text
    assert 'PUBLISHED SKILL CONTENT' in provider.requests[-1]['messages'][0]['content']
    async with app.state.container.database.sessions() as session:
        published_audit = await session.scalar(select(AgentRunAudit).where(AgentRunAudit.published_version == 1))
        preview_audit = await session.get(AgentRunAudit, UUID(preview.json()['id']))
        assert str(published_audit.skills[0]['revision_id']) == original['revision_id']
        assert published_audit.skills[0]['content_hash'] == original['content_hash']
        assert str(preview_audit.skills[0]['revision_id']) == current['revision_id']
        assert preview_audit.skills[0]['content_hash'] == current['content_hash']
    assert (await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 2})).status_code == 200
    async with app.state.container.database.sessions.begin() as session:
        queued, _, _ = await app.state.container.agent_runs.public(
            session,
            key['api_key'],
            UUID(agent['id']),
            PreviewRequest(messages=body['messages'], client_request_id=uuid4()),
            app.state.container.settings.token_limit,
        )
    disabled = await admin.patch(
        f'/api/skills/{original["id"]}/status', json={'status': 'disabled', 'expected_version': 2}
    )
    assert disabled.status_code == 200, disabled.text
    assert (
        await admin.post(endpoint, headers={'Authorization': 'Bearer ' + key['api_key']}, json=body)
    ).status_code == 404
    await execute_run(app.state.container, queued.id)
    assert 'CURRENT SKILL CONTENT' in provider.requests[-1]['messages'][0]['content']
    async with app.state.container.database.sessions() as session:
        audit = await session.get(AgentRunAudit, queued.id)
        assert audit.published_version == 2 and str(audit.skills[0]['revision_id']) == current['revision_id']
        assert audit.skills[0]['content_hash'] == current['content_hash']


@pytest.mark.asyncio
async def test_current_skill_size_does_not_change_published_context_budget(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    from tests.core.integration.test_skills import install

    original = await install(admin, 'published-small', 'SMALL FROZEN CONTENT')
    oversized = await install(admin, 'future-large', 'x' * 32768)
    application, key = await provision(admin, model_id)
    agent = (
        await admin.post(
            '/api/agents', json=config(model_id, application_id=application, skill_slugs=[original['slug']])
        )
    ).json()
    assert (await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})).status_code == 200
    await switch_current_skill_revision(app, original, oversized)
    body = {'messages': [{'role': 'user', 'content': 'q'}]}
    preview = await admin.post(f'/api/agents/{agent["id"]}/preview', json={**body, 'client_request_id': str(uuid4())})
    assert preview.status_code == 422 and preview.json()['error']['code'] == 'skill_context_too_long'
    republish = await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 2})
    assert republish.status_code == 422 and republish.json()['error']['code'] == 'skill_context_too_long'
    response = await drive(
        app.state.container,
        asyncio.create_task(
            admin.post(
                f'/v1/agents/{agent["id"]}/chat/completions',
                headers={'Authorization': 'Bearer ' + key['api_key']},
                json=body,
            )
        ),
    )
    assert response.status_code == 200, response.text
    assert 'SMALL FROZEN CONTENT' in provider.requests[-1]['messages'][0]['content']
