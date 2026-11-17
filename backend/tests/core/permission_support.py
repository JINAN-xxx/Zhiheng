"""Explicit canonical grants for tests; no production authorization is bypassed."""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI


def grants(module: str, *actions: str) -> frozenset[str]:
    return frozenset({f'{module}:enabled', f'{module}:read', *(f'{module}:{action}' for action in actions)})


async def grant_member(admin: httpx.AsyncClient, permissions: Iterable[str], *, username: str = 'member') -> None:
    """Give the fixture member functional rights so ownership tests reach the ACL."""
    users = await admin.get('/api/admin/users', params={'search': username})
    assert users.status_code == 200, users.text
    user = next(row for row in users.json()['items'] if row['username'] == username)
    role = await admin.post(
        '/api/admin/roles',
        json={'code': 'test-' + uuid4().hex, 'name': 'Test functional access', 'permissions': sorted(set(permissions))},
    )
    assert role.status_code == 201, role.text
    assigned = await admin.patch(
        f'/api/admin/users/{user["id"]}',
        json={'expected_version': user['version'], 'role_ids': [*user['role_ids'], role.json()['id']]},
    )
    assert assigned.status_code == 200, assigned.text


@asynccontextmanager
async def account(
    admin: httpx.AsyncClient, app: FastAPI, *permissions: str
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, Any], dict[str, Any]]]:
    suffix = uuid4().hex[:12]
    role = await admin.post(
        '/api/admin/roles', json={'code': suffix, 'name': suffix, 'permissions': sorted(set(permissions))}
    )
    assert role.status_code == 201, role.text
    user = await admin.post(
        '/api/admin/users',
        json={'username': suffix, 'password': 'platform-test-password', 'role_ids': [role.json()['id']]},
    )
    assert user.status_code == 201, user.text
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'content-type': 'application/json'}
    ) as client:
        login = await client.post('/api/auth/login', json={'username': suffix, 'password': 'platform-test-password'})
        assert login.status_code == 200, login.text
        assert not login.json()['user']['is_admin']
        assert set(login.json()['user']['permissions']) == set(permissions)
        yield client, role.json(), user.json()


@pytest.fixture
async def member_agent_work_permissions(admin: httpx.AsyncClient) -> None:
    """Ownership suites need functional rights before testing another owner's ACL."""
    await grant_member(
        admin,
        grants('platform:agents', 'use', 'create', 'update', 'delete', 'debug', 'publish')
        | grants('platform:work', 'use', 'create', 'update', 'delete')
        | grants('platform:developer', 'create')
        | grants('platform:models')
        | grants('platform:usage'),
    )


@pytest.fixture
async def member_knowledge_permissions(admin: httpx.AsyncClient) -> None:
    """Resource ACL suites grant functional rights before testing ownership/sharing."""
    await grant_member(
        admin, grants('platform:knowledge', 'query', 'create', 'update', 'delete', 'import', 'rebuild', 'share')
    )


@pytest.fixture
async def member_file_permissions(admin: httpx.AsyncClient) -> None:
    """Ownership tests need file functions enabled without identity-directory grants."""
    await grant_member(admin, grants('ai:storage', 'configure', 'maintain', 'acl', 'transfer', 'delete', 'parse'))
