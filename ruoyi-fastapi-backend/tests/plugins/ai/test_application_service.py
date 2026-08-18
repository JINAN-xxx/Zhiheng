import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from common.vo import PageModel
from plugins.ai.domain.developer_api_errors import DeveloperApiError
from plugins.ai.entity.do.ai_developer_api_do import AiApplication
from plugins.ai.entity.vo.ai_developer_api_vo import (
    ApplicationCreateModel,
    ApplicationPageQueryModel,
    ApplicationUpdateModel,
)
from plugins.ai.service.application_service import ApplicationService

NOW = datetime(2026, 8, 18, 14, 0)
APPLICATION_ID = 'app_00000000000040008000000000000001'
SECOND_APPLICATION_ID = 'app_00000000000040008000000000000002'
OWNER_USER_ID = 7
OTHER_USER_ID = 8
UPDATED_VERSION = 2
EXPECTED_APPLICATION_COUNT = 2
EXPECTED_ROLLBACK_COUNT = 2
DISABLED_VERSION = 3


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self.commit_error: BaseException | None = None
        self.rollback_error: BaseException | None = None

    async def commit(self) -> None:
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollback_count += 1
        if self.rollback_error is not None:
            raise self.rollback_error


class FakeApplicationDao:
    def __init__(self) -> None:
        self.applications: dict[str, AiApplication] = {}
        self.events: list[str] = []
        self.create_error: BaseException | None = None
        self.update_rowcount = 1
        self.disable_rowcount = 1
        self.last_owner_filter: int | None = None

    async def create_application(self, _db: FakeSession, application: AiApplication) -> AiApplication:
        self.events.append('create_application')
        if self.create_error is not None:
            raise self.create_error
        self.applications[application.application_id] = application
        return application

    async def get_application_by_id(self, _db: FakeSession, application_id: str) -> AiApplication | None:
        self.events.append('get_application_by_id')
        return self.applications.get(application_id)

    async def get_application_by_code(self, _db: FakeSession, application_code: str) -> AiApplication | None:
        self.events.append('get_application_by_code')
        return next(
            (item for item in self.applications.values() if item.application_code == application_code),
            None,
        )

    async def get_application_for_update(self, _db: FakeSession, application_id: str) -> AiApplication | None:
        self.events.append('get_application_for_update')
        return self.applications.get(application_id)

    async def list_applications(
        self,
        _db: FakeSession,
        query: ApplicationPageQueryModel,
        *,
        owner_user_id: int | None,
        is_page: bool = False,
    ) -> PageModel | list[AiApplication]:
        self.events.append('list_applications')
        self.last_owner_filter = owner_user_id
        rows = [
            item for item in self.applications.values() if owner_user_id is None or item.owner_user_id == owner_user_id
        ]
        if is_page:
            return PageModel(
                rows=rows,
                pageNum=query.page_num,
                pageSize=query.page_size,
                total=len(rows),
                hasNext=False,
            )
        return rows

    async def update_application_with_version(
        self,
        _db: FakeSession,
        application_id: str,
        *,
        expected_version: int,
        values: dict[str, Any],
    ) -> int:
        self.events.append('update_application_with_version')
        application = self.applications.get(application_id)
        if application is None or application.version != expected_version or self.update_rowcount == 0:
            return 0
        for field, value in values.items():
            setattr(application, field, value)
        application.version = expected_version + 1
        return 1

    async def disable_application_if_active(
        self,
        _db: FakeSession,
        application_id: str,
        values: dict[str, Any],
    ) -> int:
        self.events.append('disable_application_if_active')
        application = self.applications.get(application_id)
        if application is None or application.status != 'active' or self.disable_rowcount == 0:
            return 0
        for field, value in values.items():
            setattr(application, field, value)
        application.status = 'disabled'
        application.version += 1
        return 1


class CommitFailingAsyncSession(AsyncSession):
    fail_next_commit = False

    async def commit(self) -> None:
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError('commit failed')
        await super().commit()


@pytest_asyncio.fixture
async def real_database() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[CommitFailingAsyncSession]]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_maker = async_sessionmaker(
        bind=engine,
        class_=CommitFailingAsyncSession,
        expire_on_commit=True,
    )
    async with engine.begin() as connection:
        await connection.run_sync(AiApplication.__table__.create)
    try:
        yield engine, session_maker
    finally:
        await engine.dispose()


def _application(
    application_id: str = APPLICATION_ID,
    *,
    owner_user_id: int = OWNER_USER_ID,
    status: str = 'active',
) -> AiApplication:
    return AiApplication(
        application_id=application_id,
        application_code=f'app-{application_id[-2:]}',
        application_name=f'Application {application_id[-2:]}',
        owner_user_id=owner_user_id,
        status=status,
        version=1,
        disabled_at=NOW if status == 'disabled' else None,
        created_by='alice',
        created_at=NOW,
        updated_by='alice',
        updated_at=NOW,
    )


def _service(
    *,
    session: FakeSession | None = None,
    dao: FakeApplicationDao | None = None,
) -> tuple[ApplicationService, FakeSession, FakeApplicationDao]:
    fake_session = session or FakeSession()
    fake_dao = dao or FakeApplicationDao()
    return ApplicationService(fake_session, application_dao=fake_dao), fake_session, fake_dao  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_scopes_normal_user_to_owner_and_admin_to_all() -> None:
    service, _, dao = _service()
    dao.applications = {
        APPLICATION_ID: _application(),
        SECOND_APPLICATION_ID: _application(SECOND_APPLICATION_ID, owner_user_id=OTHER_USER_ID),
    }
    query = ApplicationPageQueryModel(pageNum=1, pageSize=10)

    owner_page = await service.list_applications(
        query,
        actor_user_id=OWNER_USER_ID,
        is_admin=False,
    )
    assert owner_page.total == 1
    assert owner_page.rows[0]['applicationId'] == APPLICATION_ID
    assert dao.last_owner_filter == OWNER_USER_ID

    admin_page = await service.list_applications(
        query,
        actor_user_id=OWNER_USER_ID,
        is_admin=True,
    )
    assert admin_page.total == EXPECTED_APPLICATION_COUNT
    assert dao.last_owner_filter is None


@pytest.mark.asyncio
async def test_get_hides_foreign_application_but_admin_can_read_it() -> None:
    service, _, dao = _service()
    dao.applications[APPLICATION_ID] = _application(owner_user_id=OTHER_USER_ID)

    with pytest.raises(DeveloperApiError) as denied:
        await service.get_application(APPLICATION_ID, actor_user_id=OWNER_USER_ID, is_admin=False)
    assert denied.value.code == 'APPLICATION_NOT_FOUND'

    result = await service.get_application(APPLICATION_ID, actor_user_id=OWNER_USER_ID, is_admin=True)
    assert result.application_id == APPLICATION_ID


@pytest.mark.asyncio
async def test_create_uses_server_identity_and_commits_safe_snapshot() -> None:
    service, session, dao = _service()

    result = await service.create_application(
        ApplicationCreateModel(applicationCode='billing-prod', applicationName='Billing Production'),
        owner_user_id=OWNER_USER_ID,
        username='alice',
        now=NOW,
    )

    assert result.application_id.startswith('app_')
    assert result.application_code == 'billing-prod'
    assert result.owner_user_id == OWNER_USER_ID
    assert result.status == 'active'
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert dao.applications[result.application_id].tenant_id is None
    assert dao.applications[result.application_id].project_id is None


@pytest.mark.asyncio
async def test_create_precheck_and_integrity_error_map_to_fixed_conflict() -> None:
    service, session, dao = _service()
    dao.applications[APPLICATION_ID] = _application()
    dao.applications[APPLICATION_ID].application_code = 'billing-prod'
    payload = ApplicationCreateModel(applicationCode='billing-prod', applicationName='Billing')

    with pytest.raises(DeveloperApiError) as precheck:
        await service.create_application(payload, owner_user_id=OWNER_USER_ID, username='alice', now=NOW)
    assert precheck.value.code == 'APPLICATION_CODE_CONFLICT'
    assert session.rollback_count == 1

    dao.applications.clear()
    dao.create_error = IntegrityError('PRIVATE-SQL', {'password': 'PRIVATE'}, Exception('PRIVATE-DB'))
    with pytest.raises(DeveloperApiError) as database_conflict:
        await service.create_application(payload, owner_user_id=OWNER_USER_ID, username='alice', now=NOW)
    assert database_conflict.value.code == 'APPLICATION_CODE_CONFLICT'
    assert database_conflict.value.__cause__ is None
    assert database_conflict.value.__context__ is None
    assert 'PRIVATE' not in repr(database_conflict.value)
    assert session.rollback_count == EXPECTED_ROLLBACK_COUNT


@pytest.mark.asyncio
async def test_update_uses_locked_owner_and_version_cas() -> None:
    service, session, dao = _service()
    dao.applications[APPLICATION_ID] = _application()

    result = await service.update_application(
        APPLICATION_ID,
        ApplicationUpdateModel(applicationName='Renamed', expectedVersion=1),
        actor_user_id=OWNER_USER_ID,
        is_admin=False,
        username='alice',
        now=NOW,
    )

    assert result.application_name == 'Renamed'
    assert result.application_code == 'app-01'
    assert result.owner_user_id == OWNER_USER_ID
    assert result.version == UPDATED_VERSION
    assert session.commit_count == 1
    assert dao.events == ['get_application_for_update', 'update_application_with_version']


@pytest.mark.asyncio
async def test_stale_update_and_disabled_update_use_stable_errors_and_rollback() -> None:
    service, session, dao = _service()
    dao.applications[APPLICATION_ID] = _application()
    dao.update_rowcount = 0
    payload = ApplicationUpdateModel(applicationName='Renamed', expectedVersion=1)

    with pytest.raises(DeveloperApiError) as stale:
        await service.update_application(
            APPLICATION_ID,
            payload,
            actor_user_id=OWNER_USER_ID,
            is_admin=False,
            username='alice',
            now=NOW,
        )
    assert stale.value.code == 'APPLICATION_VERSION_CONFLICT'

    dao.applications[APPLICATION_ID] = _application(status='disabled')
    with pytest.raises(DeveloperApiError) as disabled:
        await service.update_application(
            APPLICATION_ID,
            payload,
            actor_user_id=OWNER_USER_ID,
            is_admin=False,
            username='alice',
            now=NOW,
        )
    assert disabled.value.code == 'APPLICATION_DISABLED'
    assert session.rollback_count == EXPECTED_ROLLBACK_COUNT


@pytest.mark.asyncio
async def test_disable_is_conditional_and_returns_known_values_without_entity_sync() -> None:
    service, session, dao = _service()
    dao.applications[APPLICATION_ID] = _application()

    result = await service.disable_application(
        APPLICATION_ID,
        actor_user_id=OWNER_USER_ID,
        is_admin=False,
        username='alice',
        now=NOW,
    )

    assert result.status == 'disabled'
    assert result.disabled_at == NOW
    assert result.version == UPDATED_VERSION
    assert session.commit_count == 1

    with pytest.raises(DeveloperApiError) as repeated:
        await service.disable_application(
            APPLICATION_ID,
            actor_user_id=OWNER_USER_ID,
            is_admin=False,
            username='alice',
            now=NOW,
        )
    assert repeated.value.code == 'APPLICATION_DISABLED'


@pytest.mark.asyncio
async def test_write_failure_preserves_operation_error_and_cancellation() -> None:
    service, session, dao = _service()
    payload = ApplicationCreateModel(applicationCode='billing-prod', applicationName='Billing')
    dao.create_error = RuntimeError('operation failed')
    session.rollback_error = RuntimeError('rollback failed')

    with pytest.raises(RuntimeError, match='operation failed'):
        await service.create_application(payload, owner_user_id=OWNER_USER_ID, username='alice', now=NOW)

    dao.create_error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await service.create_application(payload, owner_user_id=OWNER_USER_ID, username='alice', now=NOW)
    assert session.rollback_count == EXPECTED_ROLLBACK_COUNT


@pytest.mark.asyncio
async def test_real_sqlite_expire_on_commit_lifecycle_returns_stable_snapshots(
    real_database: tuple[AsyncEngine, async_sessionmaker[CommitFailingAsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as session:
        service = ApplicationService(session)
        created = await service.create_application(
            ApplicationCreateModel(applicationCode='billing-prod', applicationName='Billing'),
            owner_user_id=OWNER_USER_ID,
            username='alice',
            now=NOW,
        )
        updated = await service.update_application(
            created.application_id,
            ApplicationUpdateModel(applicationName='Billing V2', expectedVersion=1),
            actor_user_id=OWNER_USER_ID,
            is_admin=False,
            username='alice',
            now=NOW,
        )
        disabled = await service.disable_application(
            created.application_id,
            actor_user_id=OWNER_USER_ID,
            is_admin=False,
            username='alice',
            now=NOW,
        )

    assert updated.application_name == 'Billing V2' and updated.version == UPDATED_VERSION
    assert disabled.status == 'disabled' and disabled.version == DISABLED_VERSION

    async with session_maker() as verify_session:
        stored = await ApplicationService(verify_session).get_application(
            created.application_id,
            actor_user_id=OWNER_USER_ID,
            is_admin=False,
        )
    assert stored.application_name == 'Billing V2'
    assert stored.status == 'disabled'
    assert stored.version == DISABLED_VERSION


@pytest.mark.asyncio
async def test_real_commit_failure_rolls_back_created_application(
    real_database: tuple[AsyncEngine, async_sessionmaker[CommitFailingAsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as session:
        session.fail_next_commit = True
        service = ApplicationService(session)
        with pytest.raises(RuntimeError, match='commit failed'):
            await service.create_application(
                ApplicationCreateModel(applicationCode='billing-prod', applicationName='Billing'),
                owner_user_id=OWNER_USER_ID,
                username='alice',
                now=NOW,
            )

    async with session_maker() as verify_session:
        assert await ApplicationService(verify_session).list_applications(
            ApplicationPageQueryModel(pageNum=1, pageSize=10),
            actor_user_id=OWNER_USER_ID,
            is_admin=False,
        ) == PageModel(rows=[], pageNum=1, pageSize=10, total=0, hasNext=False)
