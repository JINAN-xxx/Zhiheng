"""Own-profile HTTP behavior and PostgreSQL credential/row-lock concurrency."""

import asyncio
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.modules.identity.public import (
    IdentityAdminService,
    IdentityService,
    PasswordChange,
    ProfilePatch,
    schema_tables,
)
from zhiheng.modules.operations.orm import OperationLog
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

PASSWORD = 'a-long-test-password'
NEW_PASSWORD = 'a-different-test-password'
ADMIN_PASSWORD = 'an-admin-reset-password'


async def test_own_profile_http_lifecycle_and_credential_free_audit(client: httpx.AsyncClient, app: Any) -> None:
    assert (await client.get('/api/auth/profile')).status_code == 401
    assert (
        await client.post(
            '/api/auth/login',
            json={
                'username': 'member',
                'password': 'platform-test-password',
            },
        )
    ).status_code == 200
    first_token = client.cookies.get('zhiheng_session')
    original = (await client.get('/api/auth/profile')).json()
    assert original['username'] == 'member' and original['department_name'] is None
    assert original['roles'] == [] and original['posts'] == []
    changed = await client.patch(
        '/api/auth/profile',
        json={
            'display_name': '资料本人',
            'email': 'private@example.test',
            'phone': '13800000000',
            'expected_version': 1,
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()['display_name'] == '资料本人' and changed.json()['version'] == 2
    assert (
        await client.patch(
            '/api/auth/profile',
            json={
                'display_name': '过期资料',
                'expected_version': 1,
            },
        )
    ).status_code == 409
    assert (
        await client.patch(
            '/api/auth/profile',
            json={
                'is_admin': True,
                'expected_version': 2,
            },
        )
    ).status_code == 422
    wrong = await client.post(
        '/api/auth/password',
        json={
            'current_password': 'a-private-wrong-password',
            'new_password': NEW_PASSWORD,
            'expected_version': 2,
        },
    )
    assert wrong.status_code == 400
    assert 'a-private-wrong-password' not in wrong.text
    assert (await client.get('/api/auth/me')).status_code == 200
    assert (
        await client.post(
            '/api/auth/login',
            json={
                'username': 'member',
                'password': 'platform-test-password',
            },
        )
    ).status_code == 200
    second_token = client.cookies.get('zhiheng_session')
    response = await client.post(
        '/api/auth/password',
        json={
            'current_password': 'platform-test-password',
            'new_password': NEW_PASSWORD,
            'expected_version': 2,
        },
    )
    assert response.status_code == 204 and response.content == b''
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert client.cookies.get('zhiheng_session') is None
    for token in (first_token, second_token):
        assert token is not None
        client.cookies.clear()
        client.cookies.set('zhiheng_session', token)
        assert (await client.get('/api/auth/me')).status_code == 401
    client.cookies.clear()
    assert (
        await client.post(
            '/api/auth/login',
            json={
                'username': 'member',
                'password': 'platform-test-password',
            },
        )
    ).status_code == 401
    assert (
        await client.post(
            '/api/auth/login',
            json={
                'username': 'member',
                'password': NEW_PASSWORD,
            },
        )
    ).status_code == 200
    assert (await client.get('/api/auth/profile')).json()['version'] == 3
    async with app.state.container.database.sessions() as session:
        logs = list((await session.execute(select(OperationLog.action, OperationLog.message))).all())
        assert {'profile.update', 'profile.change_password'} <= {row.action for row in logs}
        for secret in ('platform-test-password', NEW_PASSWORD, 'private@example.test', '13800000000', '资料本人'):
            assert secret not in str(logs)
        admin = await session.scalar(select(IdentityUser).where(IdentityUser.username == 'admin'))
        assert admin is not None and admin.display_name == '' and admin.version == 1


@dataclass
class ProfileContext:
    factory: async_sessionmaker[AsyncSession]
    identity: IdentityService
    admin: IdentityAdminService
    root: Principal
    member: Principal
    token: str


@pytest_asyncio.fixture
async def pg_profile(settings: Any) -> AsyncIterator[ProfileContext]:
    # The shared settings fixture owns a fresh UUID database and its cleanup.
    engine = create_async_engine(settings.database_url.get_secret_value())
    async with engine.begin() as connection:
        for table in schema_tables():
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService()
    async with factory.begin() as session:
        root = await identity.create_user(session, username='root', password=PASSWORD, is_admin=True)
        member = await identity.create_user(session, username='member', password=PASSWORD)
        _, token = await identity.login(session, username='member', password=PASSWORD)
    try:
        yield ProfileContext(factory, identity, IdentityAdminService(identity), root, member, token)
    finally:
        await engine.dispose()


async def test_concurrent_profile_updates_allow_one_version_winner(pg_profile: ProfileContext) -> None:
    async def change(name: str) -> str:
        try:
            async with pg_profile.factory.begin() as session:
                await pg_profile.identity.update_profile(
                    session, user_id=pg_profile.member.id, body=ProfilePatch(display_name=name, expected_version=1)
                )
            return 'success'
        except AppError as exc:
            return exc.code

    results = await asyncio.wait_for(asyncio.gather(change('第一份'), change('第二份')), timeout=10)
    assert sorted(results) == ['success', 'version_conflict']
    async with pg_profile.factory() as session:
        profile = await pg_profile.identity.profile(session, user_id=pg_profile.member.id)
        assert profile.version == 2 and profile.display_name in ('第一份', '第二份')


@pytest.mark.parametrize('phase', ['verify', 'hash'])
@pytest.mark.parametrize('mutation', ['password_reset', 'disable', 'delete', 'profile_edit', 'hash_only'])
async def test_password_change_rechecks_user_after_argon2_without_holding_row_lock(
    pg_profile: ProfileContext,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    mutation: str,
) -> None:
    reached = threading.Event()
    release = threading.Event()
    original_verify = PasswordHasher.verify
    original_hash = PasswordHasher.hash

    def paused_verify(hasher: PasswordHasher, encoded: str, password: str) -> bool:
        if password == PASSWORD:
            reached.set()
            assert release.wait(timeout=10), 'test must release real Argon2 verification'
        return original_verify(hasher, encoded, password)

    def paused_hash(hasher: PasswordHasher, password: str) -> str:
        if password == NEW_PASSWORD:
            reached.set()
            assert release.wait(timeout=10), 'test must release real Argon2 hashing'
        return original_hash(hasher, password)

    if phase == 'verify':
        monkeypatch.setattr(PasswordHasher, 'verify', paused_verify)
    else:
        monkeypatch.setattr(PasswordHasher, 'hash', paused_hash)

    async def change_password() -> None:
        async with pg_profile.factory.begin() as session:
            await pg_profile.identity.change_password(
                session,
                user_id=pg_profile.member.id,
                body=PasswordChange(current_password=PASSWORD, new_password=NEW_PASSWORD, expected_version=1),
            )

    async def mutate_account() -> None:
        async with pg_profile.factory.begin() as session:
            if mutation == 'password_reset':
                await pg_profile.admin.reset_password(
                    session,
                    pg_profile.root,
                    pg_profile.member.id,
                    ADMIN_PASSWORD,
                    expected_version=1,
                )
            elif mutation == 'delete':
                await pg_profile.admin.delete(
                    session,
                    pg_profile.root,
                    'users',
                    pg_profile.member.id,
                    expected_version=1,
                )
            elif mutation == 'hash_only':
                encoded = await pg_profile.identity.hash_password(ADMIN_PASSWORD)
                await session.execute(
                    update(IdentityUser)
                    .where(IdentityUser.id == pg_profile.member.id)
                    .values(
                        password_hash=encoded,
                    )
                )
            else:
                fields = {'is_active': False} if mutation == 'disable' else {'display_name': '管理员修改'}
                await pg_profile.admin.update(
                    session,
                    pg_profile.root,
                    'users',
                    pg_profile.member.id,
                    fields,
                    expected_version=1,
                )

    task = asyncio.create_task(change_password())
    try:
        assert await asyncio.to_thread(reached.wait, 5), 'password change must reach Argon2'
        # This commit must finish while CPU work remains paused, proving no user lock is held.
        await asyncio.wait_for(mutate_account(), timeout=5)
    finally:
        release.set()
    with pytest.raises(AppError) as stale:
        await asyncio.wait_for(task, timeout=5)
    assert stale.value.status_code == (401 if mutation in ('disable', 'delete') else 409)
    async with pg_profile.factory() as session:
        user = await session.get(IdentityUser, pg_profile.member.id)
        assert user is not None and user.version == (1 if mutation == 'hash_only' else 2)
        expected_password = ADMIN_PASSWORD if mutation in ('password_reset', 'hash_only') else PASSWORD
        assert original_verify(PasswordHasher(), user.password_hash, expected_password)
        count = await session.scalar(
            select(func.count())
            .select_from(IdentitySession)
            .where(
                IdentitySession.user_id == pg_profile.member.id,
            )
        )
        assert count == (1 if mutation in ('profile_edit', 'hash_only') else 0)


async def test_password_change_revokes_login_committed_while_hashing(
    pg_profile: ProfileContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reached = threading.Event()
    release = threading.Event()
    original_hash = PasswordHasher.hash

    def paused_hash(hasher: PasswordHasher, password: str) -> str:
        if password == NEW_PASSWORD:
            reached.set()
            assert release.wait(timeout=10), 'test must release real Argon2 hashing'
        return original_hash(hasher, password)

    monkeypatch.setattr(PasswordHasher, 'hash', paused_hash)

    async def change_password() -> None:
        async with pg_profile.factory.begin() as session:
            await pg_profile.identity.change_password(
                session,
                user_id=pg_profile.member.id,
                body=PasswordChange(current_password=PASSWORD, new_password=NEW_PASSWORD, expected_version=1),
            )

    task = asyncio.create_task(change_password())
    try:
        assert await asyncio.to_thread(reached.wait, 5)
        async with pg_profile.factory.begin() as session:
            _, token = await asyncio.wait_for(
                pg_profile.identity.login(session, username='member', password=PASSWORD),
                timeout=5,
            )
    finally:
        release.set()
    await asyncio.wait_for(task, timeout=5)
    async with pg_profile.factory() as session:
        assert (await pg_profile.identity.profile(session, user_id=pg_profile.member.id)).version == 2
        for existing_token in (pg_profile.token, token):
            with pytest.raises(AppError) as invalid:
                await pg_profile.identity.authenticate(session, existing_token)
            assert invalid.value.status_code == 401
