"""Concurrency guards against real PostgreSQL row locks in an isolated fixture database."""

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from zhiheng.modules.identity.admin_orm import IdentityRole, IdentityUserRole
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.modules.identity.public import IdentityAdminService, IdentityService
from zhiheng.modules.identity.schema import schema_tables
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


@pytest_asyncio.fixture
async def pg_identity(settings: Any) -> AsyncIterator[tuple[Any, IdentityAdminService, Principal, Principal]]:
    # settings creates a disposable UUID database. Never touches a running instance.
    engine = create_async_engine(settings.database_url.get_secret_value())
    async with engine.begin() as connection:
        for table in schema_tables():
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService()
    async with factory.begin() as session:
        first = await identity.create_user(
            session, username='first-admin', password='a-long-test-password', is_admin=True
        )
        second = await identity.create_user(
            session, username='second-admin', password='a-long-test-password', is_admin=True
        )
    try:
        yield factory, IdentityAdminService(identity), first, second
    finally:
        await engine.dispose()


async def test_concurrent_admin_demotions_retain_one_active_admin(pg_identity: tuple[Any, ...]) -> None:
    factory, service, first, second = pg_identity

    async def demote(actor: Principal, target: Principal) -> str:
        try:
            async with factory.begin() as session:
                await service.update(session, actor, 'users', target.id, {'is_admin': False}, expected_version=1)
            return 'success'
        except AppError as exc:
            return exc.code

    results = await asyncio.wait_for(asyncio.gather(demote(first, second), demote(second, first)), timeout=10)
    assert sorted(results) == ['last_admin_protected', 'success']
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(IdentityUser)
            .where(
                IdentityUser.is_admin.is_(True),
                IdentityUser.is_active.is_(True),
            )
        )
        assert count == 1


async def test_concurrent_updates_cannot_overwrite_same_version(pg_identity: tuple[Any, ...]) -> None:
    factory, service, first, _ = pg_identity
    async with factory.begin() as session:
        user = await service.create(session, first, 'users', {'username': 'member', 'password': 'a-long-test-password'})

    async def update_name(name: str) -> str:
        try:
            async with factory.begin() as session:
                await service.update(session, first, 'users', user['id'], {'display_name': name}, expected_version=1)
            return 'success'
        except AppError as exc:
            return exc.code

    results = await asyncio.wait_for(asyncio.gather(update_name('one'), update_name('two')), timeout=10)
    assert sorted(results) == ['success', 'version_conflict']


async def test_concurrent_reciprocal_department_moves_cannot_form_cycle(pg_identity: tuple[Any, ...]) -> None:
    factory, service, first, _ = pg_identity
    async with factory.begin() as session:
        a = await service.create(session, first, 'departments', {'name': 'A'})
        b = await service.create(session, first, 'departments', {'name': 'B'})

    async def move(child: dict[str, Any], parent: dict[str, Any]) -> str:
        try:
            async with factory.begin() as session:
                await service.update(
                    session, first, 'departments', child['id'], {'parent_id': parent['id']}, expected_version=1
                )
            return 'success'
        except AppError as exc:
            return exc.code

    results = await asyncio.wait_for(asyncio.gather(move(a, b), move(b, a)), timeout=10)
    assert sorted(results) == ['success', 'tree_cycle']


async def test_existing_token_observes_committed_role_revocation(pg_identity: tuple[Any, ...]) -> None:
    factory, service, first, _ = pg_identity
    async with factory.begin() as session:
        role = await service.create(
            session,
            first,
            'roles',
            {
                'code': 'reader',
                'name': 'Reader',
                'permissions': ['system:users:read'],
            },
        )
        await service.create(
            session,
            first,
            'users',
            {
                'username': 'reader',
                'password': 'a-long-test-password',
                'role_ids': [role['id']],
            },
        )
        principal, token = await service.identity.login(session, username='reader', password='a-long-test-password')
        assert principal.permissions == frozenset({'system:users:read'})
    async with factory.begin() as session:
        await service.update(session, first, 'roles', role['id'], {'permissions': []}, expected_version=1)
    async with factory() as session:
        assert (await service.identity.authenticate(session, token)).permissions == frozenset()


async def test_concurrent_first_admin_creation_shares_one_builtin_role(settings: Any) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    async with engine.begin() as connection:
        for table in schema_tables():
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService()
    second_insert = asyncio.Event()
    insert_count = 0

    def observe_insert(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
        nonlocal insert_count
        if statement.startswith('INSERT INTO identity_roles '):
            insert_count += 1
            if insert_count == 2:
                second_insert.set()

    async def create_second() -> Principal:
        async with factory.begin() as session:
            return await identity.create_user(
                session, username='second-admin', password='a-long-test-password', is_admin=True
            )

    event.listen(engine.sync_engine, 'before_cursor_execute', observe_insert)
    task = None
    try:
        async with factory() as first_session:
            first = await identity.create_user(
                first_session, username='first-admin', password='a-long-test-password', is_admin=True
            )
            task = asyncio.create_task(create_second())
            # Hold the winning unique key uncommitted until the second transaction
            # really attempts its insert, forcing the savepoint recovery path.
            await asyncio.wait_for(second_insert.wait(), timeout=10)
            await first_session.commit()
            second = await asyncio.wait_for(task, timeout=10)
        async with factory() as session:
            roles = list(await session.scalars(select(IdentityRole)))
            assert len(roles) == 1 and roles[0].system_key == 'super_admin'
            assert set(await session.scalars(select(IdentityUserRole.user_id))) == {first.id, second.id}
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        event.remove(engine.sync_engine, 'before_cursor_execute', observe_insert)
        await engine.dispose()


@pytest.mark.parametrize('mutation', ['password_reset', 'disable', 'delete'])
async def test_login_cannot_issue_session_after_credentials_change_during_verify(
    pg_identity: tuple[Any, ...],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    factory, service, first, _ = pg_identity
    async with factory.begin() as session:
        user = await service.create(
            session,
            first,
            'users',
            {'username': 'racing-login', 'password': 'a-long-test-password'},
        )
    verifying = threading.Event()
    release = threading.Event()
    original_verify = PasswordHasher.verify

    def paused_verify(hasher: PasswordHasher, encoded: str, password: str) -> bool:
        verifying.set()
        assert release.wait(timeout=10), 'test must release real Argon2 verification'
        return original_verify(hasher, encoded, password)

    monkeypatch.setattr(PasswordHasher, 'verify', paused_verify)

    async def login() -> tuple[Principal, str]:
        async with factory.begin() as session:
            return await service.identity.login(session, username='racing-login', password='a-long-test-password')

    async def mutate_account() -> None:
        async with factory.begin() as session:
            if mutation == 'password_reset':
                await service.reset_password(session, first, user['id'], 'a-new-test-password', expected_version=1)
            elif mutation == 'disable':
                await service.update(session, first, 'users', user['id'], {'is_active': False}, expected_version=1)
            else:
                await service.delete(session, first, 'users', user['id'], expected_version=1)

    task = asyncio.create_task(login())
    try:
        assert await asyncio.to_thread(verifying.wait, 5), 'login must reach real password verification'
        # This must commit while verification is paused: no row locks around slow Argon2 work.
        await asyncio.wait_for(mutate_account(), timeout=5)
    finally:
        release.set()
    with pytest.raises(AppError) as invalid:
        await asyncio.wait_for(task, timeout=5)
    assert invalid.value.status_code == 401
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(IdentitySession).where(IdentitySession.user_id == user['id'])
            )
            == 0
        )
