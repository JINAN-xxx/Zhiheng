"""Operations contracts against an owned, real SQLite database (never skipped)."""

from collections.abc import AsyncIterator
from importlib.util import find_spec
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry

from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

Api = tuple[FastAPI, httpx.AsyncClient]


def test_operations_module_exposes_explicit_schema_inventory() -> None:
    assert find_spec('zhiheng.modules.operations') is not None, 'operations domain must be implemented'
    from zhiheng.modules.operations.public import schema_tables

    assert {table.name for table in schema_tables()} == {
        'operations_dictionaries',
        'operations_dictionary_items',
        'operations_configs',
        'operations_notices',
        'operations_logs',
    }


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    from zhiheng.modules.operations.public import schema_tables

    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "operations.db"}')

    @event.listens_for(engine.sync_engine, 'connect')
    def foreign_keys(connection: SQLiteConnection, _record: ConnectionPoolEntry) -> None:
        # Python sqlite legacy transaction control otherwise commits an initial SAVEPOINT independently.
        connection.isolation_level = None
        connection.execute('PRAGMA foreign_keys=ON')

    @event.listens_for(engine.sync_engine, 'begin')
    def begin(connection: Connection) -> None:
        connection.exec_driver_sql('BEGIN')

    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: schema_tables()[0].metadata.create_all(sync, tables=schema_tables()))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as value:
            yield value
    finally:
        await engine.dispose()


@pytest.fixture
def admin() -> Principal:
    return Principal(uuid4(), 'admin', True)


async def test_dictionary_item_crud_versions_and_delete_dependency(session: AsyncSession, admin: Principal) -> None:
    from zhiheng.modules.operations.public import (
        CreateDictionary,
        CreateDictionaryItem,
        OperationsService,
        PatchDictionaryItem,
    )

    service = OperationsService()
    dictionary = await service.create_dictionary(session, admin, CreateDictionary(code='priority', name='优先级'))
    item = await service.create_dictionary_item(
        session, admin, dictionary.id, CreateDictionaryItem(label='紧急', value='urgent', sort_order=3)
    )
    assert item.version == 1 and item.dictionary_id == dictionary.id
    with pytest.raises(AppError, match='数据') as dependency:
        await service.delete_dictionary(session, admin, dictionary.id, expected_version=1)
    assert dependency.value.status_code == 409
    updated = await service.update_dictionary_item(
        session, admin, dictionary.id, item.id, PatchDictionaryItem(expected_version=1, label='高')
    )
    assert updated.label == '高' and updated.version == 2
    with pytest.raises(AppError) as stale:
        await service.delete_dictionary_item(session, admin, dictionary.id, item.id, expected_version=1)
    assert stale.value.status_code == 409
    page = await service.list_dictionary_items(session, admin, dictionary.id, search='高', page=1, page_size=1)
    assert page.total == 1 and page.items[0].id == item.id
    other = await service.create_dictionary(session, admin, CreateDictionary(code='other', name='其他'))
    with pytest.raises(AppError) as wrong_parent:
        await service.get_dictionary_item(session, admin, other.id, item.id)
    assert wrong_parent.value.status_code == 404
    await service.delete_dictionary_item(session, admin, dictionary.id, item.id, expected_version=2)
    await service.delete_dictionary(session, admin, dictionary.id, expected_version=1)
    with pytest.raises(AppError) as missing:
        await service.get_dictionary(session, admin, dictionary.id)
    assert missing.value.status_code == 404


async def test_dictionary_unique_code_search_status_and_atomic_update(session: AsyncSession, admin: Principal) -> None:
    from zhiheng.modules.operations.public import CreateDictionary, OperationsService, PatchDictionary

    service = OperationsService()
    row = await service.create_dictionary(session, admin, CreateDictionary(code='status', name='100% 状态'))
    await service.create_dictionary(
        session, admin, CreateDictionary(code='inactive', name='1000 状态', is_active=False)
    )
    with pytest.raises(AppError) as duplicate:
        await service.create_dictionary(session, admin, CreateDictionary(code='status', name='重复'))
    assert duplicate.value.status_code == 409
    page = await service.list_dictionaries(session, admin, search='%', is_active=True, page=1, page_size=20)
    assert page.total == 1 and page.items[0].id == row.id
    fresh = await service.update_dictionary(session, admin, row.id, PatchDictionary(expected_version=1, name='更新'))
    assert fresh.version == 2
    with pytest.raises(AppError) as stale:
        await service.update_dictionary(session, admin, row.id, PatchDictionary(expected_version=1, name='旧值'))
    assert stale.value.status_code == 409
    assert (await service.get_dictionary(session, admin, row.id)).name == '更新'


async def test_configs_validate_actual_public_parameter_and_control_member_notices(
    session: AsyncSession, admin: Principal
) -> None:
    from zhiheng.modules.operations.public import (
        CreateConfig,
        CreateNotice,
        OperationsService,
        PatchConfig,
        PatchNotice,
    )

    service = OperationsService()
    member = Principal(uuid4(), 'member', False)
    draft = await service.create_notice(session, admin, CreateNotice(title='草稿', content='内部内容'))
    published = await service.create_notice(
        session, admin, CreateNotice(title='公告', content='<script>alert(1)</script>\n纯文本', status='published')
    )
    page = await service.published_notices(session, member)
    assert page.total == 1 and page.items[0].id == published.id
    assert page.items[0].content == '<script>alert(1)</script>\n纯文本'
    assert page.items[0].content_format == 'plain_text'
    config = await service.create_config(
        session, admin, CreateConfig(key='console.notice_enabled', name='显示公告', value='false')
    )
    assert (await service.published_notices(session, member)).total == 0
    config = await service.update_config(session, admin, config.id, PatchConfig(expected_version=1, value='true'))
    assert config.version == 2
    assert (await service.published_notices(session, member)).total == 1
    await service.update_notice(session, admin, draft.id, PatchNotice(expected_version=1, status='published'))
    assert (await service.published_notices(session, member)).total == 2
    await service.delete_config(session, admin, config.id, expected_version=2)
    assert (await service.published_notices(session, member)).total == 2


@pytest.mark.parametrize(
    'key',
    ['DATABASE_URL', 'provider.api_key', 'console.secret', 'console.password', 'console.token', 'console.unknown'],
)
async def test_configs_reject_deployment_and_secret_keys(session: AsyncSession, admin: Principal, key: str) -> None:
    from zhiheng.modules.operations.public import CreateConfig, OperationsService

    with pytest.raises((AppError, ValidationError)):
        await OperationsService().create_config(session, admin, CreateConfig(key=key, name='不能维护', value='private'))


async def test_configs_reject_nonboolean_notice_switch_and_duplicate(session: AsyncSession, admin: Principal) -> None:
    from zhiheng.modules.operations.public import CreateConfig, OperationsService, PatchConfig

    service = OperationsService()
    with pytest.raises((AppError, ValidationError)):
        await service.create_config(
            session, admin, CreateConfig(key='console.notice_enabled', name='公告', value='yes')
        )
    config = await service.create_config(
        session, admin, CreateConfig(key='console.notice_enabled', name='公告', value='true')
    )
    with pytest.raises(AppError) as duplicate:
        await service.create_config(
            session, admin, CreateConfig(key='console.notice_enabled', name='重复', value='true')
        )
    assert duplicate.value.status_code == 409
    with pytest.raises((AppError, ValidationError)):
        await service.update_config(session, admin, config.id, PatchConfig(expected_version=1, value='private-key'))
    assert (await service.get_config(session, admin, config.id)).value == 'true'


async def test_permissions_enforced_for_every_operations_area(session: AsyncSession, admin: Principal) -> None:
    from zhiheng.modules.operations.public import CreateNotice, OperationsService

    service = OperationsService()
    member = Principal(uuid4(), 'member', False)
    for operation in (
        service.list_dictionaries(session, member),
        service.list_configs(session, member),
        service.list_notices(session, member),
        service.list_logs(session, member),
        service.create_notice(session, member, CreateNotice(title='越权', content='禁止')),
    ):
        with pytest.raises(AppError) as denied:
            await operation
        assert denied.value.status_code == 403
    reader = Principal(uuid4(), 'reader', False, frozenset({'system:notices:read'}))
    assert (await service.list_notices(session, reader)).total == 0
    with pytest.raises(AppError) as denied:
        await service.create_notice(session, reader, CreateNotice(title='越权', content='禁止'))
    assert denied.value.status_code == 403


async def test_logs_filter_page_and_sanitize_external_messages(session: AsyncSession, admin: Principal) -> None:
    from zhiheng.modules.operations.orm import OperationLog
    from zhiheng.modules.operations.public import CreateNotice, OperationsService

    service = OperationsService()
    await service.record(
        session,
        principal=None,
        action='login',
        kind='login',
        username='member',
        outcome='failed',
        message='登录验证失败',
    )
    await service.record(session, principal=admin, action='login', kind='login', message='登录成功')
    await service.record(
        session,
        principal=admin,
        action='notice.update',
        resource_type='notice',
        message='password=hunter2 token=private-token https://user:pass@example.com?key=secret',
    )
    await service.create_notice(session, admin, CreateNotice(title='创建', content='不应记录正文 private-body'))
    failed = await service.list_logs(
        session, admin, kind='login', outcome='failed', search='member', page=1, page_size=1
    )
    assert failed.total == 1 and failed.items[0].message == '登录验证失败'
    assert failed.items[0].username == 'member'
    operations = await service.list_logs(session, admin, kind='operation', page=1, page_size=1)
    assert operations.total == 2 and len(operations.items) == 1
    stored = '\n'.join(row.message for row in await session.scalars(select(OperationLog)))
    for secret in ('hunter2', 'private-token', 'user:pass', 'private-body'):
        assert secret not in stored
    assert 'password' not in failed.model_dump_json()


async def test_operation_logs_share_transaction_and_rollback_with_write(
    session: AsyncSession, admin: Principal
) -> None:
    from zhiheng.modules.operations.public import CreateNotice, OperationsService

    service = OperationsService()
    await service.create_notice(session, admin, CreateNotice(title='事务', content='正文'))
    await session.rollback()
    assert (await service.list_notices(session, admin)).total == 0
    assert (await service.list_logs(session, admin)).total == 0


@pytest_asyncio.fixture
async def api(session: AsyncSession, admin: Principal) -> AsyncIterator[Api]:
    from types import SimpleNamespace

    from zhiheng.api.common.dependencies import get_principal, get_session
    from zhiheng.api.common.errors import install_error_handlers
    from zhiheng.api.console.admin_operations import router
    from zhiheng.modules.operations.public import OperationsService

    app = FastAPI()
    app.state.container = SimpleNamespace(operations=OperationsService())
    app.state.principal = admin
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as value:
            yield value

    async def request_principal() -> Principal:
        if app.state.principal is None:
            raise AppError('unauthorized', '请先登录', 401)
        return app.state.principal

    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[get_principal] = request_principal
    app.include_router(router)
    install_error_handlers(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield app, client


async def test_http_dictionary_crud_and_expected_version_required(api: Api) -> None:
    _app, client = api
    result = await client.post('/api/admin/dictionaries', json={'code': 'status', 'name': '状态'})
    assert result.status_code == 201, result.text
    row = result.json()
    detail = await client.get(f'/api/admin/dictionaries/{row["id"]}')
    assert detail.status_code == 200 and detail.json()['version'] == 1
    item = await client.post(f'/api/admin/dictionaries/{row["id"]}/items', json={'label': '启用', 'value': 'enabled'})
    assert item.status_code == 201, item.text
    item_id = item.json()['id']
    assert (await client.get(f'/api/admin/dictionaries/{row["id"]}/items')).json()['total'] == 1
    assert (await client.delete(f'/api/admin/dictionaries/{row["id"]}')).status_code == 422
    assert (
        await client.delete(f'/api/admin/dictionaries/{row["id"]}', params={'expected_version': 1})
    ).status_code == 409
    assert (await client.patch(f'/api/admin/dictionaries/{row["id"]}', json={'name': '缺版本'})).status_code == 422
    changed = await client.patch(
        f'/api/admin/dictionaries/{row["id"]}', json={'expected_version': 1, 'name': '状态类型'}
    )
    assert changed.status_code == 200 and changed.json()['version'] == 2
    assert (
        await client.patch(f'/api/admin/dictionaries/{row["id"]}', json={'expected_version': 1, 'name': '旧值'})
    ).status_code == 409
    assert (
        await client.delete(f'/api/admin/dictionaries/{row["id"]}/items/{item_id}', params={'expected_version': 1})
    ).status_code == 204
    assert (
        await client.delete(f'/api/admin/dictionaries/{row["id"]}', params={'expected_version': 2})
    ).status_code == 204


async def test_http_published_requires_login_and_obeys_switch_without_admin_permission(api: Api) -> None:
    app, client = api
    notice = await client.post(
        '/api/admin/notices', json={'title': '系统公告', 'content': '<b>纯文本</b>', 'status': 'published'}
    )
    assert notice.status_code == 201, notice.text
    config = await client.post(
        '/api/admin/configs', json={'key': 'console.notice_enabled', 'name': '公告开关', 'value': 'false'}
    )
    assert config.status_code == 201, config.text
    app.state.principal = Principal(uuid4(), 'member', False)
    assert (await client.get('/api/admin/notices/published')).json()['total'] == 0
    for path in ('/notices', '/configs', '/dictionaries', '/logs'):
        assert (await client.get('/api/admin' + path)).status_code == 403
    app.state.principal = Principal(uuid4(), 'admin', True)
    updated = await client.patch(
        f'/api/admin/configs/{config.json()["id"]}', json={'expected_version': 1, 'value': 'true'}
    )
    assert updated.status_code == 200, updated.text
    app.state.principal = Principal(uuid4(), 'member', False)
    published = (await client.get('/api/admin/notices/published')).json()
    assert published['items'][0]['content'] == '<b>纯文本</b>'
    assert published['items'][0]['content_format'] == 'plain_text'
    app.state.principal = None
    assert (await client.get('/api/admin/notices/published')).status_code == 401


async def test_http_logs_readonly_filters_and_body_extra_rejection(api: Api) -> None:
    _app, client = api
    assert (await client.post('/api/admin/notices', json={'title': '测试', 'content': '不记录正文'})).status_code == 201
    page = await client.get(
        '/api/admin/logs', params={'kind': 'operation', 'outcome': 'success', 'search': 'notice', 'page_size': 1}
    )
    assert page.status_code == 200 and page.json()['total'] == 1
    assert page.json()['items'][0]['action'] == 'notice.create'
    assert '不记录正文' not in page.text
    assert (await client.post('/api/admin/logs', json={})).status_code == 405
    assert (await client.get('/api/admin/logs', params={'kind': 'unknown'})).status_code == 422
    assert (await client.get('/api/admin/logs', params={'page_size': 101})).status_code == 422
    assert (
        await client.post('/api/admin/configs', json={'key': 'console.secret', 'name': '秘密', 'value': 'private'})
    ).status_code == 422
    assert (
        await client.post('/api/admin/notices', json={'title': '测试', 'content': '正文', 'request_body': 'private'})
    ).status_code == 422


async def test_cas_rejects_stale_snapshot_from_a_separate_session(session: AsyncSession, admin: Principal) -> None:
    from zhiheng.modules.operations.public import CreateNotice, OperationsService, PatchNotice

    service = OperationsService()
    original = await service.create_notice(session, admin, CreateNotice(title='原公告', content='正文'))
    await session.commit()
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    async with sessions() as reader:
        snapshot = await service.get_notice(reader, admin, original.id)
        await reader.rollback()
        async with sessions.begin() as writer:
            fresh = await service.update_notice(
                writer, admin, original.id, PatchNotice(expected_version=1, title='其他管理员已改')
            )
            assert fresh.version == 2
        with pytest.raises(AppError) as stale:
            await service.update_notice(
                reader, admin, original.id, PatchNotice(expected_version=snapshot.version, title='过期覆盖')
            )
        assert stale.value.status_code == 409
        await reader.rollback()
        assert (await service.get_notice(reader, admin, original.id)).title == '其他管理员已改'
        logs = await service.list_logs(reader, admin)
        assert logs.total == 2


async def test_http_notice_detail_filter_update_and_delete_cas(api: Api) -> None:
    _app, client = api
    response = await client.post(
        '/api/admin/notices', json={'title': '发布计划', 'content': '草稿正文', 'kind': 'announcement'}
    )
    assert response.status_code == 201
    row = response.json()
    assert (await client.get(f'/api/admin/notices/{row["id"]}')).json()['content_format'] == 'plain_text'
    assert (await client.get('/api/admin/notices', params={'status': 'draft', 'kind': 'announcement'})).json()[
        'total'
    ] == 1
    assert (await client.get('/api/admin/notices', params={'status': 'published'})).json()['total'] == 0
    changed = await client.patch(
        f'/api/admin/notices/{row["id"]}', json={'expected_version': 1, 'status': 'published', 'content': '已发布正文'}
    )
    assert changed.status_code == 200 and changed.json()['version'] == 2
    assert (await client.delete(f'/api/admin/notices/{row["id"]}', params={'expected_version': 1})).status_code == 409
    assert (await client.delete(f'/api/admin/notices/{row["id"]}', params={'expected_version': 2})).status_code == 204
    assert (await client.get(f'/api/admin/notices/{row["id"]}')).status_code == 404


def test_patch_contracts_require_version_nonnull_and_plain_text_limits() -> None:
    from zhiheng.modules.operations.public import CreateNotice, PatchDictionary, PatchNotice

    for body in (
        {'name': '缺版本'},
        {'expected_version': 0},
        {'expected_version': True},
        {'expected_version': 1, 'name': None},
    ):
        with pytest.raises(ValidationError):
            PatchDictionary.model_validate(body)
    with pytest.raises(ValidationError):
        CreateNotice(title=' ', content='正文')
    with pytest.raises(ValidationError):
        CreateNotice(title='标题', content='含\x00字符')
    with pytest.raises(ValidationError):
        PatchNotice(expected_version=1, content=None)
