"""Market presets stay usable, protected and separate from personal uploads."""

import hashlib
from datetime import timedelta
from uuid import UUID

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.core.integration.test_builtin_role_migration import _version
from tests.core.integration.test_skills import install
from tests.core.unit.test_skill_package import markdown
from zhiheng.application.identity.accounts import AccountService
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.skills.orm import Skill, SkillDraft
from zhiheng.modules.skills.package import SkillPackageParser
from zhiheng.modules.skills.public import SkillService
from zhiheng.shared.time import utcnow


async def test_market_and_mine_filter_before_pagination_and_keep_ownership(
    admin: httpx.AsyncClient, app: object
) -> None:
    uploaded = await install(admin, 'personal-upload')
    market = (await admin.get('/api/skills', params={'source': 'builtin', 'page_size': 2})).json()
    assert market['total'] == 5 and len(market['items']) == 2
    assert all(item['source'] == 'builtin' for item in market['items'])
    mine = (await admin.get('/api/skills', params={'source': 'upload'})).json()
    assert mine['total'] == 1 and mine['items'][0]['id'] == uploaded['id']
    assert (await admin.get('/api/skills')).json()['total'] == 6
    assert (await admin.get('/api/skills', params={'source': 'other'})).status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get('/api/skills', params={'source': 'upload'})).json()['items'] == []
        assert (await member.get('/api/skills', params={'source': 'builtin'})).json()['total'] == 5
        assert (await member.get(f'/api/skills/{uploaded["id"]}')).status_code == 404


async def test_presets_cannot_be_deleted_or_replaced_but_can_be_disabled(admin: httpx.AsyncClient, app: object) -> None:
    builtin = (await admin.get('/api/skills', params={'source': 'builtin'})).json()['items'][0]
    deleted = await admin.delete(f'/api/skills/{builtin["id"]}', headers={'content-type': 'application/json'})
    assert deleted.status_code == 403 and deleted.json()['error']['code'] == 'builtin_skill_protected'
    rejected = await admin.post(
        '/api/skills/import/prepare?filename=SKILL.md',
        content=markdown(builtin['slug'], 'Attempted replacement'),
        headers={'content-type': 'application/octet-stream'},
    )
    assert rejected.status_code == 403
    assert (await admin.get(f'/api/skills/{builtin["id"]}')).json() == builtin
    disabled = await admin.patch(
        f'/api/skills/{builtin["id"]}/status', json={'status': 'disabled', 'expected_version': builtin['version']}
    )
    assert disabled.status_code == 200
    assert builtin['id'] not in {item['id'] for item in (await admin.get('/api/skills/available')).json()}
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SkillDraft)) == 0
    uploaded = await install(admin, 'removable-upload')
    assert (
        await admin.delete(f'/api/skills/{uploaded["id"]}', headers={'content-type': 'application/json'})
    ).status_code == 200


async def test_preexisting_unconfirmed_draft_cannot_replace_a_market_preset(
    admin: httpx.AsyncClient, app: object
) -> None:
    builtin = (await admin.get('/api/skills', params={'source': 'builtin'})).json()['items'][0]
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    payload = markdown(builtin['slug'], 'A draft prepared before the new policy')
    parsed = SkillPackageParser().parse(payload, filename='SKILL.md')
    async with app.state.container.database.sessions.begin() as session:
        draft = SkillDraft(
            owner_id=owner,
            filename='SKILL.md',
            package_bytes=payload,
            package_sha=hashlib.sha256(payload).hexdigest(),
            parsed_hash=parsed.parsed_hash,
            expires_at=utcnow() + timedelta(hours=1),
            target_skill_id=UUID(builtin['id']),
            target_version=builtin['version'],
            action='update',
        )
        session.add(draft)
        await session.flush()
        draft_id = draft.id
    response = await admin.post(f'/api/skills/import/{draft_id}/confirm', json={})
    assert response.status_code == 403 and response.json()['error']['code'] == 'builtin_skill_protected'
    assert (await admin.get(f'/api/skills/{builtin["id"]}')).json() == builtin


async def test_upgrade_restores_deleted_presets_disabled_without_rewriting_content_or_uploads(
    settings: Settings,
) -> None:
    await _version(settings, '0029_application_governance')
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = SkillService()
    try:
        async with sessions.begin() as session:
            owner = await AccountService(IdentityService(), service).create_user(
                session, username='before-market', password='platform-test-password'
            )
            row = await session.scalar(select(Skill).where(Skill.owner_id == owner.id, Skill.source == 'builtin'))
            assert row is not None
            row.deleted_at = utcnow()
            row.version = 7
            row.ownership_audit = {'decision': 'preserve-history'}
            preset_id, revision_id = row.id, row.revision_id
            draft = await service.prepare(session, owner, markdown('user-package'), filename='SKILL.md')
            uploaded = await service.confirm(session, owner, draft.draft_id)
            upload = await session.get(Skill, uploaded.id)
            assert upload is not None
            upload.deleted_at = utcnow()
            await session.flush()
            before_upload = await session.scalar(
                text('SELECT to_jsonb(s) FROM skills s WHERE id=:id'), {'id': uploaded.id}
            )
            before_revision = await session.scalar(
                text('SELECT to_jsonb(r) FROM skill_revisions r WHERE id=:id'), {'id': revision_id}
            )
        await migrate(settings)
        async with sessions() as session:
            restored = await session.get(Skill, preset_id)
            assert restored is not None
            assert restored.deleted_at is None and restored.status == 'disabled' and restored.version == 8
            assert restored.revision_id == revision_id
            assert restored.ownership_audit['decision'] == 'preserve-history'
            assert restored.ownership_audit['market_restore_0032']['version'] == 7
            assert (
                await session.scalar(text('SELECT to_jsonb(s) FROM skills s WHERE id=:id'), {'id': uploaded.id})
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
