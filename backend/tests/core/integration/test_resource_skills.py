"""Personal skill resources preserve native packages, receipts and current authority."""

import asyncio
import hashlib
import io
import zipfile
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.core.integration.test_resource_files import auth, envelope, post
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.integration.test_skills import install
from tests.core.unit.test_skill_package import markdown
from zhiheng.modules.skills.orm import SkillDraft, SkillRevision

SCOPES = ['files:read', 'files:write', 'skills:read', 'skills:write']


async def setup(admin: httpx.AsyncClient) -> tuple[dict, dict]:
    owner_app = await application(admin)
    return owner_app, await issue(admin, owner_app['id'], scopes=SCOPES)


async def preview(client: httpx.AsyncClient, key: dict, content: bytes | None = None) -> httpx.Response:
    file = await post(
        client, key, files={'file': ('SKILL.md', content or markdown(), 'text/markdown')}, data={'purpose': 'skill'}
    )
    assert file.status_code == 201, file.text
    return await client.post('/api/v1/skill-imports', headers=auth(key, uuid4()), json={'file_id': file.json()['id']})


async def test_skill_resource_native_roundtrip_and_cas(admin: httpx.AsyncClient, app: FastAPI) -> None:
    owner_app, key = await setup(admin)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        draft = await preview(client, key)
        assert draft.status_code == 201, draft.text
        assert draft.json()['action'] == 'install'
        endpoint = '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm'
        external = uuid4()
        responses = await asyncio.gather(*(client.post(endpoint, headers=auth(key, external)) for _ in range(3)))
        assert {r.status_code for r in responses} == {201}, [r.text for r in responses]
        skill = responses[0].json()
        assert all(r.json() == skill for r in responses)
        assert skill['owner_id'] == owner_app['owner_id'] and skill['resource_version'] == 1
        assert skill['current_revision_id'] and skill['package_version']
        url = '/api/v1/skills/' + skill['id']
        detail = await client.get(url, headers=auth(key))
        assert detail.json() == skill and detail.headers['etag'] == '"skill-1"'
        assert (await client.get('/api/v1/skills', headers=auth(key))).json()['items'] == [skill]
        revision = skill['current_revision_id']
        versions = await client.get(url + '/versions', headers=auth(key))
        assert [v['id'] for v in versions.json()['items']] == [revision]
        content = await client.get(url + '/versions/' + revision + '/content', headers=auth(key))
        assert content.status_code == 200 and content.content == markdown(), content.text
        assert content.headers['x-content-type-options'] == 'nosniff'
        assert 'sandbox' in content.headers['content-security-policy']
        assert versions.json()['items'][0]['package_sha'] == hashlib.sha256(markdown()).hexdigest()
        envelope(await client.patch(url, headers=auth(key), json={'status': 'disabled'}), 428)
        envelope(
            await client.patch(url, headers={**auth(key), 'If-Match': '"skill-9"'}, json={'status': 'disabled'}), 412
        )
        changed = await client.patch(
            url, headers={**auth(key), 'If-Match': detail.headers['etag']}, json={'status': 'disabled'}
        )
        assert changed.status_code == 200 and changed.json()['resource_version'] == 2, changed.text
        envelope(
            await client.patch(
                url, headers={**auth(key), 'If-Match': changed.headers['etag']}, json={'owner_id': str(uuid4())}
            ),
            422,
        )
        removed = await client.delete(url, headers={**auth(key), 'If-Match': changed.headers['etag']})
        assert removed.status_code == 200 and removed.json()['status'] == 'deleted', removed.text
        restored_draft = await preview(client, key)
        assert restored_draft.status_code == 201 and restored_draft.json()['action'] == 'restore', restored_draft.text
        restored = await client.post(
            '/api/v1/skill-imports/' + restored_draft.json()['draft_id'] + '/confirm', headers=auth(key, uuid4())
        )
        assert restored.status_code == 201 and restored.json()['id'] == skill['id'], restored.text
        assert (await client.post(endpoint, headers=auth(key, external))).json() == skill
    async with app.state.container.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(SkillRevision).where(SkillRevision.skill_id == UUID(skill['id']))
            )
            == 2
        )


async def test_skill_import_target_and_draft_application_isolation(admin: httpx.AsyncClient, app: FastAPI) -> None:
    _owner_app, key = await setup(admin)
    existing = await install(admin)
    envelope(await preview(admin, key), 404)
    c = app.state.container
    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SkillDraft)) == 1
    draft = await preview(admin, key, markdown('different'))
    assert draft.status_code == 201, draft.text
    other_app = await application(admin)
    other = await issue(admin, other_app['id'], scopes=SCOPES)
    envelope(
        await admin.post(
            '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm', headers=auth(other, uuid4())
        ),
        404,
    )
    envelope(await admin.get('/api/v1/skills/' + existing['id'], headers=auth(key)), 404)
    envelope(await admin.get('/api/v1/skills'), 401)


async def test_skill_source_revocation_and_cross_key_receipt(admin: httpx.AsyncClient, app: FastAPI) -> None:
    owner_app, key = await setup(admin)
    file = await post(admin, key, files={'file': ('SKILL.md', markdown(), 'text/markdown')}, data={'purpose': 'skill'})
    external = uuid4()
    draft = await admin.post('/api/v1/skill-imports', headers=auth(key, external), json={'file_id': file.json()['id']})
    assert draft.status_code == 201, draft.text
    second = await issue(admin, owner_app['id'], scopes=SCOPES)
    endpoint = '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm'
    installed = await admin.post(endpoint, headers=auth(second, uuid4()))
    assert installed.status_code == 201, installed.text
    c = app.state.container
    async with c.database.sessions.begin() as session:
        caller = await c.resource_access.bearer(session, key['api_key'], scope='skills:write')
        owned = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
        await c.resource_access.unbind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'skill',
            UUID(installed.json()['id']),
            expected_version=owned.version,
        )
    envelope(await admin.post(endpoint, headers=auth(second, uuid4())), 404)
    async with c.database.sessions.begin() as session:
        owned = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
        await c.resource_access.bind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'skill',
            UUID(installed.json()['id']),
            ['read', 'write', 'delete'],
            expected_version=owned.version,
        )
    async with c.database.sessions.begin() as session:
        caller = await c.resource_access.bearer(session, key['api_key'], scope='files:read')
        owned = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
        await c.resource_access.unbind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'file',
            UUID(file.json()['id']),
            expected_version=owned.version,
        )
    envelope(await admin.post(endpoint, headers=auth(second, uuid4())), 404)
    envelope(
        await admin.post('/api/v1/skill-imports', headers=auth(key, external), json={'file_id': file.json()['id']}), 404
    )


async def test_skill_zip_revisions_keep_native_bytes_and_chronological_cursor(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner_app, key = await setup(admin)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('SKILL.md', markdown())
        archive.writestr('asset.txt', b'original asset')
    content = stream.getvalue()
    file = await post(
        admin, key, files={'file': ('original.zip', content, 'application/zip')}, data={'purpose': 'skill'}
    )
    draft = await admin.post('/api/v1/skill-imports', headers=auth(key, uuid4()), json={'file_id': file.json()['id']})
    assert draft.status_code == 201, draft.text
    installed = await admin.post(
        '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm', headers=auth(key, uuid4())
    )
    assert installed.status_code == 201, installed.text
    skill = installed.json()
    endpoint = '/api/v1/skills/' + skill['id']
    draft = await preview(admin, key, markdown(body='New instruction'))
    assert draft.status_code == 201 and draft.json()['action'] == 'update', draft.text
    monkeypatch.setattr('zhiheng.modules.skills.service.uuid4', lambda: UUID(int=1))
    updated = await admin.post(
        '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm', headers=auth(key, uuid4())
    )
    assert updated.status_code == 201 and updated.json()['resource_version'] == 2, updated.text
    first = await admin.get(endpoint + '/versions?limit=1', headers=auth(key))
    assert first.json()['items'][0]['id'] == str(UUID(int=1)) and first.json()['has_more'], first.text
    second = await admin.get(endpoint + '/versions', headers=auth(key), params={'cursor': first.json()['next_cursor']})
    assert second.json()['items'][0]['id'] == skill['current_revision_id'] and not second.json()['has_more']
    assert first.json()['items'][0]['created_at'] > second.json()['items'][0]['created_at']
    original = await admin.get(endpoint + '/versions/' + skill['current_revision_id'] + '/content', headers=auth(key))
    assert original.content == content
    assert 'original.zip' in original.headers['content-disposition']
    assert original.headers['etag'] == '"' + hashlib.sha256(content).hexdigest() + '"'


async def test_skill_confirmation_parses_package_outside_app_and_source_row_locks(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    from zhiheng.modules.developer.orm import DeveloperApplication
    from zhiheng.modules.files.orm import FileRecord
    from zhiheng.modules.skills.package import ParsedSkillPackage
    from zhiheng.modules.skills.public import SkillPackageParser

    owner_app, key = await setup(admin)
    file = await post(admin, key, files={'file': ('SKILL.md', markdown(), 'text/markdown')}, data={'purpose': 'skill'})
    draft = await admin.post('/api/v1/skill-imports', headers=auth(key, uuid4()), json={'file_id': file.json()['id']})
    assert draft.status_code == 201, draft.text
    c = app.state.container
    probes: list[bool] = []
    parser = SkillPackageParser.parse

    async def probe() -> bool:
        engine = create_async_engine(c.database.engine.url)
        try:
            async with engine.begin() as connection:
                try:
                    await connection.execute(
                        select(DeveloperApplication.id)
                        .where(DeveloperApplication.id == UUID(owner_app['id']))
                        .with_for_update(nowait=True)
                    )
                    await connection.execute(
                        select(FileRecord.id)
                        .where(FileRecord.id == UUID(file.json()['id']))
                        .with_for_update(nowait=True)
                    )
                except DBAPIError as error:
                    assert error.orig.sqlstate == '55P03'
                    return False
            return True
        finally:
            await engine.dispose()

    def parse(instance: SkillPackageParser, payload: bytes, *, filename: str) -> ParsedSkillPackage:
        thread = threading.Thread(target=lambda: probes.append(asyncio.run(probe())))
        thread.start()
        thread.join(3)
        assert not thread.is_alive()
        return parser(instance, payload, filename=filename)

    monkeypatch.setattr(SkillPackageParser, 'parse', parse)
    confirmed = await admin.post(
        '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm', headers=auth(key, uuid4())
    )
    assert confirmed.status_code == 201, confirmed.text
    assert probes == [True], 'Package parsing held an App or source File row lock'


async def test_skill_metadata_queries_never_fetch_original_package_binary(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    from sqlalchemy import event

    _owner_app, key = await setup(admin)
    draft = await preview(admin, key)
    assert draft.status_code == 201, draft.text
    installed = await admin.post(
        '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm', headers=auth(key, uuid4())
    )
    assert installed.status_code == 201, installed.text
    skill = installed.json()
    base = '/api/v1/skills/' + skill['id']
    statements: list[str] = []

    def capture(
        _connection: object, _cursor: object, statement: str, _parameters: object, _context: object, _many: bool
    ) -> None:
        if statement.lstrip().upper().startswith('SELECT') and 'skill_revisions' in statement:
            statements.append(statement)

    engine = app.state.container.database.engine.sync_engine
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        for endpoint in (
            '/api/v1/skills',
            base,
            base + '/versions?limit=1',
            base + '/versions/' + skill['current_revision_id'],
            '/api/skills',
            '/api/skills/available',
            '/api/skills/' + skill['id'],
        ):
            statements.clear()
            response = await admin.get(endpoint, headers={} if endpoint.startswith('/api/skills') else auth(key))
            assert response.status_code == 200, response.text
            assert statements
            assert all('package_bytes' not in statement for statement in statements), (endpoint, statements)
            if '/versions?' in endpoint:
                assert any('LIMIT' in statement for statement in statements)
        statements.clear()
        downloaded = await admin.get(base + '/versions/' + skill['current_revision_id'] + '/content', headers=auth(key))
        assert downloaded.status_code == 200 and downloaded.content == markdown()
        assert any('package_bytes' in statement for statement in statements)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)


@pytest.mark.parametrize('phase', ['prepare', 'confirm'])
async def test_resource_skill_waits_for_native_slug_without_locking_target(
    admin: httpx.AsyncClient, app: FastAPI, phase: str
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from zhiheng.modules.files.orm import FileRecord
    from zhiheng.modules.skills.orm import Skill

    owner_app, key = await setup(admin)
    draft = await preview(admin, key)
    installed = await admin.post(
        '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm', headers=auth(key, uuid4())
    )
    assert installed.status_code == 201, installed.text
    file = await post(
        admin, key, files={'file': ('SKILL.md', markdown(body='Changed'), 'text/markdown')}, data={'purpose': 'skill'}
    )
    payload = {'file_id': file.json()['id']}
    if phase == 'confirm':
        draft = await admin.post('/api/v1/skill-imports', headers=auth(key, uuid4()), json=payload)
        assert draft.status_code == 201, draft.text
    c = app.state.container
    unlocked = False
    async with c.database.sessions.begin() as holder:
        await c.skills._lock_slug(holder, UUID(owner_app['owner_id']), 'sample')
        request = asyncio.create_task(
            admin.post('/api/v1/skill-imports', headers=auth(key, uuid4()), json=payload)
            if phase == 'prepare'
            else admin.post(
                '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm', headers=auth(key, uuid4())
            )
        )
        try:
            for _ in range(100):
                async with c.database.sessions() as observer:
                    waiting = await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON l.pid=a.pid WHERE l.locktype='advisory' AND NOT l.granted AND a.datname=current_database())"
                        )
                    )
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting
            async with c.database.sessions.begin() as checker:
                try:
                    await checker.execute(
                        select(Skill).where(Skill.id == UUID(installed.json()['id'])).with_for_update(nowait=True)
                    )
                    await checker.execute(
                        select(FileRecord).where(FileRecord.id == UUID(file.json()['id'])).with_for_update(nowait=True)
                    )
                    unlocked = True
                except DBAPIError as error:
                    assert error.orig.sqlstate == '55P03'
        finally:
            pass
    result = await asyncio.wait_for(request, 5)
    assert result.status_code == 201, result.text
    assert unlocked, 'API held Skill while waiting for native slug advisory lock'


async def test_native_slug_fence_rechecks_target_created_after_resource_preflight(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner_app, key = await setup(admin)
    c = app.state.container
    original = c.skills.target
    appeared = None

    async def target(session: AsyncSession, owner_id: UUID, slug: str) -> UUID | None:
        nonlocal appeared
        assert await original(session, owner_id, slug) is None
        appeared = await install(admin, slug, 'Unbound Console target')
        return None

    monkeypatch.setattr(c.skills, 'target', target)
    response = await preview(admin, key, markdown('race'))
    envelope(response, 404)
    assert appeared is not None
    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SkillDraft)) == 1


async def test_only_native_creation_in_this_confirmation_may_autobind(admin: httpx.AsyncClient, app: FastAPI) -> None:
    owner_app, key = await setup(admin)
    draft = await preview(admin, key)
    assert draft.status_code == 201, draft.text
    native = await admin.post('/api/skills/import/' + draft.json()['draft_id'] + '/confirm', json={})
    assert native.status_code == 201, native.text
    endpoint = '/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm'
    envelope(await admin.post(endpoint, headers=auth(key, uuid4())), 404)
    envelope(await admin.get('/api/v1/skills/' + native.json()['id'], headers=auth(key)), 404)
    c = app.state.container
    async with c.database.sessions.begin() as session:
        caller = await c.resource_access.bearer(session, key['api_key'], scope='skills:write')
        owned = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
        await c.resource_access.bind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'skill',
            UUID(native.json()['id']),
            ['read', 'write', 'delete'],
            expected_version=owned.version,
        )
    confirmed = await admin.post(endpoint, headers=auth(key, uuid4()))
    assert confirmed.status_code == 201 and confirmed.json()['id'] == native.json()['id'], confirmed.text
    assert confirmed.json()['current_revision_id'] == native.json()['revision_id']
    assert (
        await admin.post('/api/skills/import/' + draft.json()['draft_id'] + '/confirm', json={})
    ).json() == native.json()
    async with c.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SkillRevision)
                .where(SkillRevision.skill_id == UUID(native.json()['id']))
            )
            == 1
        )


async def test_skill_confirmation_requires_source_to_remain_readable(admin: httpx.AsyncClient, app: FastAPI) -> None:
    _owner_app, key = await setup(admin)
    file = await post(admin, key, files={'file': ('SKILL.md', markdown(), 'text/markdown')}, data={'purpose': 'skill'})
    draft = await admin.post('/api/v1/skill-imports', headers=auth(key, uuid4()), json={'file_id': file.json()['id']})
    assert draft.status_code == 201, draft.text
    removed = await admin.delete(
        '/api/v1/files/' + file.json()['id'], headers={**auth(key), 'If-Match': file.headers['etag']}
    )
    assert removed.status_code == 200, removed.text
    envelope(
        await admin.post('/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm', headers=auth(key, uuid4())),
        404,
    )
    async with app.state.container.database.sessions() as session:
        row = await session.get(SkillDraft, UUID(draft.json()['draft_id']))
        assert row.confirmed_info is None


async def test_skill_preview_and_confirm_accept_omitted_keys(admin: httpx.AsyncClient, app: FastAPI) -> None:
    _owner_app, key = await setup(admin)
    file = await post(admin, key, files={'file': ('SKILL.md', markdown(), 'text/markdown')}, data={'purpose': 'skill'})
    draft = await admin.post('/api/v1/skill-imports', headers=auth(key), json={'file_id': file.json()['id']})
    assert draft.status_code == 201, draft.text
    installed = await admin.post('/api/v1/skill-imports/' + draft.json()['draft_id'] + '/confirm', headers=auth(key))
    assert installed.status_code == 201, installed.text
    assert (await admin.get('/api/v1/skills/' + installed.json()['id'], headers=auth(key))).status_code == 200
