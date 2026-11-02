"""Personal resource boundaries use real PostgreSQL and the ordinary console routes."""

import httpx

from tests.core.integration.test_agents import config
from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_skills import install


async def test_same_slug_is_independent_and_owner_can_restore(admin: httpx.AsyncClient, app: object) -> None:
    mine = await install(admin, 'personal-same', 'ADMIN CONTENT')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        theirs = await install(member, 'personal-same', 'MEMBER CONTENT')
        assert theirs['id'] != mine['id']
        assert theirs['revision_id'] != mine['revision_id']
        assert (await member.get(f'/api/skills/{mine["id"]}')).status_code == 404
        assert (await member.get(f'/api/skills/{theirs["id"]}/content')).json()['content'].endswith('MEMBER CONTENT')
        assert (
            await member.patch(
                f'/api/skills/{theirs["id"]}/status',
                json={
                    'status': 'disabled',
                    'expected_version': theirs['version'],
                },
            )
        ).status_code == 200
        assert (
            await member.delete(f'/api/skills/{theirs["id"]}', headers={'content-type': 'application/json'})
        ).status_code == 200
        restored = await install(member, 'personal-same', 'RESTORED')
        assert restored['id'] == theirs['id']
        assert restored['status'] == 'enabled'
        assert {row['id'] for row in (await member.get('/api/skills')).json()['items']} == {
            row['id'] for row in (await member.get('/api/skills/available')).json()
        }
        assert mine['id'] not in {row['id'] for row in (await member.get('/api/skills')).json()['items']}


async def test_administrator_cannot_read_or_mutate_another_users_skill(admin: httpx.AsyncClient, app: object) -> None:
    from zhiheng.modules.identity.public import IdentityService

    async with app.state.container.database.sessions.begin() as session:
        await IdentityService().create_user(
            session,
            username='other-owner',
            password='platform-test-password',
            is_admin=True,
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'other-owner', 'password': 'platform-test-password'})
        theirs = await install(member, 'member-secret')
        for suffix in ('', '/content'):
            assert (await admin.get(f'/api/skills/{theirs["id"]}{suffix}')).status_code == 404
        assert (
            await admin.patch(
                f'/api/skills/{theirs["id"]}/status',
                json={
                    'status': 'disabled',
                    'expected_version': 1,
                },
            )
        ).status_code == 404
        assert (
            await admin.delete(f'/api/skills/{theirs["id"]}', headers={'content-type': 'application/json'})
        ).status_code == 404
        assert (await member.get(f'/api/skills/{theirs["id"]}')).status_code == 200


async def test_builtin_copies_are_initialized_when_creating_accounts_and_are_independent(
    admin: httpx.AsyncClient,
    app: object,
) -> None:
    mine = (await admin.get('/api/skills/available')).json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        theirs = (await member.get('/api/skills/available')).json()
        assert {row['slug'] for row in mine} == {row['slug'] for row in theirs}
        assert {row['id'] for row in mine}.isdisjoint(row['id'] for row in theirs)
        assert (
            await admin.delete(f'/api/skills/{mine[0]["id"]}', headers={'content-type': 'application/json'})
        ).status_code == 200
        assert (await member.get(f'/api/skills/{theirs[0]["id"]}')).status_code == 200
        assert mine[0]['id'] not in {row['id'] for row in (await admin.get('/api/skills/available')).json()}


async def test_private_knowledge_owner_only_and_explicit_share_is_read_only(
    admin: httpx.AsyncClient,
    app: object,
    provider: object,
) -> None:
    model = await embedding_model(admin, provider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        created = await member.post('/api/knowledge/bases', json={'name': 'Member private', 'embed_model_id': model})
        assert created.status_code == 201, created.text
        base = created.json()
        assert (await admin.get('/api/knowledge/bases')).json() == []
        assert (await admin.get(f'/api/knowledge/bases/{base["id"]}')).status_code == 404
        assert (
            await admin.patch(
                f'/api/knowledge/bases/{base["id"]}',
                json={
                    'name': 'stolen',
                    'expected_version': base['version'],
                },
            )
        ).status_code == 404
        shared = await member.patch(
            f'/api/knowledge/bases/{base["id"]}/acl',
            json={
                'access': 'public',
                'entries': [],
                'expected_version': base['acl_version'],
            },
        )
        assert shared.status_code == 200, shared.text
        assert (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()['can_manage'] is False
        assert (await admin.get('/api/knowledge/bases')).json() == []
        assert (await admin.post(f'/api/knowledge/bases/{base["id"]}/folders', json={'name': 'no'})).status_code == 404
        assert (await member.get('/api/knowledge/bases')).json()[0]['id'] == base['id']


async def test_another_owners_same_slug_agent_does_not_block_deletion(
    admin: httpx.AsyncClient,
    app: object,
    model_id: str,
) -> None:
    mine = await install(admin, 'referenced-personally')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        theirs = await install(member, 'referenced-personally')
        created = await member.post('/api/agents', json=config(model_id, skill_slugs=[theirs['slug']]))
        assert created.status_code == 201, created.text
        assert (
            await admin.delete(
                f'/api/skills/{mine["id"]}',
                headers={
                    'content-type': 'application/json',
                },
            )
        ).status_code == 200
        assert (
            await member.delete(
                f'/api/skills/{theirs["id"]}',
                headers={
                    'content-type': 'application/json',
                },
            )
        ).status_code == 409


async def test_account_resources_share_the_account_transaction_and_reads_do_not_initialize(
    app: object,
) -> None:
    import pytest
    from sqlalchemy import func, select, text

    from zhiheng.modules.identity.orm import IdentityUser
    from zhiheng.modules.identity.public import IdentityService
    from zhiheng.modules.skills.orm import Skill

    container = app.state.container
    async with container.database.sessions.begin() as session:
        raw = await IdentityService().create_user(session, username='raw-data-user', password='platform-test-password')
    async with container.database.sessions.begin() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        before = await session.scalar(select(func.count()).select_from(Skill))
        assert (await container.skills.list_skills(session, raw.id)).items == []
        assert await container.skills.available(session, raw.id) == []
        assert await session.scalar(select(func.count()).select_from(Skill)) == before
    with pytest.raises(RuntimeError, match='rollback account'):
        async with container.database.sessions.begin() as session:
            user = await container.accounts.create_user(
                session, username='rollback-user', password='platform-test-password'
            )
            assert len(await container.skills.available(session, user.id)) == 2
            raise RuntimeError('rollback account')
    async with container.database.sessions() as session:
        assert await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'rollback-user')) is None
        assert await session.scalar(select(func.count()).select_from(Skill)) == before


async def test_administrator_preview_and_work_use_agent_owner_and_frozen_revision(
    admin: httpx.AsyncClient,
    app: object,
    model_id: str,
    provider: object,
) -> None:
    from uuid import UUID, uuid4

    from tests.core.integration.test_work import published, task, turn
    from zhiheng.worker.execution import execute_run

    await install(admin, 'owned-execution', 'ACTOR MUST NOT SUPPLY THIS CONTENT')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        original = await install(member, 'owned-execution', 'FROZEN OWNER CONTENT')
        agent = await published(member, model_id, skill_slugs=[original['slug']])
        preview = await admin.post(
            f'/api/agents/{agent["id"]}/preview',
            json={
                'client_request_id': str(uuid4()),
                'messages': [{'role': 'user', 'content': 'preview'}],
            },
        )
        assert preview.status_code == 202, preview.text
        await execute_run(app.state.container, UUID(preview.json()['id']))
        assert 'FROZEN OWNER CONTENT' in provider.requests[-1]['messages'][0]['content']
        work = await task(admin, agent)
        await install(member, 'owned-execution', 'LATEST OWNER CONTENT')
        run = await admin.post(f'/api/work/tasks/{work["id"]}/runs', json=turn(work, agent))
        assert run.status_code == 202, run.text
        await execute_run(app.state.container, UUID(run.json()['id']))
        messages = provider.requests[-1]['messages']
        assert any('FROZEN OWNER CONTENT' in row['content'] for row in messages)
        assert not any('ACTOR MUST NOT' in row['content'] or 'LATEST OWNER' in row['content'] for row in messages)


async def test_upgraded_database_rejects_unmarked_ownerless_resources(app: object) -> None:
    from uuid import uuid4

    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    container = app.state.container
    async with container.database.sessions() as session:
        constraints = set(
            await session.scalars(text("SELECT conname FROM pg_constraint WHERE conrelid='skills'::regclass"))
        )
        assert 'ck_skills_archive_owner' in constraints
    for source in ('upload', 'builtin'):
        with pytest.raises(IntegrityError, match='ck_skills_archive_owner'):
            async with container.database.sessions.begin() as session:
                await session.execute(
                    text("""INSERT INTO skills
                    (id,slug,status,source,version,revision_id,created_at,updated_at)
                    VALUES (:id,'new-unowned','enabled',:source,1,:revision,now(),now())"""),
                    {'id': uuid4(), 'source': source, 'revision': uuid4()},
                )
