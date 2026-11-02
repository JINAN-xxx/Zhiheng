import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from tests.core.integration.test_agents import config
from tests.core.integration.test_skills import install, prepare
from tests.core.legacy_schema import reconstruct
from tests.core.unit.test_skill_package import markdown
from zhiheng.modules.skills.orm import Skill, SkillRevision
from zhiheng.modules.skills.package import SkillPackageParser
from zhiheng.shared.errors import AppError


async def confirm(admin: httpx.AsyncClient, draft: dict) -> httpx.Response:
    return await admin.post(f'/api/skills/import/{draft["draft_id"]}/confirm', json={})


async def remove(admin: httpx.AsyncClient, skill: dict) -> httpx.Response:
    return await admin.delete(f'/api/skills/{skill["id"]}', headers={'Content-Type': 'application/json'})


async def test_enabled_builtin_and_upload_can_be_removed_with_revisions_retained(
    admin: httpx.AsyncClient, app: object
) -> None:
    builtin = (await admin.get('/api/skills/available')).json()[0]
    uploaded = await install(admin, 'direct-remove')
    for skill in [builtin, uploaded]:
        response = await remove(admin, skill)
        assert response.status_code == 200, response.text
        assert (await admin.get(f'/api/skills/{skill["id"]}')).status_code == 404
        async with app.state.container.database.sessions() as session:
            stored = await session.get(Skill, UUID(skill['id']))
            revision = await session.get(SkillRevision, UUID(skill['revision_id']))
            assert stored.deleted_at is not None
            assert revision.content and revision.package_bytes


async def test_prepare_distinguishes_install_unchanged_update_restore_and_freezes_confirmation(
    admin: httpx.AsyncClient, app: object
) -> None:
    initial = await prepare(admin, 'identity')
    assert initial['action'] == 'install' and initial['target_skill_id'] is None
    first = (await confirm(admin, initial)).json()
    unchanged = await prepare(admin, 'identity')
    assert unchanged['action'] == 'unchanged'
    assert unchanged['target_skill_id'] == first['id']
    assert unchanged['expected_version'] == first['version']
    assert unchanged['current_package_version'] == '1.0.0'
    assert (await confirm(admin, unchanged)).json() == first
    changed = await prepare(admin, 'identity', 'NEW CONTENT')
    assert changed['action'] == 'update'
    response = await confirm(admin, changed)
    assert response.status_code == 201, response.text
    second = response.json()
    assert second['id'] == first['id'] and second['revision_id'] != first['revision_id']
    assert second['version'] == first['version'] + 1
    assert (await confirm(admin, initial)).json() == first
    assert (await confirm(admin, unchanged)).json() == first
    assert (await remove(admin, second)).status_code == 200
    restore = await prepare(admin, 'identity', 'NEW CONTENT')
    assert restore['action'] == 'restore' and restore['target_skill_id'] == first['id']
    response = await confirm(admin, restore)
    assert response.status_code == 201, response.text
    restored = response.json()
    assert restored['id'] == first['id'] and restored['revision_id'] != second['revision_id']
    assert restored['status'] == 'enabled'
    assert (await confirm(admin, changed)).json() == second
    assert (await confirm(admin, restore)).json() == restored
    async with app.state.container.database.sessions() as session:
        revisions = (
            await session.scalars(select(SkillRevision).where(SkillRevision.skill_id == UUID(first['id'])))
        ).all()
        assert len(revisions) == 3
        assert {row.content for row in revisions} == {
            markdown('identity').decode(),
            markdown('identity', 'NEW CONTENT').decode(),
        }


async def test_update_previews_use_cas_and_confirmation_retry_adds_no_revision(
    admin: httpx.AsyncClient, app: object
) -> None:
    original = await install(admin, 'cas-update')
    drafts = [await prepare(admin, original['slug'], body) for body in ['FIRST', 'SECOND']]
    responses = await asyncio.gather(*(confirm(admin, draft) for draft in drafts))
    assert sorted(response.status_code for response in responses) == [201, 409]
    winner = next(index for index, response in enumerate(responses) if response.status_code == 201)
    loser = responses[1 - winner]
    assert loser.json()['error']['code'] == 'skill_version_conflict'
    retries = await asyncio.gather(*(confirm(admin, drafts[winner]) for _ in range(2)))
    assert all(response.json() == responses[winner].json() for response in retries)
    async with app.state.container.database.sessions() as session:
        revisions = (
            await session.scalars(select(SkillRevision).where(SkillRevision.skill_id == UUID(original['id'])))
        ).all()
        assert len(revisions) == 2


async def test_restore_preview_cannot_overwrite_a_later_restore(admin: httpx.AsyncClient) -> None:
    original = await install(admin, 'cas-restore')
    assert (await remove(admin, original)).status_code == 200
    drafts = [await prepare(admin, original['slug'], body) for body in ['FIRST RESTORE', 'SECOND RESTORE']]
    responses = await asyncio.gather(*(confirm(admin, draft) for draft in drafts))
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()['error']['code']
        == 'skill_version_conflict'
    )


async def test_prepare_rejects_status_or_deletion_changes_before_confirmation(admin: httpx.AsyncClient) -> None:
    original = await install(admin, 'stale-preview')
    drafts = [await prepare(admin, original['slug'], 'UPDATE'), await prepare(admin, original['slug'])]
    assert (
        await admin.patch(f'/api/skills/{original["id"]}/status', json={'status': 'disabled', 'expected_version': 1})
    ).status_code == 200
    for draft in drafts:
        response = await confirm(admin, draft)
        assert response.status_code == 409 and response.json()['error']['code'] == 'skill_version_conflict'
    deletion_draft = await prepare(admin, original['slug'], 'AFTER DISABLE')
    assert (await remove(admin, original)).status_code == 200
    response = await confirm(admin, deletion_draft)
    assert response.status_code == 409 and response.json()['error']['code'] == 'skill_version_conflict'


async def test_active_agent_reference_is_listed_but_deleted_agent_history_does_not_block(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    skill = await install(admin, 'agent-history')
    agent = (await admin.post('/api/agents', json=config(model_id, skill_slugs=[skill['slug']]))).json()
    blocked = await remove(admin, skill)
    assert blocked.status_code == 409, blocked.text
    references = blocked.json()['error']['details']['references']
    assert any(item['id'] == agent['id'] and item['name'] == agent['name'] for item in references)
    assert (
        await admin.delete(f'/api/agents/{agent["id"]}', headers={'Content-Type': 'application/json'})
    ).status_code == 200
    response = await remove(admin, skill)
    assert response.status_code == 200, response.text


async def test_inflight_chat_blocks_deletion_and_old_messages_keep_revision_after_update(
    admin: httpx.AsyncClient, app: object, model_id: str, provider: object
) -> None:
    from zhiheng.worker.execution import execute_run

    skill = await install(admin, 'chat-history', 'OLD CHAT INSTRUCTION')
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    response = await admin.post(
        f'/api/chat/conversations/{conversation}/runs',
        json={
            'model_id': model_id,
            'message': 'hello',
            'client_request_id': str(uuid4()),
            'skill_slugs': [skill['slug']],
        },
    )
    assert response.status_code == 202, response.text
    run = response.json()
    blocked = await remove(admin, skill)
    assert blocked.status_code == 409, blocked.text
    assert any(
        item['kind'] == 'chat' and item['id'] == conversation
        for item in blocked.json()['error']['details']['references']
    )
    update = await prepare(admin, skill['slug'], 'NEW CHAT INSTRUCTION')
    assert (await confirm(admin, update)).status_code == 201
    await execute_run(app.state.container, UUID(run['id']))
    assert 'OLD CHAT INSTRUCTION' in str(provider.requests[-1])
    assert 'NEW CHAT INSTRUCTION' not in str(provider.requests[-1])
    messages = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    assert messages[0]['skill_snapshots'][0]['revision_id'] == skill['revision_id']
    assert (await remove(admin, skill)).status_code == 200


async def test_delete_holds_skill_lock_until_new_chat_admission_checks_availability(
    admin: httpx.AsyncClient, app: object, model_id: str
) -> None:
    skill = await install(admin, 'serialized-delete')
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    container = app.state.container
    async with container.database.sessions.begin() as session:
        principal = await container.identity.authenticate(session, admin.cookies['zhiheng_session'])
        await container.skill_management.remove(session, principal, UUID(skill['id']))
        admission = asyncio.create_task(
            admin.post(
                f'/api/chat/conversations/{conversation}/runs',
                json={
                    'model_id': model_id,
                    'message': 'hello',
                    'client_request_id': str(uuid4()),
                    'skill_slugs': [skill['slug']],
                },
            )
        )
        await asyncio.sleep(0.05)
        assert not admission.done()
    response = await asyncio.wait_for(admission, 2)
    assert response.status_code == 422 and response.json()['error']['code'] == 'skill_unavailable'


async def test_removed_work_member_and_old_publication_keep_history_without_blocking_deletion(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    from tests.core.integration.test_work import published, task

    skill = await install(admin, 'removed-work')
    first = await published(admin, model_id, skill_slugs=[skill['slug']])
    second = await published(admin, model_id)
    work = await task(admin, first, agent_ids=[first['id'], second['id']])
    response = await admin.patch(
        f'/api/agents/{first["id"]}',
        json=config(model_id, application_id=first['application_id'], expected_version=2),
    )
    assert response.status_code == 200, response.text
    assert (await admin.post(f'/api/agents/{first["id"]}/publish', json={'expected_version': 3})).status_code == 200
    blocked = await remove(admin, skill)
    assert blocked.status_code == 409, blocked.text
    assert any(item['id'] == work['id'] for item in blocked.json()['error']['details']['references'])
    response = await admin.delete(
        f'/api/work/tasks/{work["id"]}/members/{first["id"]}',
        params={'expected_version': work['version']},
        headers={'Content-Type': 'application/json'},
    )
    assert response.status_code == 200, response.text
    response = await remove(admin, skill)
    assert response.status_code == 200, response.text


async def test_package_update_preserves_published_and_work_member_content_until_republish(
    admin: httpx.AsyncClient, app: object, model_id: str, provider: object
) -> None:
    from tests.core.integration.test_work import published, task, turn
    from zhiheng.modules.agents.orm import AgentVersion
    from zhiheng.modules.work.orm import WorkMember
    from zhiheng.worker.execution import execute_run

    original = await install(admin, 'frozen-package', 'ORIGINAL PUBLISHED INSTRUCTION')
    agent = await published(admin, model_id, skill_slugs=[original['slug']])
    work = await task(admin, agent)
    response = await confirm(admin, await prepare(admin, original['slug'], 'UPDATED PUBLISHED INSTRUCTION'))
    assert response.status_code == 201, response.text
    updated = response.json()
    async with app.state.container.database.sessions() as session:
        publication = await session.scalar(select(AgentVersion).where(AgentVersion.agent_id == UUID(agent['id'])))
        member = await session.scalar(select(WorkMember).where(WorkMember.task_id == UUID(work['id'])))
        assert publication.skills[0]['revision_id'] == member.skills[0]['revision_id'] == original['revision_id']
        assert 'ORIGINAL PUBLISHED INSTRUCTION' in publication.skills[0]['content']
    response = await admin.post(f'/api/work/tasks/{work["id"]}/runs', json=turn(work, agent))
    assert response.status_code == 202, response.text
    await execute_run(app.state.container, UUID(response.json()['id']))
    assert 'ORIGINAL PUBLISHED INSTRUCTION' in str(provider.requests[-1])
    assert 'UPDATED PUBLISHED INSTRUCTION' not in str(provider.requests[-1])
    assert (await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 2})).status_code == 200
    async with app.state.container.database.sessions() as session:
        publications = (
            await session.scalars(
                select(AgentVersion).where(AgentVersion.agent_id == UUID(agent['id'])).order_by(AgentVersion.version)
            )
        ).all()
        member = await session.scalar(select(WorkMember).where(WorkMember.task_id == UUID(work['id'])))
        assert publications[0].skills[0]['revision_id'] == member.skills[0]['revision_id'] == original['revision_id']
        assert publications[1].skills[0]['revision_id'] == updated['revision_id']


async def test_downgrade_rejects_multiple_revisions_without_losing_history(
    admin: httpx.AsyncClient, app: object
) -> None:
    from zhiheng.bootstrap.migrations import migration_directory

    skill = await install(admin, 'migration-history')
    assert (await confirm(admin, await prepare(admin, skill['slug'], 'NEW REVISION'))).status_code == 201
    configuration = Config()
    configuration.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: object) -> None:
        configuration.attributes['connection'] = connection
        command.downgrade(configuration, '0012_platform_credits')

    async with app.state.container.database.engine.begin() as connection:

        def prepare_0013(current: object) -> None:
            configuration.attributes['connection'] = current
            command.upgrade(configuration, '0013_skill_revision_history')

        await reconstruct(connection, prepare_0013)
    await app.state.container.database.engine.dispose()

    with pytest.raises(RuntimeError, match='multiple revisions'):
        async with app.state.container.database.engine.begin() as connection:
            await connection.run_sync(downgrade)
    async with app.state.container.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0013_skill_revision_history'
        assert (
            await connection.scalar(
                text('SELECT count(*) FROM skill_revisions WHERE skill_id=:id'), {'id': UUID(skill['id'])}
            )
            == 2
        )


async def test_0012_upgrade_freezes_legacy_confirmation_and_rejects_stale_legacy_preview(
    admin: httpx.AsyncClient, app: object
) -> None:
    from datetime import timedelta

    from zhiheng.bootstrap.migrations import migrate, migration_directory
    from zhiheng.shared.time import utcnow

    container = app.state.container
    original = (await admin.get('/api/skills/available')).json()[0]
    async with container.database.sessions() as session:
        from zhiheng.modules.skills.orm import SkillLegacyBinding

        binding = await session.scalar(
            select(SkillLegacyBinding).where(SkillLegacyBinding.personal_skill_id == UUID(original['id']))
        )
        archive = await session.get(Skill, binding.legacy_skill_id)
        archived_revision = await session.get(SkillRevision, archive.revision_id)
        original = container.skills._info(archive, archived_revision).model_dump(mode='json')
    async with container.database.sessions.begin() as session:
        principal = await container.identity.authenticate(session, admin.cookies['zhiheng_session'])
        revision = await session.get(SkillRevision, UUID(original['revision_id']))
        payload, package_sha = revision.package_bytes, revision.package_sha
    configuration = Config()
    configuration.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: object) -> None:
        configuration.attributes['connection'] = connection
        command.upgrade(configuration, '0012_platform_credits')

    confirmed_id, preview_id = uuid4(), uuid4()
    async with container.database.engine.begin() as connection:
        await reconstruct(connection, downgrade)
        for draft_id, confirmed in [(confirmed_id, original['id']), (preview_id, None)]:
            await connection.execute(
                text("""INSERT INTO skill_drafts
                    (id,owner_id,created_at,expires_at,filename,package_bytes,package_sha,parsed_hash,confirmed_skill_id)
                    VALUES (:id,:owner,:created,:expires,'SKILL.md',:payload,:sha,:parsed,:confirmed)"""),
                {
                    'id': draft_id,
                    'owner': principal.id,
                    'created': utcnow(),
                    'expires': utcnow() + timedelta(hours=1),
                    'payload': payload,
                    'sha': package_sha,
                    'parsed': SkillPackageParser().parse(payload, filename='SKILL.md').parsed_hash,
                    'confirmed': UUID(confirmed) if confirmed else None,
                },
            )
    await migrate(container.settings)
    async with container.database.sessions.begin() as session:
        frozen = await container.skills.confirm(session, principal, confirmed_id)
        assert frozen.model_dump(mode='json') == original
        personal = next(
            row for row in await container.skills.available(session, principal.id) if row.slug == original['slug']
        )
        await container.skills.set_status(session, principal, personal.id, status='disabled', expected_version=1)
    async with container.database.sessions.begin() as session:
        assert await container.skills.confirm(session, principal, confirmed_id) == frozen
        with pytest.raises(AppError) as error:
            await container.skills.confirm(session, principal, preview_id)
        assert error.value.code == 'skill_version_conflict'
