from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.core.integration.test_assistant_langchain import assistant, execute, send
from tests.core.integration.test_builtin_role_migration import _version
from tests.core.integration.test_skills import install
from tests.core.permission_support import account, grants
from tests.core.provider import ProviderServer
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.agents.public import AgentConfig
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.skills.orm import Skill, SkillRevision
from zhiheng.modules.skills.public import SkillService, require_frozen_available, resolve_selection
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError


async def test_upgrade_moves_only_admin_presets_into_a_private_library(settings: Settings) -> None:
    await _version(settings, '0047_platform_skills')
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    skills = SkillService()
    try:
        async with sessions.begin() as session:
            identity = IdentityService()
            root = await identity.create_user(session, username='admin', password='platform-test-password', is_admin=True)
            other = await identity.create_user(session, username='other', password='platform-test-password', is_admin=True)
            for owner in (root, other):
                await skills.initialize_admin_library(session, owner.id)
            await session.execute(update(Skill).where(Skill.owner_id.is_not(None)).values(source='builtin'))
            copies = (await session.scalars(select(Skill).where(Skill.owner_id.is_not(None)))).all()
            admin_copy = next(row for row in copies if row.owner_id == root.id and row.slug == 'writing-polish')
            admin_copy.status = 'disabled'
            revisions = {row.id: row.revision_id for row in copies}

        await migrate(settings)
        async with sessions.begin() as session:
            library = await skills.list_skills(session, root.id, source='upload')
            assert library.total == 5 and all(item.source == 'upload' for item in library.items)
            assert next(item for item in library.items if item.id == admin_copy.id).status == 'disabled'
            assert all(item.platform_revision_id is None for item in library.items)
            assert (await skills.list_skills(session, root.id, source='platform')).total == 0
            assert (await skills.list_skills(session, other.id, source='upload')).total == 0
            assert await skills.available(session, other.id) == []
            for skill_id, revision_id in revisions.items():
                row = await session.get(Skill, skill_id)
                revision = await session.get(SkillRevision, revision_id)
                assert row is not None and row.revision_id == revision_id and row.version == 2
                assert (row.deleted_at is not None) == (row.owner_id == other.id)
                assert revision is not None and revision.package_bytes
            selected = next(item for item in library.items if item.status == 'enabled')
            await skills.publish_platform(session, root, selected.id, selected.version)
            assert (await skills.list_skills(session, other.id, source='platform')).total == 1
    finally:
        await engine.dispose()


async def test_platform_publication_uses_latest_release_and_preserves_frozen_execution(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    old = await install(admin, 'manual', 'Original instruction')
    path = f'/api/skills/{old["id"]}/platform'
    release = await admin.put(path, json={'expected_version': old['version']})
    assert release.status_code == 200, release.text
    async with account(admin, app, *grants('platform:skills', 'use', 'update')) as (user, _, account_info):
        owner = UUID(account_info['id'])
        visible = (await user.get('/api/skills', params={'source': 'platform'})).json()['items']
        published = next(item for item in visible if item['source'] == 'platform')
        assert published['revision_id'] == old['revision_id']
        assert (await user.get(f'/api/skills/{old["id"]}/content')).status_code == 404
        assert (await user.put(path, json={'expected_version': release.json()['version']})).status_code == 403
        available = (await user.get('/api/skills/available')).json()
        key = next(item['slug'] for item in available if item['source'] == 'platform')
        database = app.state.container.database
        async with database.sessions.begin() as session:
            original = await resolve_selection(session, owner, [key])
        assert 'Original instruction' in original.revisions[0].content

        updated = await install(admin, 'manual', 'Updated instruction')
        async with database.sessions.begin() as session:
            still_old = await resolve_selection(session, owner, [key])
        assert still_old == original
        republished = await admin.put(path, json={'expected_version': updated['version']})
        assert republished.status_code == 200, republished.text
        async with database.sessions.begin() as session:
            latest = await resolve_selection(session, owner, [key])
            await require_frozen_available(session, owner, original)
            package = await app.state.container.skills.frozen_package(session, owner, original.revisions[0])
        assert 'Updated instruction' in latest.revisions[0].content
        assert 'Original instruction' in package.content

        hide = await admin.request('DELETE', path, json={'expected_revision_id': updated['revision_id']})
        assert hide.status_code == 204, hide.text
        assert key not in {item['slug'] for item in (await user.get('/api/skills/available')).json()}
        async with database.sessions.begin() as session:
            with pytest.raises(AppError, match='所选技能不存在或已停用'):
                await resolve_selection(session, owner, [key])


async def test_role_delegates_skill_publication_only_for_owned_skills(admin: httpx.AsyncClient, app: FastAPI) -> None:
    permission = 'platform:skills:platform_publish'
    catalog = await admin.get('/api/admin/permissions')
    assert catalog.status_code == 200, catalog.text
    assert next(item for item in catalog.json()['items'] if item['code'] == permission)['label'] == '平台发布'
    other = await install(admin, 'other-skill')
    other_path = f'/api/skills/{other["id"]}/platform'
    other_release = await admin.put(other_path, json={'expected_version': other['version']})
    assert other_release.status_code == 200, other_release.text

    initial = grants('platform:skills', 'create', 'update')
    async with account(admin, app, *initial) as (publisher, role, _):
        skill = await install(publisher, 'owned-skill')
        path = f'/api/skills/{skill["id"]}/platform'
        role_path = f'/api/admin/roles/{role["id"]}'
        assert (await publisher.put(path, json={'expected_version': skill['version']})).status_code == 403

        authorized = await admin.patch(
            role_path, json={'permissions': sorted(initial | {permission}), 'expected_version': role['version']}
        )
        assert authorized.status_code == 200, authorized.text
        published = await publisher.put(path, json={'expected_version': skill['version']})
        assert published.status_code == 200, published.text
        assert (
            await publisher.put(other_path, json={'expected_version': other_release.json()['version']})
        ).status_code == 404
        assert (
            await publisher.request('DELETE', other_path, json={'expected_revision_id': other['revision_id']})
        ).status_code == 404

        draft = await install(publisher, 'owned-skill', 'Updated instructions')
        updated = await publisher.put(path, json={'expected_version': draft['version']})
        assert updated.status_code == 200, updated.text
        assert updated.json()['platform_revision_id'] == draft['revision_id']
        withdrawal = {'expected_revision_id': draft['revision_id']}
        assert (await publisher.request('DELETE', path, json=withdrawal)).status_code == 204
        personal = (await publisher.get(f'/api/skills/{skill["id"]}')).json()
        assert personal['platform_revision_id'] is None
        assert (await publisher.put(path, json={'expected_version': personal['version']})).status_code == 200

        revoked = await admin.patch(
            role_path, json={'permissions': sorted(initial), 'expected_version': authorized.json()['version']}
        )
        assert revoked.status_code == 200, revoked.text
        assert (await publisher.request('DELETE', path, json=withdrawal)).status_code == 403
        assert (await admin.request('DELETE', path, json=withdrawal)).status_code == 204
        remaining = (await admin.get('/api/skills', params={'source': 'platform'})).json()['items']
        assert [item['id'] for item in remaining] == [other['id']]
        logs = (
            await admin.get('/api/admin/logs', params={'kind': 'operation', 'search': '平台技能'})
        ).json()['items']
        owned = [row for row in logs if row['resource_id'] == skill['id']]
        assert sum(row['action'] == 'skill.platform.publish' for row in owned) == 3
        assert sum(row['action'] == 'skill.platform.withdraw' for row in owned) == 2
        assert owned[0]['username'] == 'admin'


async def test_platform_skill_is_selectable_in_work_and_runs_in_plaza_assistant(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    instruction = 'PLATFORM SKILL TEST INSTRUCTION'
    skill = await install(admin, 'reference-workflow', instruction)
    published = await admin.put(f'/api/skills/{skill["id"]}/platform', json={'expected_version': skill['version']})
    assert published.status_code == 200, published.text
    available = (await admin.get('/api/skills/available')).json()
    slug = next(item['slug'] for item in available if item['source'] == 'platform' and item['id'] == skill['id'])
    agent = await assistant(admin, model_id, skill_slugs=[slug])
    base = f'/api/agents/{agent["id"]}'
    promoted = await admin.put(f'{base}/plaza', json={'expected_version': agent['version']})
    assert promoted.status_code == 200, promoted.text

    async with account(admin, app, *grants('platform:agents', 'use')) as (member, _, _):
        chat = await member.post(f'{base}/conversations?plaza=true', json={'client_request_id': str(uuid4())})
        assert chat.status_code == 201, chat.text
        run = await send(member, agent['id'], chat.json()['id'], 'Hi')
        await execute(app, run['id'])
        assert any(
            instruction in str(message['content'])
            for message in provider.requests[-1]['messages']
            if message['role'] == 'system'
        )

    permissions = grants('platform:skills', 'use') | grants('platform:work', 'create', 'use')
    async with account(admin, app, *permissions) as (worker, _, _):
        options = await worker.get('/api/work/options')
        assert options.status_code == 200, options.text
        assert slug in {item['slug'] for item in options.json()['skills']}
        request_id = str(uuid4())
        goal = 'Check routing'
        task = await worker.post(
            '/api/work/tasks',
            json={'client_request_id': request_id, 'model_id': model_id, 'goal': goal, 'skill_slugs': [slug]},
        )
        assert task.status_code == 201, task.text
        run = await worker.post(
            f'/api/work/tasks/{task.json()["id"]}/runs',
            json={'client_request_id': request_id, 'expected_version': task.json()['version'], 'message': goal},
        )
        assert run.status_code == 202, run.text
        async with app.state.container.database.sessions() as session:
            accepted = await session.get(Run, UUID(run.json()['id']))
            assert accepted is not None
            assert accepted.payload['skills'][0]['revision_id'] == skill['revision_id']


async def test_private_skill_is_used_only_through_its_published_assistant(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    marker = 'OWNER PRIVATE ASSISTANT SKILL'
    skill = await install(admin, 'private-assistant-workflow', marker)
    agent = await assistant(admin, model_id, skill_slugs=[skill['slug']])
    base = f'/api/agents/{agent["id"]}'
    published = await admin.put(f'{base}/plaza', json={'expected_version': agent['version']})
    assert published.status_code == 200, published.text

    async with account(admin, app, *grants('platform:agents', 'use'), *grants('platform:skills')) as (visitor, _, _):
        library = (await visitor.get('/api/skills', params={'source': 'upload'})).json()
        assert all(item['id'] != skill['id'] for item in library['items'])
        skill_path = f'/api/skills/{skill["id"]}'
        assert (await visitor.get(skill_path)).status_code == 404
        assert (await visitor.get(f'{skill_path}/content')).status_code == 404
        assert (await visitor.get(f'{skill_path}/references')).status_code == 404
        chat = await visitor.post(f'{base}/conversations?plaza=true', json={'client_request_id': str(uuid4())})
        assert chat.status_code == 201, chat.text
        assert marker not in chat.text and skill['slug'] not in chat.text
        run = await send(visitor, agent['id'], chat.json()['id'], 'Hi')
        await execute(app, run['id'])
        done = await visitor.get(f'/api/runs/{run["id"]}')
        assert done.status_code == 200, done.text
        assert done.json()['status'] == 'completed' and marker not in done.text
        assert any(marker in str(message['content']) for message in provider.requests[-1]['messages'])

        queued = await send(visitor, agent['id'], chat.json()['id'], 'Follow-up')
        refs = (await admin.get(f'{skill_path}/references')).json()['references']
        assert {'agent', 'plaza_agent', 'run'} <= {row['kind'] for row in refs}
        assert any(row['kind'] == 'run' and row['id'] is None and row['path'] is None for row in refs)
        disabled = await admin.patch(
            f'{skill_path}/status', json={'status': 'disabled', 'expected_version': skill['version']}
        )
        assert disabled.status_code == 409, disabled.text
        conflict = disabled.json()['error']
        assert conflict['code'] == 'skill_assistant_referenced'
        assert {row['kind'] for row in conflict['details']['references']} == {'agent', 'plaza_agent'}
        await execute(app, queued['id'])
        follow_up = (await visitor.get(f'/api/runs/{queued["id"]}')).json()
        assert follow_up['status'] == 'completed'

        config = {key: value for key, value in agent.items() if key in AgentConfig.model_fields}
        updated = await admin.patch(
            base, json={**config, 'skill_slugs': [], 'expected_version': published.json()['version']}
        )
        assert updated.status_code == 200, updated.text
        refs = (await admin.get(f'{skill_path}/references')).json()['references']
        assert {row['kind'] for row in refs} == {'plaza_agent'}
        assert refs[0]['path'] == f'/agents/{agent["id"]}'
        blocked = await admin.patch(
            f'{skill_path}/status', json={'status': 'disabled', 'expected_version': skill['version']}
        )
        assert blocked.status_code == 409
        assert blocked.json()['error']['details']['references'] == refs
        removed = await admin.request('DELETE', skill_path, json={})
        assert removed.status_code == 409
        assert removed.json()['error']['details']['references'] == refs

        republished = await admin.put(f'{base}/plaza', json={'expected_version': updated.json()['version']})
        assert republished.status_code == 200, republished.text
        disabled = await admin.patch(
            f'{skill_path}/status', json={'status': 'disabled', 'expected_version': skill['version']}
        )
        assert disabled.status_code == 200 and disabled.json()['status'] == 'disabled'
