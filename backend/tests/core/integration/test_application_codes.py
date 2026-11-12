import asyncio
import re
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI


async def test_application_codes_are_generated_uniquely_for_same_names(admin: httpx.AsyncClient) -> None:
    results = await asyncio.gather(
        *(admin.post('/api/developer/applications', json={'name': '同名应用'}) for _ in range(12))
    )
    assert all(result.status_code == 201 for result in results), [result.text for result in results]
    applications = [result.json() for result in results]
    assert len({application['id'] for application in applications}) == 12
    assert len({application['code'] for application in applications}) == 12
    assert all(re.fullmatch(r'app_[0-9a-f]{32}', application['code']) for application in applications)


async def test_generated_application_code_survives_rename_and_retains_key_binding(admin: httpx.AsyncClient) -> None:
    response = await admin.post('/api/developer/applications', json={'name': '自动编码应用'})
    assert response.status_code == 201, response.text
    application = response.json()
    renamed = await admin.patch(
        f'/api/developer/applications/{application["id"]}', json={'name': '改名后的应用', 'expected_version': 1}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()['code'] == application['code']
    key = await admin.post(
        f'/api/developer/applications/{application["id"]}/keys',
        json={'name': '资源密钥', 'scopes': ['files:read'], 'model_ids': []},
    )
    assert key.status_code == 201, key.text
    assert key.json()['application_id'] == application['id']
    forged = await admin.patch(
        f'/api/developer/applications/{application["id"]}',
        json={'name': '无效修改', 'expected_version': 2, 'code': 'other'},
    )
    assert forged.status_code == 422
    assert (await admin.get(f'/api/developer/applications/{application["id"]}')).json()['code'] == application['code']


async def test_generated_application_code_collision_retries_inside_transaction(
    admin: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zhiheng.modules.developer import applications as domain

    collision, replacement = uuid4(), uuid4()
    legacy = await admin.post('/api/developer/applications', json={'code': f'app_{collision.hex}', 'name': '已有应用'})
    assert legacy.status_code == 201, legacy.text
    identifiers = iter([collision, replacement])
    monkeypatch.setattr(domain, 'uuid4', lambda: next(identifiers))
    generated = await admin.post('/api/developer/applications', json={'name': '新应用'})
    assert generated.status_code == 201, generated.text
    assert UUID(generated.json()['id']) == replacement
    assert generated.json()['code'] == f'app_{replacement.hex}'
    retained = await admin.get(f'/api/developer/applications/{legacy.json()["id"]}')
    assert retained.json()['code'] == legacy.json()['code']


async def test_name_only_application_creation_preserves_real_owner_and_isolation(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    original = (await admin.post('/api/developer/applications', json={'code': '123', 'name': '原应用'})).json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        owner = (await member.get('/api/auth/me')).json()['id']
        generated = await member.post('/api/developer/applications', json={'name': '成员应用'})
        assert generated.status_code == 201, generated.text
        assert generated.json()['owner_id'] == owner
        assert (await member.get(f'/api/developer/applications/{original["id"]}')).status_code == 404
        forged = await member.post(
            '/api/developer/applications', json={'name': '伪造', 'owner_id': original['owner_id']}
        )
        assert forged.status_code == 422
    assert (await admin.get(f'/api/developer/applications/{original["id"]}')).json()['code'] == '123'
