from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from zhiheng.modules.identity.admin_orm import (
    IdentityDepartment,
    IdentityPost,
    IdentityRole,
    IdentityUserPost,
    IdentityUserRole,
)
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.modules.identity.public import IdentityService, PasswordChange, ProfilePatch, schema_tables
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

PASSWORD = 'a-long-test-password'
NEW_PASSWORD = 'a-different-test-password'


@dataclass
class Context:
    factory: async_sessionmaker[AsyncSession]
    identity: IdentityService
    member: Principal


@pytest_asyncio.fixture
async def context() -> AsyncIterator[Context]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for table in schema_tables():
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService()
    async with factory.begin() as session:
        member = await identity.create_user(session, username='member', password=PASSWORD)
    try:
        yield Context(factory, identity, member)
    finally:
        await engine.dispose()


async def test_profile_returns_association_names_without_credentials(context: Context) -> None:
    async with context.factory.begin() as session:
        department = IdentityDepartment(name='研发部')
        post = IdentityPost(code='engineer', name='工程师')
        role = IdentityRole(code='reader', name='审阅员', permissions=[])
        session.add_all([department, post, role])
        await session.flush()
        user = await session.get(IdentityUser, context.member.id)
        assert user is not None
        user.department_id = department.id
        session.add_all(
            [
                IdentityUserRole(user_id=user.id, role_id=role.id),
                IdentityUserPost(user_id=user.id, post_id=post.id),
            ]
        )
    async with context.factory() as session:
        profile = await context.identity.profile(session, user_id=context.member.id)
        assert profile.username == 'member' and profile.version == 1
        assert profile.department_name == '研发部'
        assert profile.roles == ['审阅员'] and profile.posts == ['工程师']
        assert 'password' not in profile.model_dump_json() and PASSWORD not in str(profile)


async def test_profile_patch_rejects_stale_versions_without_overwriting(context: Context) -> None:
    async with context.factory.begin() as session:
        changed = await context.identity.update_profile(
            session, user_id=context.member.id, body=ProfilePatch(display_name='本人', expected_version=1)
        )
        assert changed.version == 2 and changed.display_name == '本人'
    with pytest.raises(AppError) as stale:
        async with context.factory.begin() as session:
            await context.identity.update_profile(
                session, user_id=context.member.id, body=ProfilePatch(display_name='过期', expected_version=1)
            )
    assert stale.value.status_code == 409
    async with context.factory() as session:
        profile = await context.identity.profile(session, user_id=context.member.id)
        assert profile.display_name == '本人' and profile.version == 2


@pytest.mark.parametrize(
    'current, new, version, status',
    [
        ('wrong-password', NEW_PASSWORD, 1, 400),
        (PASSWORD, PASSWORD, 1, 400),
        (PASSWORD, NEW_PASSWORD, 2, 409),
    ],
)
async def test_password_change_failures_preserve_version_and_sessions(
    context: Context,
    current: str,
    new: str,
    version: int,
    status: int,
) -> None:
    async with context.factory.begin() as session:
        _, token = await context.identity.login(session, username='member', password=PASSWORD)
    with pytest.raises(AppError) as failed:
        async with context.factory.begin() as session:
            await context.identity.change_password(
                session,
                user_id=context.member.id,
                body=PasswordChange(current_password=current, new_password=new, expected_version=version),
            )
    assert failed.value.status_code == status
    assert current not in failed.value.message and new not in failed.value.message
    async with context.factory() as session:
        assert (await context.identity.authenticate(session, token)).id == context.member.id
        assert (await context.identity.profile(session, user_id=context.member.id)).version == 1


async def test_password_change_increments_version_and_revokes_every_session(context: Context) -> None:
    async with context.factory.begin() as session:
        await context.identity.login(session, username='member', password=PASSWORD)
        await context.identity.login(session, username='member', password=PASSWORD)
    async with context.factory.begin() as session:
        await context.identity.change_password(
            session,
            user_id=context.member.id,
            body=PasswordChange(current_password=PASSWORD, new_password=NEW_PASSWORD, expected_version=1),
        )
    async with context.factory() as session:
        assert await session.scalar(select(func.count()).select_from(IdentitySession)) == 0
        assert (await context.identity.profile(session, user_id=context.member.id)).version == 2
        await context.identity.login(session, username='member', password=NEW_PASSWORD)
