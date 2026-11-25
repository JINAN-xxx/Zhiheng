"""The platform catalog contains only explicitly published personal skills."""

import hashlib
from datetime import timedelta
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.core.integration.test_builtin_role_migration import _version
from tests.core.integration.test_skills import install
from tests.core.permission_support import grant_member, grants
from tests.core.unit.test_skill_package import markdown
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.skills.orm import Skill, SkillDraft
from zhiheng.modules.skills.package import SkillPackageParser
from zhiheng.shared.time import utcnow


async def test_market_and_mine_filter_before_pagination_and_keep_ownership(
    admin: httpx.AsyncClient, app: object
) -> None:
    await grant_member(admin, grants('platform:skills'))
    uploaded = await install(admin, 'personal-upload')
    assert (await admin.get('/api/skills', params={'source': 'platform'})).json()['total'] == 0
    mine = (await admin.get('/api/skills', params={'source': 'upload', 'page_size': 2})).json()
    assert mine['total'] == 6 and len(mine['items']) == 2
    for skill in mine['items']:
        response = await admin.put(f'/api/skills/{skill["id"]}/platform', json={'expected_version': skill['version']})
        assert response.status_code == 200, response.text
    market = (await admin.get('/api/skills', params={'source': 'platform', 'page_size': 1})).json()
    assert market['total'] == 2 and len(market['items']) == 1
    assert market['items'][0]['source'] == 'platform'
    mine = (await admin.get('/api/skills', params={'source': 'upload'})).json()
    assert mine['total'] == 6 and uploaded['id'] in {item['id'] for item in mine['items']}
    assert (await admin.get('/api/skills')).json()['total'] == 6
    assert (await admin.get('/api/skills', params={'source': 'other'})).status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get('/api/skills', params={'source': 'upload'})).json()['items'] == []
        assert (await member.get('/api/skills', params={'source': 'builtin'})).json()['total'] == 0
        assert (await member.get('/api/skills', params={'source': 'platform'})).json()['total'] == 2
        assert (await member.get(f'/api/skills/{uploaded["id"]}')).status_code == 404


async def test_admin_can_replace_disable_and_delete_a_seeded_skill(admin: httpx.AsyncClient) -> None:
    seeded = (await admin.get('/api/skills', params={'source': 'upload'})).json()['items'][0]
    prepared = await admin.post(
        '/api/skills/import/prepare?filename=SKILL.md',
        content=markdown(seeded['slug'], 'New content'),
        headers={'content-type': 'application/octet-stream'},
    )
    assert prepared.status_code == 201 and prepared.json()['action'] == 'update'
    draft_id = prepared.json()['draft_id']
    replaced = await admin.post(f'/api/skills/import/{draft_id}/confirm', json={})
    assert replaced.status_code == 201 and replaced.json()['id'] == seeded['id']
    disabled = await admin.patch(
        f'/api/skills/{seeded["id"]}/status',
        json={'status': 'disabled', 'expected_version': replaced.json()['version']},
    )
    assert disabled.status_code == 200
    assert seeded['id'] not in {item['id'] for item in (await admin.get('/api/skills/available')).json()}
    assert (
        await admin.delete(f'/api/skills/{seeded["id"]}', headers={'content-type': 'application/json'})
    ).status_code == 200


async def test_preexisting_unconfirmed_draft_cannot_overwrite_a_changed_skill(
    admin: httpx.AsyncClient, app: object
) -> None:
    seeded = (await admin.get('/api/skills', params={'source': 'upload'})).json()['items'][0]
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    payload = markdown(seeded['slug'], 'A draft prepared before the new policy')
    parsed = SkillPackageParser().parse(payload, filename='SKILL.md')
    async with app.state.container.database.sessions.begin() as session:
        draft = SkillDraft(
            owner_id=owner,
            filename='SKILL.md',
            package_bytes=payload,
            package_sha=hashlib.sha256(payload).hexdigest(),
            parsed_hash=parsed.parsed_hash,
            expires_at=utcnow() + timedelta(hours=1),
            target_skill_id=UUID(seeded['id']),
            target_version=seeded['version'] - 1,
            action='update',
        )
        session.add(draft)
        await session.flush()
        draft_id = draft.id
    response = await admin.post(f'/api/skills/import/{draft_id}/confirm', json={})
    assert response.status_code == 409 and response.json()['error']['code'] == 'skill_version_conflict'
    assert (await admin.get(f'/api/skills/{seeded["id"]}')).json() == seeded


async def test_upgrade_restores_deleted_presets_disabled_without_rewriting_content_or_uploads(
    settings: Settings,
) -> None:
    await _version(settings, '0026_model_output_range')
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            owner = await IdentityService().create_user(
                session, username='before-market', password='platform-test-password'
            )
        await _version(settings, '0029_application_governance')
        async with sessions.begin() as session:
            preset_id, revision_id = (
                await session.execute(
                    text("""UPDATE skills SET deleted_at=now(), version=7,
                    ownership_audit=jsonb_build_object('decision', 'preserve-history')
                    WHERE owner_id=:owner AND slug='meeting-minutes' RETURNING id, revision_id"""),
                    {'owner': owner.id},
                )
            ).one()
            uploaded_id = await session.scalar(
                text("""UPDATE skills SET source='upload', deleted_at=now()
                WHERE owner_id=:owner AND slug='writing-polish' RETURNING id"""),
                {'owner': owner.id},
            )
            before_upload = await session.scalar(
                text('SELECT to_jsonb(s) FROM skills s WHERE id=:id'), {'id': uploaded_id}
            )
            before_revision = await session.scalar(
                text('SELECT to_jsonb(r) FROM skill_revisions r WHERE id=:id'), {'id': revision_id}
            )
        await _version(settings, '0047_platform_skills')
        async with sessions() as session:
            restored = await session.get(Skill, preset_id)
            assert restored is not None
            assert restored.deleted_at is None and restored.status == 'disabled' and restored.version == 8
            assert restored.revision_id == revision_id
            assert restored.ownership_audit['decision'] == 'preserve-history'
            assert restored.ownership_audit['market_restore_0032']['version'] == 7
            assert (
                await session.scalar(
                    text("SELECT to_jsonb(s) - 'platform_revision_id' FROM skills s WHERE id=:id"), {'id': uploaded_id}
                )
                == before_upload
            )
            assert (
                await session.scalar(
                    text('SELECT to_jsonb(r) FROM skill_revisions r WHERE id=:id'), {'id': revision_id}
                )
                == before_revision
            )
    finally:
        await engine.dispose()
