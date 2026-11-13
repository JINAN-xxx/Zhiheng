import asyncio
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.core.billing_support import enable_free_billing
from zhiheng.modules.billing.orm import Invocation, UsageAccount
from zhiheng.modules.developer.contracts import KeyCreate
from zhiheng.modules.models.orm import ModelConnection, ModelDefinition
from zhiheng.shared.errors import AppError


async def _binding_read(container: Any, session: Any, operation: str, model: UUID) -> None:
    if operation == 'resolve':
        await container.models.resolve(session, model)
    elif operation == 'resolve_many':
        await container.models.resolve_many(session, [model])
    else:
        await container.models.lock_many(session, [model])


async def _blocked_by(observer: Any, holder: int, contender: int, task: asyncio.Task[Any]) -> bool:
    for _ in range(200):
        blocked = await observer.scalar(
            text('SELECT CAST(:holder AS int) = ANY(pg_blocking_pids(CAST(:contender AS int)))'),
            {'holder': holder, 'contender': contender},
        )
        if blocked or task.done():
            return bool(blocked)
        await asyncio.sleep(0.01)
    raise AssertionError('Concurrent operation neither completed nor reached the expected PostgreSQL lock wait')


async def _shared_connection_models(admin: httpx.AsyncClient, app: FastAPI) -> tuple[UUID, list[UUID], UUID, Any]:
    response = await admin.post(
        '/api/models/connections',
        json={'name': 'Shared binding', 'base_url': 'https://provider.invalid/v1', 'api_key': 'unused-test-key'},
    )
    assert response.status_code == 201, response.text
    connection_id = UUID(response.json()['id'])
    model_ids = [UUID(int=value) for value in (1, 2, 3)]
    container = app.state.container
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    async with container.database.sessions.begin() as session:
        principal = await container.identity.principal_for_user(session, owner_id)
        session.add_all(
            ModelDefinition(
                id=identifier,
                name=model_type,
                model_type=model_type,
                connection_id=connection_id,
                upstream_model=model_type,
                max_output_tokens=64,
            )
            for identifier, model_type in zip(model_ids, ('embedding', 'rerank', 'chat'), strict=True)
        )
    response = await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'Lock regression'})
    assert response.status_code == 201, response.text
    return connection_id, model_ids, UUID(response.json()['id']), principal


async def test_key_creation_locks_all_models_before_shared_connection(admin: httpx.AsyncClient, app: FastAPI) -> None:
    container = app.state.container
    connection_id, model_ids, application_id, principal = await _shared_connection_models(admin, app)
    started: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def create_key() -> Any:
        async with container.database.sessions.begin() as session:
            started.set_result(await session.scalar(text('SELECT pg_backend_pid()')))
            return await container.keys.create(
                session, principal, application_id, KeyCreate(name='All models', model_ids=list(reversed(model_ids)))
            )

    task = None
    try:
        async with container.database.sessions.begin() as holder:
            holder_pid = await holder.scalar(text('SELECT pg_backend_pid()'))
            await holder.execute(select(ModelDefinition).where(ModelDefinition.id == model_ids[1]).with_for_update())
            task = asyncio.create_task(create_key())
            contender_pid = await asyncio.wait_for(started, timeout=5)
            assert await _blocked_by(holder, holder_pid, contender_pid, task)
            try:
                async with holder.begin_nested():
                    await holder.execute(
                        select(ModelConnection).where(ModelConnection.id == connection_id).with_for_update(nowait=True)
                    )
            except DBAPIError as error:
                assert getattr(error.orig, 'sqlstate', None) == '55P03'
                pytest.fail('Key creation locked the shared connection before obtaining every model lock')
        issued = await asyncio.wait_for(task, timeout=5)
        assert set(issued.model_ids) == set(model_ids)
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_key_creation_and_single_model_admission_do_not_form_lock_cycle(  # noqa: PLR0915 - keep the complete deterministic interleaving and cleanup together
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = app.state.container
    _, model_ids, application_id, principal = await _shared_connection_models(admin, app)
    connection_locked, continue_key, admission_read, release_admission = (asyncio.Event() for _ in range(4))
    key_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    admission_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def create_key() -> Any:
        async with container.database.sessions.begin() as session:
            key_pid.set_result(await session.scalar(text('SELECT pg_backend_pid()')))

            def pause_after_connection(method: Any) -> Any:
                async def execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
                    result = await method(statement, *args, **kwargs)
                    if (
                        not connection_locked.is_set()
                        and getattr(statement, '_for_update_arg', None) is not None
                        and any(table.name == 'model_connections' for table in statement.get_final_froms())
                    ):
                        connection_locked.set()
                        await continue_key.wait()
                    return result

                return execute

            monkeypatch.setattr(session, 'scalar', pause_after_connection(session.scalar))
            monkeypatch.setattr(session, 'execute', pause_after_connection(session.execute))
            return await container.keys.create(
                session, principal, application_id, KeyCreate(name='Mixed models', model_ids=model_ids)
            )

    async def admit_one() -> None:
        async with container.database.sessions.begin() as session:
            admission_pid.set_result(await session.scalar(text('SELECT pg_backend_pid()')))
            await container.models.resolve(session, model_ids[1])
            admission_read.set()
            await release_admission.wait()

    key_task = asyncio.create_task(create_key())
    admission_task = None
    try:
        await asyncio.wait_for(connection_locked.wait(), timeout=5)
        creator_pid = await key_pid
        admission_task = asyncio.create_task(admit_one())
        contender_pid = await asyncio.wait_for(admission_pid, timeout=5)
        async with container.database.sessions() as observer:
            # A completed reader retains its transaction until explicitly released.
            for _ in range(200):
                blocked = await observer.scalar(
                    text('SELECT CAST(:holder AS int) = ANY(pg_blocking_pids(CAST(:contender AS int)))'),
                    {'holder': creator_pid, 'contender': contender_pid},
                )
                if blocked or admission_read.is_set() or admission_task.done():
                    break
                await asyncio.sleep(0.01)
            assert blocked or admission_read.is_set(), 'Single-model admission did not reach the interleaving point'
            continue_key.set()
            if blocked:
                cycle = await _blocked_by(observer, contender_pid, creator_pid, key_task)
                assert not cycle, (
                    'Key creation and single-model admission formed a PostgreSQL model/connection lock cycle'
                )
        issued = await asyncio.wait_for(key_task, timeout=5)
        assert set(issued.model_ids) == set(model_ids)
        await asyncio.wait_for(admission_read.wait(), timeout=5)
        release_admission.set()
        await asyncio.wait_for(admission_task, timeout=5)
    finally:
        continue_key.set()
        release_admission.set()
        key_task.cancel()
        if admission_task is not None:
            admission_task.cancel()
        await asyncio.gather(
            key_task, *([admission_task] if admission_task is not None else []), return_exceptions=True
        )


@pytest.mark.parametrize('holder_operation', ['resolve', 'resolve_many', 'lock_many'])
@pytest.mark.parametrize('reader_operation', ['resolve', 'resolve_many', 'lock_many'])
async def test_binding_readers_do_not_block_each_other(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, holder_operation: str, reader_operation: str
) -> None:
    container = app.state.container
    model = UUID(model_id)
    started: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def read() -> None:
        async with container.database.sessions.begin() as session:
            started.set_result(await session.scalar(text('SELECT pg_backend_pid()')))
            await _binding_read(container, session, reader_operation, model)

    task = None
    try:
        async with container.database.sessions.begin() as holder:
            await _binding_read(container, holder, holder_operation, model)
            holder_pid = await holder.scalar(text('SELECT pg_backend_pid()'))
            task = asyncio.create_task(read())
            reader_pid = await asyncio.wait_for(started, timeout=5)
            async with container.database.sessions() as observer:
                assert not await _blocked_by(observer, holder_pid, reader_pid, task), (
                    f'{holder_operation} blocked concurrent {reader_operation} metadata validation'
                )
            await asyncio.wait_for(task, timeout=5)
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('outcome', ['known', 'unknown', 'rejected'])
async def test_binding_validation_allows_concurrent_usage_settlement(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: object, batch: bool, outcome: str
) -> None:
    await enable_free_billing(admin)
    container = app.state.container
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    model, first, second = UUID(model_id), uuid4(), uuid4()
    response = await admin.post(
        f'/api/models/{model_id}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.02'},
    )
    assert response.status_code == 201, response.text
    async with container.database.sessions.begin() as session:
        rate = await container.models.latest_rate(session, model)
        await container.usage.reserve(
            session,
            user_id=owner,
            run_id=first,
            model_id=model,
            source='connection_test',
            tokens=100,
            token_limit=100000,
            rate=rate,
            estimated_input_tokens=100,
            max_output_tokens=0,
        )

    async def settle() -> None:
        async with container.database.sessions.begin() as session:
            await container.usage.settle(
                session,
                first,
                input_tokens=10 if outcome == 'known' else None,
                output_tokens=5 if outcome == 'known' else None,
                rejected_before_execution=outcome == 'rejected',
            )

    task = None
    try:
        async with container.database.sessions.begin() as admission:
            if batch:
                await container.models.resolve_many(admission, [model])
            else:
                await container.models.resolve(admission, model)
            # Another request's metadata validation must allow a completed call
            # to settle before this admission reserves the shared account.
            task = asyncio.create_task(settle())
            await asyncio.wait_for(asyncio.shield(task), timeout=2)
            await container.usage.reserve(
                admission,
                user_id=owner,
                run_id=second,
                model_id=model,
                source='connection_test',
                tokens=100,
                token_limit=100000,
                rate=rate,
                estimated_input_tokens=100,
                max_output_tokens=0,
            )
        async with container.database.sessions.begin() as session:
            invocation = await session.scalar(select(Invocation).where(Invocation.run_id == first))
            account = await session.get(UsageAccount, owner)
            assert invocation.status == ('unknown' if outcome == 'unknown' else 'known')
            assert invocation.reserved_cost == Decimal('2')
            assert invocation.billable_cost == (
                None if outcome == 'unknown' else Decimal('0.2') if outcome == 'known' else Decimal('0')
            )
            assert account.used_tokens == (15 if outcome == 'known' else 0)
            assert account.reserved_tokens == (200 if outcome == 'unknown' else 100)
            again = await container.usage.settle(session, first, input_tokens=99, output_tokens=99)
            assert again.status == invocation.status
        assert provider.requests == []
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('operation', ['resolve', 'resolve_many', 'lock_many'])
@pytest.mark.parametrize('resource', ['model', 'connection'])
@pytest.mark.parametrize('delete', [False, True])
async def test_binding_validation_still_excludes_model_configuration_changes(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, operation: str, resource: str, delete: bool
) -> None:
    container = app.state.container
    model = UUID(model_id)
    mutation = None
    try:
        async with container.database.sessions.begin() as admission:
            await _binding_read(container, admission, operation, model)
            definition = await admission.get(ModelDefinition, model)
            endpoint = (
                f'/api/models/connections/{definition.connection_id}'
                if resource == 'connection'
                else f'/api/models/{model_id}'
            )
            binding_pid = await admission.scalar(text('SELECT pg_backend_pid()'))
            request = (
                admin.delete(endpoint, headers={'Content-Type': 'application/json'})
                if delete
                else admin.patch(endpoint, json={'enabled': False})
            )
            mutation = asyncio.create_task(request)
            async with container.database.sessions() as observer:
                for _ in range(100):
                    blocked = await observer.scalar(
                        # Lock state stays current when the request opens its
                        # connection after this observer's statistics snapshot.
                        text(
                            'SELECT EXISTS (SELECT 1 FROM pg_locks '
                            'WHERE NOT granted AND :pid = ANY(pg_blocking_pids(pid)))'
                        ),
                        {'pid': binding_pid},
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
                assert blocked and not mutation.done()
        response = await asyncio.wait_for(mutation, timeout=2)
        assert response.status_code == (409 if resource == 'connection' and delete else 200), response.text
        async with container.database.sessions() as session:
            if resource == 'connection' and delete:
                # The write lock is acquired before the service checks model references.
                await container.models.resolve(session, model)
            else:
                with pytest.raises(AppError, match='模型不存在或不可用'):
                    await container.models.resolve(session, model)
    finally:
        if mutation is not None:
            mutation.cancel()
            await asyncio.gather(mutation, return_exceptions=True)
