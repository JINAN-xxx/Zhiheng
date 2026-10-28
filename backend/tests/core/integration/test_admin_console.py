import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from zhiheng.infrastructure.database import SCHEMA_REVISION


async def test_role_permissions_apply_to_existing_sessions_and_monitor(admin: httpx.AsyncClient, app: FastAPI) -> None:
    role = await admin.post(
        '/api/admin/roles',
        json={
            'code': 'auditor',
            'name': '审计查看',
            'permissions': ['system:monitor:read', 'system:logs:read'],
        },
    )
    assert role.status_code == 201, role.text
    user = await admin.post(
        '/api/admin/users',
        json={
            'username': 'auditor',
            'password': 'audit-platform-password',
            'role_ids': [role.json()['id']],
        },
    )
    assert user.status_code == 201, user.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        login = await member.post(
            '/api/auth/login',
            json={
                'username': 'auditor',
                'password': 'audit-platform-password',
            },
        )
        assert login.status_code == 200
        assert login.json()['user']['permissions'] == ['system:logs:read', 'system:monitor:read']
        monitor = await member.get('/api/admin/monitor')
        assert monitor.status_code == 200, monitor.text
        assert monitor.json()['database']['status'] == 'ok'
        assert monitor.json()['redis']['status'] == 'ok'
        assert monitor.json()['schema_version'] == SCHEMA_REVISION
        assert (await member.get('/api/admin/users')).status_code == 403
        assert (await member.post('/api/admin/roles', json={'code': 'deny', 'name': '拒绝'})).status_code == 403
        updated = await admin.patch(
            '/api/admin/roles/' + role.json()['id'],
            json={
                'permissions': [],
                'expected_version': role.json()['version'],
            },
        )
        assert updated.status_code == 200, updated.text
        assert (await member.get('/api/auth/me')).json()['permissions'] == []
        assert (await member.get('/api/admin/monitor')).status_code == 403


async def test_login_failures_are_audited_without_passwords(admin: httpx.AsyncClient, app: FastAPI) -> None:
    secret = 'this-value-must-never-enter-audit'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as guest:
        denied = await guest.post('/api/auth/login', json={'username': 'member', 'password': secret})
        assert denied.status_code == 401
    logs = await admin.get('/api/admin/logs', params={'kind': 'login', 'outcome': 'failed'})
    assert logs.status_code == 200, logs.text
    assert logs.json()['total'] == 1
    assert logs.json()['items'][0]['username'] == 'member'
    assert secret not in logs.text


async def test_published_announcements_are_live_and_configured(admin: httpx.AsyncClient, app: FastAPI) -> None:
    draft = await admin.post('/api/admin/notices', json={'title': '维护', 'content': '维护安排'})
    assert draft.status_code == 201, draft.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        assert (await member.get('/api/admin/notices/published')).status_code == 401
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get('/api/admin/notices/published')).json()['total'] == 0
        published = await admin.patch(
            '/api/admin/notices/' + draft.json()['id'],
            json={
                'status': 'published',
                'expected_version': draft.json()['version'],
            },
        )
        assert published.status_code == 200
        assert (await member.get('/api/admin/notices/published')).json()['total'] == 1
        setting = await admin.post(
            '/api/admin/configs',
            json={
                'key': 'console.notice_enabled',
                'name': '公告展示',
                'value': 'false',
            },
        )
        assert setting.status_code == 201
        assert (await member.get('/api/admin/notices/published')).json()['total'] == 0


@pytest.mark.parametrize(
    'username, secret',
    [
        ('password=private-login-sentinel', 'private-login-sentinel'),
        ('https://user:private-url-sentinel@example.test', 'private-url-sentinel'),
    ],
)
async def test_untrusted_login_names_cannot_put_secrets_in_audit(
    admin: httpx.AsyncClient,
    app: FastAPI,
    username: str,
    secret: str,
) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as guest:
        denied = await guest.post('/api/auth/login', json={'username': username, 'password': 'irrelevant-password'})
        assert denied.status_code == 401
    logs = await admin.get('/api/admin/logs', params={'kind': 'login', 'outcome': 'failed'})
    assert logs.status_code == 200
    assert logs.json()['total'] == 1
    assert secret not in logs.text
    async with app.state.container.database.sessions() as session:
        names = await session.scalars(text("SELECT username FROM operations_logs WHERE outcome='failed'"))
        assert all(secret not in value for value in names)
