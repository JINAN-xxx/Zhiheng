import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text

from zhiheng.modules.agents.orm import Agent, AgentVersion
from zhiheng.modules.agents.public import AgentConfig
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey, Invocation
from zhiheng.modules.knowledge.graph.orm import GraphJob
from zhiheng.modules.knowledge.orm import IndexGeneration, KnowledgeBase, KnowledgeJob, RetrievalIntent
from zhiheng.modules.models.orm import ConnectionRevision, ModelConnection, ModelDefinition, RateCard
from zhiheng.modules.work.orm import WorkMember, WorkTask
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow


async def test_historical_run_rate_and_invoice_survive_model_and_connection_removal(
    admin: httpx.AsyncClient, app: object, model_id: str
) -> None:
    container = app.state.container
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    model = UUID(model_id)
    rate = await admin.post(
        f'/api/models/{model_id}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.02'},
    )
    assert rate.status_code == 201, rate.text
    async with container.database.sessions.begin() as session:
        binding = await container.models.resolve(session, model)
        run = Run(
            owner_id=owner_id,
            billing_owner_id=owner_id,
            resource_type='connection_test',
            resource_id=model,
            client_request_id=uuid4(),
            fingerprint='historical',
            payload={'binding': binding.model_dump(mode='json')},
            status='completed',
        )
        session.add(run)
        await session.flush()
        await container.usage.reserve(
            session,
            user_id=owner_id,
            run_id=run.id,
            model_id=model,
            source='connection_test',
            tokens=100,
            token_limit=100000,
            rate=await container.models.latest_rate(session, model),
        )
        await container.usage.settle(session, run.id, input_tokens=10, output_tokens=5)
        run_id, connection_id, revision_id = run.id, binding.connection_id, binding.connection_revision_id
    response = await admin.delete(f'/api/models/{model_id}', headers={'Content-Type': 'application/json'})
    assert response.status_code == 200, response.text
    response = await admin.delete(
        f'/api/models/connections/{connection_id}', headers={'Content-Type': 'application/json'}
    )
    assert response.status_code == 200, response.text
    async with container.database.sessions() as session:
        removed = await session.get(ModelDefinition, model)
        assert removed.deleted_at is not None and not removed.enabled and not removed.published
        assert (await session.get(ModelConnection, connection_id)).deleted_at is not None
        assert await session.get(ConnectionRevision, revision_id) is not None
        assert (await session.get(Run, run_id)).payload['binding']['model_id'] == model_id
        invoice = await session.scalar(select(Invocation).where(Invocation.run_id == run_id))
        assert invoice.billable_cost == Decimal('0.2')
        assert Decimal(invoice.rate_snapshot['input_price']) == Decimal('0.01')
        assert Decimal(invoice.rate_snapshot['output_price']) == Decimal('0.02')
        assert await session.get(RateCard, invoice.rate_card_id) is not None


@pytest.mark.parametrize('status', ['pending', 'running', 'cancel_requested'])
async def test_inflight_binding_blocks_model_and_connection_removal_with_reference(
    admin: httpx.AsyncClient, app: object, model_id: str, status: str
) -> None:
    container = app.state.container
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    async with container.database.sessions.begin() as session:
        binding = await container.models.resolve(session, UUID(model_id))
        run = Run(
            owner_id=owner_id,
            billing_owner_id=owner_id,
            resource_type='connection_test',
            resource_id=UUID(model_id),
            client_request_id=uuid4(),
            fingerprint=status,
            payload={'binding': binding.model_dump(mode='json')},
            status=status,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
    result = await admin.delete(f'/api/models/{model_id}', headers={'Content-Type': 'application/json'})
    assert result.status_code == 409, result.text
    assert any(
        item['kind'] == 'run' and item['id'] == str(run_id) for item in result.json()['error']['details']['references']
    )
    async with container.database.sessions() as session:
        assert await container.runs.references_connection(session, binding.connection_id)
        assert [ref.id for ref in await container.runs.connection_references(session, binding.connection_id)] == [
            run_id
        ]


async def test_agent_references_only_live_draft_and_current_publication(
    admin: httpx.AsyncClient, app: object, model_id: str
) -> None:
    container = app.state.container
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    model = UUID(model_id)
    live = {'name': 'Live agent', 'model_id': model_id, 'skill_slugs': ['document-summary']}
    replacement = {'name': 'New draft', 'model_id': str(uuid4()), 'skill_slugs': []}
    async with container.database.sessions.begin() as session:
        agent = Agent(owner_id=owner_id, config=replacement, published_version=1)
        session.add(agent)
        await session.flush()
        publication = AgentVersion(agent_id=agent.id, version=1, draft_version=1, config=live, skills=[])
        session.add(publication)
        await session.flush()
        refs = await container.agents.model_references(session, model)
        assert [(r.id, r.name, r.status, r.path) for r in refs] == [
            (agent.id, 'Live agent', 'published', f'/agents/{agent.id}')
        ]
        assert await container.agents.references_skill(session, owner_id, 'document-summary')
        agent.published_version = None
        await session.flush()
        assert not await container.agents.references_model(session, model)
        assert not await container.agents.references_skill(session, owner_id, 'document-summary')
        agent.config = live
        await session.flush()
        assert (await container.agents.model_references(session, model))[0].status == 'draft'
        agent.deleted_at = utcnow()
        await session.flush()
        assert not await container.agents.references_model(session, model)
        assert not await container.agents.references_skill(session, owner_id, 'document-summary')
        assert await session.get(AgentVersion, publication.id) is not None


@pytest.mark.parametrize('release', ['removed', 'completed', 'archived', 'deleted'])
async def test_work_releases_historical_member_references(
    admin: httpx.AsyncClient, app: object, model_id: str, release: str
) -> None:
    container = app.state.container
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    async with container.database.sessions.begin() as session:
        agent = Agent(owner_id=owner_id, config={'name': 'Agent'})
        session.add(agent)
        await session.flush()
        task = WorkTask(
            owner_id=owner_id,
            title='Live task',
            goal='Goal',
            create_active_agent_id=agent.id,
            create_client_request_id=uuid4(),
            create_fingerprint='test',
        )
        session.add(task)
        await session.flush()
        member = WorkMember(
            task_id=task.id,
            agent_id=agent.id,
            agent_owner_id=owner_id,
            agent_name_snapshot='Agent',
            published_version=1,
            config={'model_id': model_id, 'skill_slugs': ['document-summary']},
            skills=[],
        )
        session.add(member)
        await session.flush()
        assert (await container.work.model_references(session, UUID(model_id)))[0].id == task.id
        assert (await container.work.agent_references(session, agent.id))[0].path == f'/work?taskId={task.id}'
        assert await container.work.references_skill(session, owner_id, 'document-summary')
        if release == 'removed':
            member.removed_at = utcnow()
        elif release == 'deleted':
            task.deleted_at = utcnow()
        else:
            task.status = release
        await session.flush()
        assert not await container.work.references_model(session, UUID(model_id))
        assert not await container.work.references_skill(session, owner_id, 'document-summary')
        assert await container.work.agent_references(session, agent.id) == []
        assert await session.get(WorkMember, member.id) is not None


@pytest.mark.parametrize('release', ['revoked', 'expired', 'retired', 'disabled_app', 'deleted_app'])
async def test_unusable_keys_release_model_but_keep_authorization_history(
    admin: httpx.AsyncClient, app: object, model_id: str, release: str
) -> None:
    container = app.state.container
    created = (
        await admin.post('/api/developer/applications', json={'code': 'lifecycle', 'name': 'Application'})
    ).json()
    issued = await admin.post(
        f'/api/developer/applications/{created["id"]}/keys', json={'name': 'SDK', 'model_ids': [model_id]}
    )
    assert issued.status_code == 201, issued.text
    async with container.database.sessions.begin() as session:
        key = await session.get(DeveloperKey, UUID(issued.json()['id']))
        application = await session.get(DeveloperApplication, UUID(created['id']))
        refs = await container.keys.model_references(session, UUID(model_id))
        assert [(ref.id, ref.name, ref.owner_id) for ref in refs] == [(key.id, 'SDK', application.owner_id)]
        if release == 'revoked':
            key.status = 'revoked'
        elif release == 'expired':
            key.expires_at = utcnow() - timedelta(seconds=1)
        elif release == 'retired':
            key.status, key.retire_at = 'retiring', utcnow() - timedelta(seconds=1)
        elif release == 'disabled_app':
            application.status = 'disabled'
        else:
            application.deleted_at = utcnow()
        await session.flush()
        assert not await container.keys.references_model(session, UUID(model_id))
    response = await admin.delete(f'/api/models/{model_id}', headers={'Content-Type': 'application/json'})
    assert response.status_code == 200, response.text
    async with container.database.sessions() as session:
        assert (await session.get(DeveloperKey, UUID(issued.json()['id']))).model_ids == [model_id]


async def test_deleted_knowledge_only_protects_pending_cleanup(
    admin: httpx.AsyncClient, app: object, model_id: str
) -> None:
    container = app.state.container
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    model = UUID(model_id)
    async with container.database.sessions.begin() as session:
        binding = await container.models.resolve(session, model)
        base = KnowledgeBase(owner_id=owner_id, name='Knowledge', embed_model_id=model)
        session.add(base)
        await session.flush()
        assert await container.knowledge.references_model(session, model)
        base.deleted_at = utcnow()
        job = KnowledgeJob(
            base_id=base.id, actor_id=owner_id, client_request_id=uuid4(), fingerprint='delete', operation='delete_base'
        )
        session.add(job)
        await session.flush()
        session.add(
            IndexGeneration(
                id=job.index_revision,
                base_id=base.id,
                attempt_id=job.attempt_id,
                collection_name='lifecycle',
                binding=binding.model_dump(mode='json'),
                fingerprint='delete',
            )
        )
        await session.flush()
        assert await container.knowledge.references_model(session, model)
        job.status = 'completed'
        await session.flush()
        assert not await container.knowledge.references_model(session, model)


async def test_frozen_graph_parent_keeps_model_and_old_connection_until_terminal(
    admin: httpx.AsyncClient, app: object, model_id: str
) -> None:
    container = app.state.container
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    model = UUID(model_id)
    async with container.database.sessions.begin() as session:
        binding = await container.models.resolve(session, model)
        base = KnowledgeBase(owner_id=owner_id, name='Deleted graph', embed_model_id=model, deleted_at=utcnow())
        session.add(base)
        await session.flush()
        frozen = {'llm_binding': binding.model_dump(mode='json'), 'embedding_binding': binding.model_dump(mode='json')}
        job = GraphJob(
            base_id=base.id,
            actor_id=owner_id,
            owner_id=owner_id,
            client_request_id=uuid4(),
            request_fingerprint='graph',
            operation='build',
            attempt_id=uuid4(),
            frozen=frozen,
            request={},
            status='waiting_model',
        )
        session.add(job)
        await session.flush()
        assert [ref.id for ref in await container.knowledge.model_references(session, model)] == [job.id]
        assert [ref.id for ref in await container.knowledge.connection_references(session, binding.connection_id)] == [
            job.id
        ]
        job.status = 'cancelled'
        await session.flush()
        assert not await container.knowledge.model_references(session, model)
        assert not await container.knowledge.connection_references(session, binding.connection_id)
        assert job.frozen == frozen


@pytest.mark.parametrize('kind', ['agent_preview', 'work_turn'])
async def test_preparation_keeps_future_model_skill_and_agent_dependencies(
    admin: httpx.AsyncClient, app: object, model_id: str, kind: str
) -> None:
    container = app.state.container
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    model, agent_id, task_id = UUID(model_id), uuid4(), uuid4()
    async with container.database.sessions.begin() as session:
        binding = await container.models.resolve(session, model)
        config = {'model_id': model_id, 'skill_slugs': ['document-summary']}
        session.add(Agent(id=agent_id, owner_id=owner_id, config=config))
        await session.flush()
        if kind == 'work_turn':
            session.add(
                WorkTask(
                    id=task_id,
                    owner_id=owner_id,
                    title='Preparing task',
                    goal='Goal',
                    create_active_agent_id=agent_id,
                    create_client_request_id=uuid4(),
                    create_fingerprint='preparing',
                )
            )
            await session.flush()
            session.add(
                WorkMember(
                    task_id=task_id,
                    agent_id=agent_id,
                    agent_owner_id=owner_id,
                    agent_name_snapshot='Preparing agent',
                    published_version=1,
                    config=config,
                    skills=[],
                )
            )
            await session.flush()
        dependencies = {
            'bases': [{'binding': binding.model_dump(mode='json')}],
            'config' if kind == 'agent_preview' else 'member_config': config,
        }
        intent = RetrievalIntent(
            namespace_id=owner_id,
            client_request_id=uuid4(),
            actor_id=owner_id,
            owner_id=owner_id,
            resource_type=kind,
            resource_id=agent_id if kind == 'agent_preview' else task_id,
            fingerprint='preparation',
            request={'body': {'agent_id': str(agent_id)}},
            dependencies=dependencies,
            deadline=utcnow() + timedelta(minutes=1),
        )
        session.add(intent)
        await session.flush()
        assert [ref.id for ref in await container.knowledge.model_references(session, model)] == [intent.id]
        assert [ref.id for ref in await container.knowledge.connection_references(session, binding.connection_id)] == [
            intent.id
        ]
        assert [
            ref.id for ref in await container.knowledge.skill_references(session, 'document-summary', owner_id=owner_id)
        ] == [intent.id]
        assert [ref.id for ref in await container.knowledge.agent_references(session, agent_id)] == [intent.id]
        intent.status = 'cancelled'
        await session.flush()
        assert not await container.knowledge.model_references(session, model)
        assert not await container.knowledge.connection_references(session, binding.connection_id)
        assert not await container.knowledge.skill_references(session, 'document-summary', owner_id=owner_id)
        assert not await container.knowledge.agent_references(session, agent_id)
        intent = RetrievalIntent(
            namespace_id=owner_id,
            client_request_id=uuid4(),
            actor_id=owner_id,
            owner_id=owner_id,
            resource_type='agent_preview',
            resource_id=uuid4(),
            fingerprint='intent',
            request={},
            dependencies={'main_request': {'binding': binding.model_dump(mode='json')}, 'bases': []},
            deadline=utcnow() + timedelta(minutes=1),
        )
        session.add(intent)
        await session.flush()
        assert (await container.knowledge.model_references(session, model))[0].id == intent.id
        intent.status = 'failed'
        await session.flush()
        assert not await container.knowledge.references_model(session, model)


async def test_new_agent_reference_commits_before_waiting_model_delete_checks_references(
    admin: httpx.AsyncClient, app: object, model_id: str
) -> None:
    container = app.state.container
    deletion = None
    try:
        async with container.database.sessions.begin() as session:
            principal = await container.identity.authenticate(session, admin.cookies['zhiheng_session'])
            agent = await container.agent_management.create(
                session, principal, AgentConfig(name='Committed dependency', model_id=UUID(model_id), max_tokens=32)
            )
            admission_pid = await session.scalar(text('SELECT pg_backend_pid()'))
            deletion = asyncio.create_task(
                admin.delete(f'/api/models/{model_id}', headers={'Content-Type': 'application/json'})
            )
            async with container.database.sessions() as observer:
                for _ in range(100):
                    blocked = await observer.scalar(
                        text('SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid)))'),
                        {'pid': admission_pid},
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
                assert blocked and not deletion.done()
        response = await asyncio.wait_for(deletion, 2)
        assert response.status_code == 409, response.text
        assert any(
            ref['kind'] == 'agent' and ref['id'] == str(agent.id)
            for ref in response.json()['error']['details']['references']
        )
        async with container.database.sessions() as session:
            assert (await container.models.resolve(session, UUID(model_id))).model_id == UUID(model_id)
    finally:
        if deletion is not None:
            deletion.cancel()
            await asyncio.gather(deletion, return_exceptions=True)
