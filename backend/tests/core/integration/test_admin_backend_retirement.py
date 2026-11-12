"""Real PostgreSQL verification of retirement without deleting historical facts."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select, text, update

from zhiheng.modules.identity.admin_orm import IdentityRole

ARCHIVED = (
    'identity_menus',
    'identity_posts',
    'identity_user_posts',
    'operations_dictionaries',
    'operations_dictionary_items',
)


async def fingerprints(app: FastAPI) -> dict[str, Any]:
    async with app.state.container.database.sessions() as session:
        return {
            name: tuple(
                (
                    await session.execute(
                        text(
                            f"SELECT count(*), md5(coalesce(string_agg(row_to_json(t)::text, '' ORDER BY row_to_json(t)::text), '')) FROM {name} t"
                        )
                    )
                ).one()
            )
            for name in ARCHIVED
        }


async def seed_archived_rows(app: FastAPI) -> UUID:
    now = datetime.now(timezone.utc)
    post, dictionary = uuid4(), uuid4()
    async with app.state.container.database.sessions.begin() as session:
        user = await session.scalar(text("SELECT id FROM identity_users WHERE username='member'"))
        rows = [
            (
                'identity_menus',
                {
                    'parent_id': None,
                    'name': 'Archived menu',
                    'path': '/admin/users',
                    'permission': 'system:users:read',
                    'sort_order': 0,
                    'is_visible': True,
                },
            ),
            (
                'identity_posts',
                {
                    'id': post,
                    'code': 'archived-post',
                    'name': 'Archived post',
                    'sort_order': 0,
                    'is_active': True,
                    'remark': '',
                },
            ),
            (
                'operations_dictionaries',
                {
                    'id': dictionary,
                    'code': 'archived-dict',
                    'name': 'Archived dictionary',
                    'is_active': True,
                    'remark': '',
                },
            ),
            (
                'operations_dictionary_items',
                {
                    'dictionary_id': dictionary,
                    'label': 'Archived item',
                    'value': 'old',
                    'sort_order': 0,
                    'is_active': True,
                    'remark': '',
                },
            ),
        ]
        for name, fields in rows:
            values = {'id': uuid4(), 'version': 1, 'created_at': now, 'updated_at': now, **fields}
            await session.execute(
                text(f'INSERT INTO {name} ({",".join(values)}) VALUES ({",".join(":" + key for key in values)})'),
                values,
            )
        await session.execute(
            text('INSERT INTO identity_user_posts (user_id,post_id) VALUES (:user,:post)'), {'user': user, 'post': post}
        )
    return user


async def test_archived_data_survives_active_user_and_profile_operations(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    user = await seed_archived_rows(app)
    before = await fingerprints(app)
    assert all(count == 1 for count, _ in before.values())
    for area in ('menus', 'posts', 'dictionaries'):
        assert (await admin.get('/api/admin/' + area)).status_code == 404
    assert set((await admin.get('/api/admin/users/options')).json()) == {'roles', 'departments'}
    current = (await admin.get(f'/api/admin/users/{user}')).json()
    assert 'post_ids' not in current
    changed = await admin.patch(
        f'/api/admin/users/{user}', json={'expected_version': current['version'], 'display_name': 'Updated member'}
    )
    assert changed.status_code == 200 and 'post_ids' not in changed.json()
    rejected = await admin.patch(
        f'/api/admin/users/{user}', json={'expected_version': changed.json()['version'], 'post_ids': []}
    )
    assert rejected.status_code == 422
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        assert (
            await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        ).status_code == 200
        profile = await member.get('/api/auth/profile')
        assert profile.status_code == 200 and 'posts' not in profile.json()
    assert await fingerprints(app) == before
    assert not any(
        path.startswith(('/api/admin/posts', '/api/admin/menus', '/api/admin/dictionaries'))
        for path in app.openapi()['paths']
    )


async def test_existing_cookie_and_role_edits_ignore_stored_retired_codes(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    role = await admin.post(
        '/api/admin/roles', json={'name': 'Legacy permissions', 'permissions': ['system:notices:read']}
    )
    assert role.status_code == 201
    identifier = UUID(role.json()['id'])
    legacy = ['system:notices:read', 'system:posts:read', 'system:menus:write', 'system:dictionaries:write']
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(update(IdentityRole).where(IdentityRole.id == identifier).values(permissions=legacy))
        user = await session.scalar(text("SELECT id FROM identity_users WHERE username='member'"))
    assigned = await admin.patch(
        f'/api/admin/users/{user}', json={'expected_version': 1, 'role_ids': [str(identifier)]}
    )
    assert assigned.status_code == 200, assigned.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        login = await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert login.status_code == 200
        assert login.json()['user']['permissions'] == ['system:notices:read']
        edited = await admin.patch(
            f'/api/admin/roles/{identifier}', json={'expected_version': 1, 'name': 'Renamed legacy role'}
        )
        assert edited.status_code == 200 and edited.json()['permissions'] == ['system:notices:read']
        async with app.state.container.database.sessions() as session:
            assert await session.scalar(select(IdentityRole.permissions).where(IdentityRole.id == identifier)) == legacy
        cleaned = await admin.patch(
            f'/api/admin/roles/{identifier}', json={'expected_version': 2, 'permissions': legacy}
        )
        assert cleaned.status_code == 200 and cleaned.json()['permissions'] == ['system:notices:read']
        assert (await member.get('/api/auth/me')).json()['permissions'] == ['system:notices:read']
    denied = await admin.post('/api/admin/roles', json={'name': 'Cannot revive', 'permissions': ['system:posts:write']})
    assert denied.status_code == 400
