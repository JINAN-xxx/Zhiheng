import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx

from tests.core.billing_support import enable_free_billing
from tests.core.unit.test_skill_package import markdown


async def prepare(admin: httpx.AsyncClient, slug: str = 'sample', body: str = 'Instruction') -> dict:
    response = await admin.post(
        '/api/skills/import/prepare?filename=SKILL.md',
        content=markdown(slug, body),
        headers={'content-type': 'application/octet-stream'},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def install(admin: httpx.AsyncClient, slug: str = 'sample', body: str = 'Instruction') -> dict:
    draft = await prepare(admin, slug, body)
    response = await admin.post(f'/api/skills/import/{draft["draft_id"]}/confirm', json={})
    assert response.status_code == 201, response.text
    return response.json()


async def test_builtins_and_two_phase_owner_cas_and_soft_delete(admin: httpx.AsyncClient, app: object) -> None:
    builtin = (await admin.get('/api/skills/available')).json()
    assert {item['slug'] for item in builtin} == {
        'deep-research',
        'document-summary',
        'meeting-minutes',
        'requirements-clarification',
        'writing-polish',
    }
    draft = await prepare(admin)
    assert (await admin.get('/api/skills')).json()['total'] == len(builtin)
    results = await asyncio.gather(
        *(admin.post(f'/api/skills/import/{draft["draft_id"]}/confirm', json={}) for _ in range(2))
    )
    assert [result.status_code for result in results] == [201, 201]
    skill = results[0].json()
    assert results[1].json()['id'] == skill['id']
    assert (await admin.get(f'/api/skills/{skill["id"]}/content')).json()['content'] == markdown().decode()
    disabled = await admin.patch(
        f'/api/skills/{skill["id"]}/status', json={'status': 'disabled', 'expected_version': 1}
    )
    assert disabled.status_code == 200 and disabled.json()['version'] == 2
    assert (
        await admin.patch(f'/api/skills/{skill["id"]}/status', json={'status': 'enabled', 'expected_version': 1})
    ).status_code == 409
    assert (
        await admin.delete(f'/api/skills/{skill["id"]}', headers={'content-type': 'application/json'})
    ).status_code == 200
    assert (await admin.get(f'/api/skills/{skill["id"]}')).status_code == 404
    assert (
        await admin.delete(f'/api/skills/{builtin[0]["id"]}', headers={'content-type': 'application/json'})
    ).status_code == 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get('/api/skills')).status_code == 200
        assert (
            await member.post(
                '/api/skills/import/prepare?filename=SKILL.md',
                content=markdown(),
                headers={'content-type': 'application/octet-stream'},
            )
        ).status_code == 201
        assert (await member.post(f'/api/skills/import/{draft["draft_id"]}/confirm', json={})).status_code == 404


async def test_slug_conflict_and_unknown_actions(admin: httpx.AsyncClient) -> None:
    drafts = [await prepare(admin) for _ in range(2)]
    results = await asyncio.gather(
        *(admin.post(f'/api/skills/import/{draft["draft_id"]}/confirm', json={}) for draft in drafts)
    )
    assert sorted(result.status_code for result in results) == [201, 409]
    assert (
        await admin.post(f'/api/skills/import/{drafts[0]["draft_id"]}/confirm', json={'extra': 1})
    ).status_code == 422
    assert (await admin.get('/api/skills?page_size=101')).status_code == 422


async def test_chat_selection_freezes_order_content_and_retries_after_disable(
    admin: httpx.AsyncClient, app: object, model_id: str, provider: object
) -> None:
    await enable_free_billing(admin)
    from zhiheng.worker.execution import execute_run

    one = await install(admin, 'one', 'FIRST FULL INSTRUCTION')
    two = await install(admin, 'two', 'SECOND FULL INSTRUCTION')
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    path = f'/api/chat/conversations/{conversation}/runs'
    body = {'model_id': model_id, 'message': 'hello', 'client_request_id': str(uuid4()), 'skill_slugs': ['two', 'one']}
    run = await admin.post(path, json=body)
    assert run.status_code == 202, run.text
    usage = (await admin.get('/api/usage')).json()
    assert (
        usage['reserved_tokens']
        >= len(markdown('one', 'FIRST FULL INSTRUCTION')) + len(markdown('two', 'SECOND FULL INSTRUCTION')) + 64
    )
    await admin.patch(f'/api/skills/{two["id"]}/status', json={'status': 'disabled', 'expected_version': 1})
    retry = await admin.post(path, json=body)
    assert retry.status_code == 202 and retry.json()['id'] == run.json()['id']
    assert (await admin.post(path, json={**body, 'skill_slugs': ['one', 'two']})).status_code == 409
    await execute_run(app.state.container, UUID(run.json()['id']))
    sent = provider.requests[0]['messages']
    assert [message['content'] for message in sent[:2]] == [
        markdown('two', 'SECOND FULL INSTRUCTION').decode(),
        markdown('one', 'FIRST FULL INSTRUCTION').decode(),
    ]
    messages = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    assert [item['slug'] for item in messages[0]['skill_snapshots']] == ['two', 'one']
    assert messages[0]['skill_snapshots'][1]['revision_id'] == one['revision_id']
    for slugs in [['two'], ['missing'], ['one', 'one'], ['one'] * 6]:
        response = await admin.post(path, json={**body, 'client_request_id': str(uuid4()), 'skill_slugs': slugs})
        assert response.status_code == 422
    assert len(provider.requests) == 1


async def test_owner_expiration_digest_and_bounded_cleanup(admin: httpx.AsyncClient, app: object) -> None:
    from datetime import timedelta

    from sqlalchemy import func, select, update

    from zhiheng.modules.identity.public import IdentityService
    from zhiheng.modules.skills.orm import SkillDraft
    from zhiheng.shared.time import utcnow

    drafts = [await prepare(admin, f'draft-{index}') for index in range(4)]
    container = app.state.container
    async with container.database.sessions.begin() as session:
        await IdentityService().create_user(
            session, username='other-admin', password='platform-test-password', is_admin=True
        )
        await session.execute(
            update(SkillDraft)
            .where(SkillDraft.id == UUID(drafts[1]['draft_id']))
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.execute(
            update(SkillDraft).where(SkillDraft.id == UUID(drafts[2]['draft_id'])).values(package_bytes=b'changed')
        )
        await session.execute(
            update(SkillDraft).where(SkillDraft.id == UUID(drafts[3]['draft_id'])).values(parsed_hash='0' * 64)
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as other:
        await other.post('/api/auth/login', json={'username': 'other-admin', 'password': 'platform-test-password'})
        assert (await other.post(f'/api/skills/import/{drafts[0]["draft_id"]}/confirm', json={})).status_code == 404
    for index, code in [(1, 'skill_draft_expired'), (2, 'skill_draft_changed'), (3, 'skill_draft_changed')]:
        response = await admin.post(f'/api/skills/import/{drafts[index]["draft_id"]}/confirm', json={})
        assert response.status_code == 409 and response.json()['error']['code'] == code
    confirmed = await admin.post(f'/api/skills/import/{drafts[0]["draft_id"]}/confirm', json={})
    async with container.database.sessions.begin() as session:
        row = await session.get(SkillDraft, UUID(drafts[0]['draft_id']))
        row.expires_at = utcnow() - timedelta(seconds=1)
        for _ in range(102):
            session.add(
                SkillDraft(
                    owner_id=row.owner_id,
                    expires_at=utcnow() - timedelta(seconds=1),
                    filename='SKILL.md',
                    package_bytes=b'x',
                    package_sha='0' * 64,
                    parsed_hash='0' * 64,
                )
            )
    async with container.database.sessions.begin() as session:
        assert await container.skills.cleanup_expired(session) == 100
    async with container.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SkillDraft)
                .where(SkillDraft.confirmed_skill_id.is_(None), SkillDraft.expires_at <= utcnow())
            )
            == 3
        )
    assert (await admin.post(f'/api/skills/import/{drafts[0]["draft_id"]}/confirm', json={})).json()[
        'id'
    ] == confirmed.json()['id']


async def test_selection_byte_limit_and_builtin_disable(admin: httpx.AsyncClient, app: object) -> None:
    import pytest

    from zhiheng.modules.skills.public import resolve_selection
    from zhiheng.shared.errors import AppError

    await install(admin, 'large', '测' * 11000)
    async with app.state.container.database.sessions.begin() as session:
        with pytest.raises(AppError) as error:
            await resolve_selection(session, UUID((await admin.get('/api/auth/me')).json()['id']), ['large'])
        assert error.value.code == 'skill_context_too_long'
        with pytest.raises(AppError):
            await resolve_selection(session, UUID((await admin.get('/api/auth/me')).json()['id']), ['missing'] * 6)
    builtin = (await admin.get('/api/skills/available')).json()[0]
    assert (
        await admin.patch(f'/api/skills/{builtin["id"]}/status', json={'status': 'disabled', 'expected_version': 1})
    ).status_code == 200
    async with app.state.container.database.sessions.begin() as session:
        with pytest.raises(AppError) as error:
            await resolve_selection(session, UUID((await admin.get('/api/auth/me')).json()['id']), [builtin['slug']])
        assert error.value.code == 'skill_unavailable'


async def test_raw_transport_origin_type_size_and_length(admin: httpx.AsyncClient) -> None:
    path = '/api/skills/import/prepare?filename=SKILL.md'
    assert (
        await admin.post(
            path,
            content=markdown(),
            headers={'content-type': 'application/octet-stream', 'origin': 'https://evil.invalid'},
        )
    ).status_code == 403
    assert (await admin.post(path, json={})).status_code == 415
    assert (
        await admin.post(
            '/api/chat/conversations', content=markdown(), headers={'content-type': 'application/octet-stream'}
        )
    ).status_code == 415
    assert (
        await admin.post(
            path, content=markdown(), headers={'content-type': 'application/octet-stream', 'content-length': 'wrong'}
        )
    ).status_code == 400
    assert (
        await admin.post(
            path, content=markdown(), headers={'content-type': 'application/octet-stream', 'content-length': '1'}
        )
    ).status_code == 400
    assert (
        await admin.post(
            path,
            content=b'x',
            headers={'content-type': 'application/octet-stream', 'content-length': str(10 * 1024 * 1024 + 1)},
        )
    ).status_code == 413

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(11):
            yield b'x' * (1024 * 1024)

    response = await admin.post(path, content=chunks(), headers={'content-type': 'application/octet-stream'})
    assert response.status_code == 413


async def test_revision_package_bytes_are_retained_and_immutable(admin: httpx.AsyncClient, app: object) -> None:
    import pytest
    from sqlalchemy import select, text
    from sqlalchemy.exc import DBAPIError

    from tests.core.unit.test_skill_package import archive
    from zhiheng.modules.skills.orm import SkillRevision

    payload = archive([('SKILL.md', markdown()), ('supporting.txt', b'attachment is preserved')])
    result = await admin.post(
        '/api/skills/import/prepare?filename=package.zip',
        content=payload,
        headers={'content-type': 'application/octet-stream'},
    )
    draft = result.json()
    skill = (await admin.post(f'/api/skills/import/{draft["draft_id"]}/confirm', json={})).json()
    async with app.state.container.database.sessions() as session:
        row = await session.scalar(select(SkillRevision).where(SkillRevision.id == UUID(skill['revision_id'])))
        assert row.package_bytes == payload and row.file_count == 2
    with pytest.raises(DBAPIError):
        async with app.state.container.database.sessions.begin() as session:
            await session.execute(
                text('UPDATE skill_revisions SET content=:content WHERE id=:id'),
                {'content': 'mutated', 'id': UUID(skill['revision_id'])},
            )


async def test_upload_length_header_cannot_trigger_integer_conversion_failure(admin: httpx.AsyncClient) -> None:
    path = '/api/skills/import/prepare?filename=SKILL.md'
    for length in ['9' * 5000, '²']:
        response = await admin.post(
            path,
            content=markdown(),
            headers=[(b'content-type', b'application/octet-stream'), (b'content-length', length.encode('latin1'))],
        )
        assert response.status_code in {400, 413}


async def test_upload_filename_nul_is_rejected_before_postgresql_storage(admin: httpx.AsyncClient) -> None:
    from tests.core.unit.test_skill_package import archive

    response = await admin.post(
        '/api/skills/import/prepare?filename=skill%00.zip',
        content=archive([('SKILL.md', markdown())]),
        headers={'content-type': 'application/octet-stream'},
    )
    assert response.status_code == 422 and response.json()['error']['code'] == 'invalid_skill_package'


async def test_corrupt_deflate_prepare_returns_422_without_draft(admin: httpx.AsyncClient, app: object) -> None:
    from sqlalchemy import func, select

    from tests.core.unit.test_skill_package import corrupt_deflate
    from zhiheng.modules.skills.orm import SkillDraft

    response = await admin.post(
        '/api/skills/import/prepare?filename=broken.zip',
        content=corrupt_deflate(),
        headers={'content-type': 'application/octet-stream'},
    )
    assert response.status_code == 422 and response.json()['error']['code'] == 'invalid_skill_package'
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SkillDraft)) == 0


async def test_other_corrupt_codecs_and_directory_payload_leave_no_draft(admin: httpx.AsyncClient, app: object) -> None:
    import io
    import struct
    import zipfile

    from sqlalchemy import func, select

    from tests.core.unit.test_skill_package import archive
    from zhiheng.modules.skills.orm import SkillDraft

    payloads = [archive([('SKILL.md', markdown()), ('directory/', b'x' * (31 * 1024 * 1024))])]
    for compression, delta in [(zipfile.ZIP_BZIP2, 0), (zipfile.ZIP_LZMA, 4)]:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=compression) as zipped:
            zipped.writestr('SKILL.md', markdown())
        payload = bytearray(stream.getvalue())
        name_length, extra_length = struct.unpack_from('<HH', payload, 26)
        payload[30 + name_length + extra_length + delta] = 0xFF
        payloads.append(bytes(payload))
    for payload in payloads:
        response = await admin.post(
            '/api/skills/import/prepare?filename=broken.zip',
            content=payload,
            headers={'content-type': 'application/octet-stream'},
        )
        assert response.status_code == 422 and response.json()['error']['code'] == 'invalid_skill_package'
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SkillDraft)) == 0


async def test_invalid_skill_input_is_422_without_runs_messages_ledger_or_provider(
    admin: httpx.AsyncClient, app: object, model_id: str, provider: object
) -> None:
    import json

    from sqlalchemy import func, select

    from zhiheng.modules.billing.orm import Invocation
    from zhiheng.modules.chat.orm import ChatMessage
    from zhiheng.runtime.orm import Run

    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url='http://test', cookies=admin.cookies) as client:
        for slug in ['nul\x00slug', '\ud800', 'a' * 256, 'Bad', '中文', 'under_score']:
            body = json.dumps(
                {'model_id': model_id, 'message': 'hello', 'client_request_id': str(uuid4()), 'skill_slugs': [slug]}
            )
            response = await client.post(
                f'/api/chat/conversations/{conversation}/runs',
                content=body,
                headers={'content-type': 'application/json'},
            )
            assert response.status_code == 422, (repr(slug), response.text)
        for query in ['nul%00query', '%ED%A0%80', '%FF']:
            response = await client.get(f'/api/skills?search={query}')
            assert response.status_code == 422, (query, response.text)
        response = await client.get('/api/skills', params={'search': '深度研究'})
        assert response.status_code == 200 and [item['slug'] for item in response.json()['items']] == ['deep-research']
    async with app.state.container.database.sessions() as session:
        for row in [Run, ChatMessage, Invocation]:
            assert await session.scalar(select(func.count()).select_from(row)) == 0
    assert not provider.requests


async def test_public_selection_and_search_reject_invalid_text_before_query(
    app: object, admin: httpx.AsyncClient
) -> None:
    import pytest

    from zhiheng.modules.skills.public import resolve_selection
    from zhiheng.shared.errors import AppError

    async with app.state.container.database.sessions.begin() as session:
        for slug in ['nul\x00slug', '\ud800', 'a' * 256, 'Bad', '中文']:
            with pytest.raises(AppError) as error:
                await resolve_selection(session, UUID((await admin.get('/api/auth/me')).json()['id']), [slug])
            assert error.value.code == 'invalid_skill_selection'
        for search in ['nul\x00query', '\ud800']:
            with pytest.raises(AppError) as error:
                await app.state.container.skills.list_skills(
                    session, UUID((await admin.get('/api/auth/me')).json()['id']), search=search
                )
            assert error.value.code == 'invalid_skill_search'


async def test_long_valid_slugs_remain_installable_and_selectable(
    admin: httpx.AsyncClient, app: object, model_id: str
) -> None:
    from zhiheng.modules.skills.public import resolve_selection

    for length in [101, 255]:
        slug = 'a' * length
        skill = await install(admin, slug)
        async with app.state.container.database.sessions.begin() as session:
            selection = await resolve_selection(session, UUID((await admin.get('/api/auth/me')).json()['id']), [slug])
            assert (
                selection.revisions[0].slug == slug and str(selection.revisions[0].revision_id) == skill['revision_id']
            )
        conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
        response = await admin.post(
            f'/api/chat/conversations/{conversation}/runs',
            json={'model_id': model_id, 'message': 'hello', 'client_request_id': str(uuid4()), 'skill_slugs': [slug]},
        )
        assert response.status_code == 202, response.text
