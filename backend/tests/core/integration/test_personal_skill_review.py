"""Regressions for trusted owners of legacy preparations and old Work recovery."""

from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from tests.core.integration.test_work import published, task, turn
from zhiheng.modules.knowledge.orm import RetrievalIntent
from zhiheng.modules.work.public import WorkTurn
from zhiheng.shared.time import utcnow
from zhiheng.worker.execution import execute_run


@pytest.mark.parametrize('owner_hint', [False, True])
async def test_legacy_builtin_preparation_owner_precedes_shared_global_id(
    admin: httpx.AsyncClient,
    app: object,
    model_id: str,
    owner_hint: bool,
) -> None:
    container = app.state.container
    mine = (await admin.get('/api/skills/available')).json()[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        theirs = next(row for row in (await member.get('/api/skills/available')).json() if row['slug'] == mine['slug'])
        agent = await published(member, model_id, skill_slugs=[mine['slug']])
        work = await task(member, agent)
        assert (await admin.get('/api/work/tasks/' + work['id'])).status_code == 404
        async with container.database.sessions.begin() as session:
            my_owner = await container.identity.authenticate(session, admin.cookies['zhiheng_session'])
            their_owner = await container.identity.authenticate(session, member.cookies['zhiheng_session'])
            mine_ids = await container.skills.reference_ids(session, my_owner.id, UUID(mine['id']))
            their_ids = await container.skills.reference_ids(session, their_owner.id, UUID(theirs['id']))
            legacy = next(value for value in mine_ids if value != UUID(mine['id']))
            dependencies = {
                'member_config': {'skill_slugs': [mine['slug']]},
                'member_skills': [{'skill_id': str(legacy), 'slug': mine['slug']}],
            }
            if owner_hint:
                dependencies['skill_owner_id'] = str(their_owner.id)
            intent = RetrievalIntent(
                namespace_id=my_owner.id,
                actor_id=my_owner.id,
                owner_id=my_owner.id,
                client_request_id=uuid4(),
                resource_type='work_turn',
                resource_id=UUID(work['id']),
                fingerprint='legacy',
                request={'body': {'agent_id': agent['id']}},
                dependencies=dependencies,
                deadline=utcnow() + timedelta(minutes=1),
            )
            session.add(intent)
            await session.flush()
            assert (
                await container.knowledge.skill_references(
                    session, mine['slug'], owner_id=my_owner.id, skill_ids=mine_ids
                )
                == []
            )
            refs = await container.knowledge.skill_references(
                session, mine['slug'], owner_id=their_owner.id, skill_ids=their_ids
            )
            assert [ref.id for ref in refs] == [intent.id]
        assert (
            await admin.delete(f'/api/skills/{mine["id"]}', headers={'content-type': 'application/json'})
        ).status_code == 200
        assert (
            await member.delete(f'/api/skills/{theirs["id"]}', headers={'content-type': 'application/json'})
        ).status_code == 409


@pytest.mark.parametrize('stage', ['check', 'finalize'])
async def test_old_work_preparation_without_owner_dependency_recovers(
    admin: httpx.AsyncClient,
    app: object,
    model_id: str,
    stage: str,
) -> None:
    container = app.state.container
    agent = await published(admin, model_id, skill_slugs=['document-summary'])
    work = await task(admin, agent)
    body = WorkTurn.model_validate(turn(work, agent))
    token = uuid4()
    async with container.database.sessions.begin() as session:
        owner = await container.identity.authenticate(session, admin.cookies['zhiheng_session'])
        binding = await container.models.resolve(session, UUID(model_id))
        dependencies = await container.work_runs._knowledge_dependencies(
            session, owner, UUID(work['id']), body, binding
        )
        dependencies.pop('skill_owner_id', None)
        dependencies['bases'] = []
        intent = RetrievalIntent(
            namespace_id=owner.id,
            actor_id=owner.id,
            owner_id=owner.id,
            client_request_id=body.client_request_id,
            resource_type='work_turn',
            resource_id=UUID(work['id']),
            fingerprint='old-work-format',
            status='ready',
            tick_token=token,
            request={'body': body.model_dump(mode='json'), 'admission_body': body.model_dump(mode='json')},
            dependencies=dependencies,
            deadline=utcnow() + timedelta(minutes=1),
        )
        session.add(intent)
        await session.flush()
        state = container.knowledge_preparation.store.state(intent)
        intent_id = intent.id
        if stage == 'check':
            await container.work_runs.check_knowledge_dependencies(session, state)
    if stage == 'finalize':
        await container.knowledge_preparation.finalize(intent_id, token)
        async with container.database.sessions() as session:
            restored = await session.scalar(select(RetrievalIntent).where(RetrievalIntent.id == intent_id))
            assert restored.status == 'admitted' and restored.main_run_id is not None, restored.error
            run_id = restored.main_run_id
        await execute_run(container, run_id)
        assert (await admin.get(f'/api/runs/{run_id}')).json()['status'] == 'completed'
