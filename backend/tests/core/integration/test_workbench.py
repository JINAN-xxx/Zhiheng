"""Real database workbench projections respect reporting time and independent privileges."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.core.integration.test_agents import config
from tests.core.permission_support import account, grants
from tests.core.provider import ProviderServer
from zhiheng.application.overview import WorkbenchService
from zhiheng.modules.billing.orm import CreditControl, CreditHold, CreditRate, Invocation
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey
from zhiheng.modules.overview.public import WorkbenchOverview
from zhiheng.runtime.orm import Run
from zhiheng.shared.principal import Principal

NOW = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)
MIDNIGHT = datetime(2026, 11, 15, 16, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('zhiheng.application.overview.utcnow', lambda: NOW)


async def actor_id(client: httpx.AsyncClient) -> UUID:
    return UUID((await client.get('/api/auth/me')).json()['id'])


async def invocation(
    app: FastAPI,
    model_id: str,
    owner: UUID,
    *,
    at: datetime = NOW,
    status: str = 'completed',
    started: bool = True,
    input_tokens: int | None = 10,
    output_tokens: int | None = 2,
) -> UUID:
    async with app.state.container.database.sessions.begin() as session:
        run = Run(
            owner_id=owner,
            billing_owner_id=owner,
            resource_type='chat',
            resource_id=uuid4(),
            client_request_id=uuid4(),
            fingerprint='test',
            payload={},
            status=status,
            provider_started=started,
            created_at=at,
        )
        session.add(run)
        await session.flush()
        session.add(
            Invocation(
                run_id=run.id,
                user_id=owner,
                model_id=UUID(model_id),
                source='chat',
                reserved_tokens=0,
                status='known' if input_tokens is not None and output_tokens is not None else 'unknown',
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                created_at=at,
            )
        )
        return run.id


async def test_overview_requires_its_module_and_cookie_login(admin: httpx.AsyncClient, app: FastAPI) -> None:
    async with account(admin, app, *grants('platform:usage')) as (member, _, _):
        assert (await member.get('/api/overview')).status_code == 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        assert (await anonymous.get('/api/overview', headers={'Authorization': 'Bearer test-only'})).status_code == 401


async def test_overview_http_reads_use_a_repeatable_read_read_only_transaction(
    admin: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = WorkbenchService.read
    observed: list[tuple[str, str]] = []

    async def inspected_read(
        service: WorkbenchService,
        session: AsyncSession,
        actor: Principal,
        scope: Literal['mine', 'platform'],
        days: Literal[7, 30],
    ) -> WorkbenchOverview:
        isolation = await session.scalar(text('SHOW transaction_isolation'))
        read_only = await session.scalar(text('SHOW transaction_read_only'))
        observed.append((str(isolation), str(read_only)))
        return await original(service, session, actor, scope, days)

    monkeypatch.setattr(WorkbenchService, 'read', inspected_read)
    response = await admin.get('/api/overview?days=7')
    assert response.status_code == 200, response.text
    assert observed == [('repeatable read', 'on')]
    value = response.json()
    assert value['scope'] == 'mine' and value['period_days'] == 7
    assert len(value['metrics']['trend']) == 7


async def test_overview_alone_cannot_reveal_usage_credits_or_platform_data(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    async with account(admin, app, *grants('platform:overview')) as (member, _, _):
        response = await member.get('/api/overview')
        assert response.status_code == 200, response.text
        value = response.json()
        assert value['metrics'] is None and value['credits'] is None
        assert value['recent'] == value['activities'] == value['alerts'] == []
        assert not value['can_view_platform']
        assert (await member.get('/api/overview?scope=platform')).status_code == 403


async def test_personal_daily_aggregation_uses_shanghai_midnight_and_preserves_unknown_tokens(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    async with account(admin, app, *(grants('platform:overview') | grants('platform:usage'))) as (member, _, _):
        owner = await actor_id(member)
        await invocation(app, model_id, owner, at=MIDNIGHT - timedelta(seconds=1))
        await invocation(app, model_id, owner, at=MIDNIGHT)
        await invocation(app, model_id, owner, input_tokens=3, output_tokens=1)
        await invocation(app, model_id, owner, status='failed', input_tokens=5, output_tokens=None)
        await invocation(app, model_id, owner, status='cancelled', input_tokens=None, output_tokens=None)
        await invocation(app, model_id, owner, status='running', input_tokens=None, output_tokens=None)
        await invocation(app, model_id, owner, status='failed', started=False, input_tokens=10000)
        await invocation(app, model_id, await actor_id(admin), input_tokens=99999)
        response = await member.get('/api/overview?days=7')
        assert response.status_code == 200, response.text
        m = response.json()['metrics']
        assert (m['today_calls'], m['yesterday_calls']) == (5, 1)
        assert (m['successful_calls'], m['failed_calls'], m['cancelled_calls']) == (3, 1, 1)
        assert m['today_known_tokens'] == 21 and m['today_unconfirmed_calls'] == 3
        assert len(m['trend']) == 7 and m['trend'][-1] == {'day': '2026-11-16', 'calls': 5}


async def test_platform_statistics_does_not_grant_financial_or_private_resource_authority(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    owner = await actor_id(admin)
    await invocation(app, model_id, owner)
    created = await admin.post('/api/agents', json=config(model_id, name='PRIVATE ASSISTANT'))
    assert created.status_code == 201, created.text
    assert (
        await admin.post('/api/agents/' + created.json()['id'] + '/enable', json={'expected_version': 1})
    ).status_code == 200
    permissions = grants('platform:overview', 'platform_stats') | grants('platform:agents') | grants('platform:models')
    async with account(admin, app, *permissions) as (member, _, _):
        response = await member.get('/api/overview?scope=platform')
        assert response.status_code == 200, response.text
        value = response.json()
        assert value['metrics']['today_calls'] == 1 and value['credits'] is None
        assert 'PRIVATE ASSISTANT' not in response.text and value['recent'] == []
        assert (await member.get('/api/agents/' + created.json()['id'])).status_code == 404


async def test_monthly_points_stay_exact_and_key_expiry_stays_owner_scoped(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    async with account(
        admin, app, *(grants('platform:overview') | grants('platform:usage') | grants('platform:developer'))
    ) as (member, _, _):
        owner = await actor_id(member)
        foreign = await actor_id(admin)
        async with app.state.container.database.sessions.begin() as session:
            control = await session.get(CreditControl, 1)
            control.enabled = True
            rate = CreditRate(
                model_id=UUID(model_id),
                version=1,
                billing_mode='token',
                is_free=False,
                input_points=Decimal('1'),
                output_points=Decimal('0'),
                cache_points=Decimal('0'),
                search_points=Decimal('0'),
            )
            session.add(rate)
            await session.flush()
            for who, amount, state in [
                (owner, Decimal('0.000000000000000123'), 'settled'),
                (foreign, Decimal('900'), 'settled'),
                (owner, None, 'unknown'),
            ]:
                session.add(
                    CreditHold(
                        run_id=uuid4(),
                        user_id=who,
                        model_id=UUID(model_id),
                        source='chat',
                        operation='chat',
                        status=state,
                        reserved_points=Decimal('0'),
                        charged_points=amount,
                        rate_id=rate.id,
                        rate_version=1,
                        rate_snapshot={},
                        request_payload={},
                        created_at=NOW,
                    )
                )
            for who, name in [(owner, 'My API Key'), (foreign, 'PRIVATE OTHER KEY')]:
                application = DeveloperApplication(code=uuid4().hex, name='Application', owner_id=who)
                session.add(application)
                await session.flush()
                session.add(
                    DeveloperKey(
                        application_id=application.id,
                        name=name,
                        prefix='fixture',
                        digest=uuid4().hex + uuid4().hex,
                        status='active',
                        model_ids=[],
                        expires_at=NOW + timedelta(days=2),
                    )
                )
        response = await member.get('/api/overview')
        assert response.status_code == 200, response.text
        value = response.json()
        assert Decimal(value['credits']['monthly_points']) == Decimal('0.000000000000000123')
        assert isinstance(value['credits']['monthly_points'], str) and value['credits']['unconfirmed_calls'] == 1
        assert len(value['alerts']) == 1 and 'My API Key' in value['alerts'][0]['title']
        assert (
            'PRIVATE OTHER KEY' not in response.text
            and 'digest' not in response.text
            and 'base_url' not in response.text
        )


async def test_period_is_bounded_and_empty_series_has_complete_calendar_days(admin: httpx.AsyncClient) -> None:
    response = await admin.get('/api/overview?days=30')
    assert response.status_code == 200, response.text
    value = response.json()
    assert len(value['metrics']['trend']) == 30
    assert all(row['calls'] == 0 for row in value['metrics']['trend'])
    for query in ('days=14', 'days=100', 'scope=other'):
        assert (await admin.get('/api/overview?' + query)).status_code == 422


async def test_real_resource_metadata_and_recent_calls_survive_without_exposing_configuration(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    from tests.core.integration.test_knowledge import VectorFixture, embedding_model
    from tests.core.integration.test_knowledge_failures import finish_job, source
    from tests.core.integration.test_skills import install
    from zhiheng.modules.agents.orm import AgentRunAudit
    from zhiheng.modules.chat.orm import ChatMessage, Conversation
    from zhiheng.modules.skills.orm import Skill

    owner = await actor_id(admin)
    skill = await install(admin, 'workbench-personal', 'PRIVATE SKILL BODY')
    agent = (
        await admin.post(
            '/api/agents', json=config(model_id, name='Workbench assistant', system_prompt='PRIVATE SYSTEM PROMPT')
        )
    ).json()
    assert (await admin.post('/api/agents/' + agent['id'] + '/enable', json={'expected_version': 1})).status_code == 200
    embed = await embedding_model(admin, provider)
    base = (
        await admin.post('/api/knowledge/bases', json={'name': 'Workbench library', 'embed_model_id': embed})
    ).json()
    file = await source(admin, 'workbench.txt', b'Workbench source material for real metadata')
    app.state.container.knowledge_jobs.vectors = VectorFixture()
    imported = await admin.post(
        '/api/knowledge/bases/' + base['id'] + '/documents',
        json={'file_ids': [file['id']], 'client_request_id': str(uuid4())},
    )
    assert imported.status_code == 202, imported.text
    assert (await finish_job(admin, app, imported.json()))['status'] == 'completed'
    run_id = await invocation(app, model_id, owner, at=NOW - timedelta(hours=2))
    async with app.state.container.database.sessions.begin() as session:
        conversation = Conversation(owner_id=owner, title='Private conversation')
        session.add(conversation)
        await session.flush()
        session.add(
            ChatMessage(
                conversation_id=conversation.id,
                run_id=run_id,
                role='user',
                content='PRIVATE QUESTION',
                skill_snapshots=[{'slug': skill['slug']}],
                created_at=NOW - timedelta(hours=2),
            )
        )
        session.add(
            AgentRunAudit(
                run_id=run_id,
                agent_id=UUID(agent['id']),
                owner_id=owner,
                config={},
                skills=[],
                knowledge=[],
            )
        )
        row = await session.get(Skill, UUID(skill['id']))
        row.updated_at = NOW + timedelta(days=1)
    from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeJob

    async with app.state.container.database.sessions.begin() as session:
        stale = KnowledgeJob(
            base_id=UUID(base['id']),
            actor_id=owner,
            client_request_id=uuid4(),
            fingerprint='historical failure',
            operation='rechunk',
            status='failed',
            phase='write',
            error={'code': 'parse_failed', 'message': 'old error'},
            created_at=NOW - timedelta(days=2),
            finished_at=NOW - timedelta(days=2),
        )
        session.add(stale)
        library = await session.get(KnowledgeBase, UUID(base['id']))
        library.last_error = {'code': 'parse_failed', 'message': 'historical metadata retained'}
    response = await admin.get('/api/overview')
    assert response.status_code == 200, response.text
    value = response.json()
    assert not any(item['kind'] == 'knowledge' for item in value['alerts']), (
        'A later successful job and ready document resolve the old failure'
    )
    kinds = {item['kind'] for item in value['recent']}
    assert {'model', 'agent', 'knowledge', 'skill'} <= kinds
    record = next(item for item in value['recent'] if item['kind'] == 'skill')
    assert datetime.fromisoformat(record['occurred_at'].replace('Z', '+00:00')) == NOW - timedelta(hours=2)
    assert {'created', 'published', 'indexed'} <= {item['action'] for item in value['activities']}
    for secret in ['PRIVATE SKILL BODY', 'PRIVATE SYSTEM PROMPT', 'PRIVATE QUESTION', 'encrypted_api_key', 'base_url']:
        assert secret not in response.text


async def test_revoking_model_module_hides_previously_used_model_immediately(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    permissions = grants('platform:overview') | grants('platform:usage') | grants('platform:models')
    async with account(admin, app, *permissions) as (member, role, _):
        await invocation(app, model_id, await actor_id(member))
        before = await member.get('/api/overview')
        assert before.status_code == 200 and len(before.json()['recent']) == 1
        changed = await admin.patch(
            '/api/admin/roles/' + role['id'],
            json={
                'expected_version': role['version'],
                'permissions': sorted(permissions - {'platform:models:enabled'}),
            },
        )
        assert changed.status_code == 200, changed.text
        after = await member.get('/api/overview')
        assert after.status_code == 200 and after.json()['recent'] == []
        assert after.json()['metrics']['today_calls'] == 1


async def test_shared_library_recent_use_disappears_after_access_revocation(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from tests.core.integration.test_knowledge import embedding_model
    from zhiheng.modules.knowledge.orm import KnowledgeGrant, RetrievalIntent

    embed = await embedding_model(admin, provider)
    base = (
        await admin.post('/api/knowledge/bases', json={'name': 'Explicitly shared library', 'embed_model_id': embed})
    ).json()
    async with account(admin, app, *(grants('platform:overview') | grants('platform:knowledge'))) as (member, _, _):
        owner = await actor_id(member)
        async with app.state.container.database.sessions.begin() as session:
            session.add(KnowledgeGrant(base_id=UUID(base['id']), user_id=owner, effect='allow'))
            session.add(
                RetrievalIntent(
                    namespace_id=owner,
                    client_request_id=uuid4(),
                    actor_id=owner,
                    owner_id=owner,
                    resource_type='knowledge_query',
                    resource_id=UUID(base['id']),
                    fingerprint='fixture',
                    request={},
                    dependencies={},
                    status='completed',
                    deadline=NOW + timedelta(minutes=5),
                    created_at=NOW,
                )
            )
        before = await member.get('/api/overview')
        assert before.status_code == 200 and before.json()['recent'][0]['name'] == 'Explicitly shared library'
        async with app.state.container.database.sessions.begin() as session:
            grant = await session.get(KnowledgeGrant, (UUID(base['id']), owner))
            grant.effect = 'deny'
        after = await member.get('/api/overview')
        assert after.status_code == 200 and after.json()['recent'] == []


async def test_real_assistant_conversation_usage_appears_from_native_run_snapshot(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import func, select

    from tests.core.integration.test_assistant_conversations import assistant, conversation, submit
    from tests.core.integration.test_skills import install
    from zhiheng.modules.agents.orm import Agent, AgentRunAudit
    from zhiheng.modules.skills.orm import Skill
    from zhiheng.shared.time import utcnow
    from zhiheng.worker.execution import execute_run

    monkeypatch.setattr('zhiheng.application.overview.utcnow', utcnow)
    skill = await install(admin, 'real-conversation-recent', 'PRIVATE WORKBENCH SKILL')
    agent = await assistant(
        admin,
        model_id,
        name='Real assistant usage',
        skill_slugs=[skill['slug']],
        system_prompt='PRIVATE WORKBENCH SYSTEM',
    )
    async with app.state.container.database.sessions.begin() as session:
        old = utcnow() - timedelta(days=40)
        (await session.get(Agent, UUID(agent['id']))).created_at = old
        (await session.get(Skill, UUID(skill['id']))).created_at = old
    before = (await admin.get('/api/overview')).json()
    assert not any(item['kind'] in {'agent', 'skill'} for item in before['recent'])
    chat = await conversation(admin, agent['id'])
    accepted = await submit(admin, agent['id'], chat['id'], 'PRIVATE WORKBENCH QUESTION')
    pending = (await admin.get('/api/overview')).json()
    assert not any(item['kind'] in {'agent', 'skill'} for item in pending['recent'])
    await execute_run(app.state.container, UUID(accepted['id']))
    assert (await admin.get('/api/runs/' + accepted['id'])).json()['status'] == 'completed'
    async with app.state.container.database.sessions() as session:
        actual = await session.get(Run, UUID(accepted['id']))
        assert actual.resource_type == 'agent_chat' and actual.provider_started
        used_at = actual.created_at
        assert (
            await session.scalar(
                select(func.count()).select_from(AgentRunAudit).where(AgentRunAudit.run_id == actual.id)
            )
            == 1
        )
    response = await admin.get('/api/overview')
    assert response.status_code == 200, response.text
    recent = response.json()['recent']
    for kind, identifier in [('agent', agent['id']), ('skill', skill['id'])]:
        record = next(item for item in recent if item['kind'] == kind and item['id'] == identifier)
        assert record['action'] == 'used'
        assert datetime.fromisoformat(record['occurred_at'].replace('Z', '+00:00')) == used_at
    assert len(provider.requests) == response.json()['metrics']['today_calls'] == 1
    for private in ['PRIVATE WORKBENCH SKILL', 'PRIVATE WORKBENCH SYSTEM', 'PRIVATE WORKBENCH QUESTION']:
        assert private not in response.text
    async with account(
        admin,
        app,
        *(grants('platform:overview', 'platform_stats') | grants('platform:agents') | grants('platform:skills')),
    ) as (other, _, _):
        foreign = await other.get('/api/overview?scope=platform')
        assert foreign.status_code == 200, foreign.text
        assert foreign.json()['metrics']['today_calls'] == 1
        assert not any(item['id'] in {agent['id'], skill['id']} for item in foreign.json()['recent'])


async def test_assistant_recent_usage_uses_each_turn_publication_skills_and_frozen_name(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.core.integration.test_assistant_conversations import assistant, conversation, submit
    from tests.core.integration.test_skills import install
    from zhiheng.shared.time import utcnow
    from zhiheng.worker.execution import execute_run

    monkeypatch.setattr('zhiheng.application.overview.utcnow', utcnow)
    first_skill = await install(admin, 'first-publication-skill')
    second_skill = await install(admin, 'second-publication-skill')
    agent = await assistant(admin, model_id, name='First accepted name', skill_slugs=[first_skill['slug']])
    chat = await conversation(admin, agent['id'])
    first = await submit(admin, agent['id'], chat['id'], 'First actual use')
    await execute_run(app.state.container, UUID(first['id']))
    current = (await admin.get('/api/agents/' + agent['id'])).json()
    updated = await admin.patch(
        '/api/agents/' + agent['id'],
        json={
            **config(model_id, name='Second accepted name', skill_slugs=[second_skill['slug']]),
            'expected_version': current['version'],
        },
    )
    assert updated.status_code == 200, updated.text
    published = await admin.post(
        '/api/agents/' + agent['id'] + '/enable', json={'expected_version': updated.json()['version']}
    )
    assert published.status_code == 200, published.text
    second = await submit(admin, agent['id'], chat['id'], 'Second actual use')
    await execute_run(app.state.container, UUID(second['id']))
    unused = await admin.patch(
        '/api/agents/' + agent['id'],
        json={
            **config(model_id, name='UNUSED DRAFT NAME', skill_slugs=[first_skill['slug']]),
            'expected_version': published.json()['version'],
        },
    )
    assert unused.status_code == 200, unused.text
    async with app.state.container.database.sessions() as session:
        first_run = await session.get(Run, UUID(first['id']))
        second_run = await session.get(Run, UUID(second['id']))
        assert first_run.status == second_run.status == 'completed'
        expected = {first_skill['id']: first_run.created_at, second_skill['id']: second_run.created_at}
    response = await admin.get('/api/overview')
    assert response.status_code == 200, response.text
    recent = response.json()['recent']
    actual_agent = next(item for item in recent if item['kind'] == 'agent' and item['id'] == agent['id'])
    assert actual_agent['name'] == 'Second accepted name'
    for identifier, used_at in expected.items():
        item = next(item for item in recent if item['kind'] == 'skill' and item['id'] == identifier)
        assert datetime.fromisoformat(item['occurred_at'].replace('Z', '+00:00')) == used_at
    assert len(provider.requests) == 2


async def work_recent_fixture(admin: httpx.AsyncClient, app: FastAPI, model_id: str) -> tuple[dict, dict, dict]:
    from tests.core.integration.test_skills import install
    from tests.core.integration.test_work import published, task
    from zhiheng.modules.agents.orm import Agent
    from zhiheng.modules.skills.orm import Skill
    from zhiheng.shared.time import utcnow

    frozen = await install(admin, 'workbench-work-frozen', 'WORK FROZEN SKILL ONLY MARKER')
    future = await install(admin, 'workbench-work-future', 'WORK FUTURE SKILL MUST NOT APPEAR')
    agent = await published(admin, model_id, name='Work recent only', skill_slugs=[frozen['slug']])
    work = await task(admin, agent, goal='One real Work-only overview request')
    # The Task retains published version1 although the live Agent now publishes
    # version2 with another skill. Recent-use references must come from the turn.
    changed = await admin.patch(
        '/api/agents/' + agent['id'],
        json=config(
            model_id,
            application_id=agent['application_id'],
            name='Work current publication changed',
            skill_slugs=[future['slug']],
            expected_version=2,
        ),
    )
    assert changed.status_code == 200, changed.text
    republished = await admin.post('/api/agents/' + agent['id'] + '/enable', json={'expected_version': 3})
    assert republished.status_code == 200, republished.text
    old = utcnow() - timedelta(days=40)
    async with app.state.container.database.sessions.begin() as session:
        for skill in (frozen, future):
            row = await session.get(Skill, UUID(skill['id']))
            row.created_at = old
            row.updated_at = old
        owner_agent = await session.get(Agent, UUID(agent['id']))
        owner_agent.created_at = old
    return agent, work, frozen


async def work_recent_native_facts(app: FastAPI, run_id: UUID, agent: dict, frozen: dict) -> tuple[datetime, datetime]:
    from sqlalchemy import func, select

    from zhiheng.modules.agents.orm import AgentRunAudit
    from zhiheng.modules.work.orm import WorkMessage

    async with app.state.container.database.sessions() as session:
        row = await session.get(Run, run_id)
        assert row.resource_type == 'work_turn' and row.status == 'completed' and row.provider_started
        assert not await session.scalar(
            select(func.count()).select_from(AgentRunAudit).where(AgentRunAudit.run_id == run_id)
        ), 'A real Work event must not be made visible by fabricating an AgentRunAudit'
        message = await session.scalar(
            select(WorkMessage).where(WorkMessage.run_id == run_id, WorkMessage.role == 'assistant')
        )
        assert str(message.agent_id) == agent['id'] and message.agent_revision == 2
        assert {item['slug'] for item in message.audit['skills']} == {frozen['slug']}
        return row.created_at, message.created_at


async def work_recent_private_and_deleted(
    admin: httpx.AsyncClient, app: FastAPI, agent: dict, work: dict, frozen: dict
) -> None:
    permissions = (
        grants('platform:overview', 'platform_stats')
        | grants('platform:usage')
        | grants('platform:agents')
        | grants('platform:skills')
        | grants('platform:work')
    )
    ids = {agent['id'], frozen['id']}
    async with account(admin, app, *permissions) as (foreign, _, _):
        denied = await foreign.get('/api/work/tasks/' + work['id'])
        assert denied.status_code == 404
        response = await foreign.get('/api/overview?scope=platform')
        assert response.status_code == 200, response.text
        value = response.json()
        assert value['metrics']['today_calls'] == 1
        assert not any(item['id'] in ids for item in value['recent'])
    detail = await admin.get('/api/work/tasks/' + work['id'])
    assert detail.status_code == 200, detail.text
    removed = await admin.delete(
        '/api/work/tasks/' + work['id'],
        params={'expected_version': detail.json()['version']},
        headers={'content-type': 'application/json'},
    )
    assert removed.status_code == 204, removed.text
    after = await admin.get('/api/overview')
    assert after.status_code == 200, after.text
    assert not any(item['id'] in ids for item in after.json()['recent'])
    assert after.json()['metrics']['today_calls'] == 1, 'Deleting private history does not erase native usage'


async def test_real_work_recent_uses_frozen_turn_refs_and_respects_owner_and_deletion(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.core.integration.test_work import turn
    from zhiheng.shared.time import utcnow
    from zhiheng.worker.execution import execute_run

    # test_workbench.py has an autouse fixed-time fixture for seeded calendar
    # cases. Native execution creates current timestamps, so use its real clock.
    monkeypatch.setattr('zhiheng.application.overview.utcnow', utcnow)
    agent, work, frozen = await work_recent_fixture(admin, app, model_id)
    ids = {agent['id'], frozen['id']}
    before = await admin.get('/api/overview')
    assert before.status_code == 200, before.text
    assert before.json()['metrics']['today_calls'] == 0
    assert not any(item['id'] in ids for item in before.json()['recent'])
    admitted = await admin.post('/api/work/tasks/' + work['id'] + '/runs', json=turn(work, agent))
    assert admitted.status_code == 202, admitted.text
    run_id = UUID(admitted.json()['id'])
    await execute_run(app.state.container, run_id)
    event_times = await work_recent_native_facts(app, run_id, agent, frozen)
    assert len(provider.requests) == 1
    assert 'WORK FROZEN SKILL ONLY MARKER' in str(provider.requests)
    assert 'WORK FUTURE SKILL MUST NOT APPEAR' not in str(provider.requests)
    detail = await admin.get('/api/work/tasks/' + work['id'])
    assert detail.status_code == 200, detail.text
    upgraded = await admin.patch(
        '/api/work/tasks/' + work['id'] + '/members/' + agent['id'],
        json={
            'expected_version': detail.json()['version'],
            'expected_agent_revision': 4,
        },
    )
    assert upgraded.status_code == 200, upgraded.text
    response = await admin.get('/api/overview')
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['metrics']['today_calls'] == 1
    selected = [item for item in value['recent'] if item['id'] in ids]
    assert {(item['kind'], item['id']) for item in selected} == {('agent', agent['id']), ('skill', frozen['id'])}
    for item in selected:
        occurred = datetime.fromisoformat(item['occurred_at'].replace('Z', '+00:00'))
        assert min(event_times) <= occurred <= max(event_times), (
            'Use the genuine turn time, not resource creation/update'
        )
    assert 'WORK FROZEN SKILL ONLY MARKER' not in response.text
    await work_recent_private_and_deleted(admin, app, agent, work, frozen)
