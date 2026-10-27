import asyncio
from contextvars import ContextVar
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from tests.core.graph_fixtures import evidence, graph_base, graph_role
from tests.core.integration.test_knowledge_milvus import real_vectors  # noqa: F401
from zhiheng.shared.errors import AppError

MODE = ContextVar('graph_lock_test_mode', default='')


@pytest.mark.parametrize('cancel_first', [False, True])
async def test_public_cancel_and_renew_lock_base_before_sources(
    admin, app, provider, real_vectors, monkeypatch, cancel_first
):
    container = app.state.container
    base = await graph_base(admin, app, provider)
    path = f'/api/knowledge/bases/{base["id"]}/graph'
    async with graph_role(container.settings) as (pg, url, _):
        container.graph_sdk.database_url = url
        response = await admin.post(path + '/build', json={'expected_version': 1, 'client_request_id': str(uuid4())})
        assert response.status_code == 202, response.text
        job = response.json()
        async with container.database.sessions.begin() as session:
            claim = await container.graph_store.claim(session, UUID(job['id']))
        pids, source_entries, base_calls = {}, [], []
        paused, release, contender_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        authorize, history, require = (
            container.graph.authorize,
            container.graph.authorize_history,
            container.knowledge.require,
        )

        async def observe_require(session, principal, base_id, **kwargs):
            mode = MODE.get()
            if mode in {'renew', 'cancel'} and mode not in pids:
                pids[mode] = await session.scalar(text('SELECT pg_backend_pid()'))
                if mode == ('renew' if cancel_first else 'cancel'):
                    contender_started.set()
            if mode == 'cancel':
                base_calls.append(bool(kwargs.get('lock')))
            return await require(session, principal, base_id, **kwargs)

        async def pause_authorize(session, principal, base_id, frozen):
            if not cancel_first and MODE.get() == 'renew' and not paused.is_set():
                paused.set()
                await release.wait()
            return await authorize(session, principal, base_id, frozen)

        async def observe_history(session, principal, base_id, frozen):
            if cancel_first and MODE.get() == 'cancel' and not paused.is_set():
                paused.set()
                await release.wait()
            result = await history(session, principal, base_id, frozen)
            if MODE.get() == 'cancel':
                source_entries.append(True)
            return result

        monkeypatch.setattr(container.knowledge, 'require', observe_require)
        monkeypatch.setattr(container.graph, 'authorize', pause_authorize)
        monkeypatch.setattr(container.graph, 'authorize_history', observe_history)

        async def execute(mode):
            token = MODE.set(mode)
            try:
                if mode == 'cancel':
                    response = await admin.post(path + '/jobs/' + job['id'] + '/cancel', json={})
                    return {'mode': mode, 'status': response.status_code, 'body': response.json()}
                async with container.database.sessions.begin() as session:
                    await container.graph_store.renew(session, claim)
                return {'mode': mode, 'status': 200}
            except BaseException as error:
                chain, cursor = [], error
                while cursor is not None:
                    chain.append(
                        {
                            'type': type(cursor).__name__,
                            'code': getattr(cursor, 'code', None),
                            'sqlstate': getattr(cursor, 'sqlstate', None),
                        }
                    )
                    cursor = getattr(cursor, 'orig', None) or cursor.__cause__
                return {'mode': mode, 'status': 'error', 'chain': chain}
            finally:
                MODE.reset(token)

        holder, contender = ('cancel', 'renew') if cancel_first else ('renew', 'cancel')
        tasks = [asyncio.create_task(execute(holder))]
        try:
            await asyncio.wait_for(paused.wait(), timeout=5)
            tasks.append(asyncio.create_task(execute(contender)))
            await asyncio.wait_for(contender_started.wait(), timeout=5)
            blocked = False
            for _ in range(150):
                blocked = await pg.fetchval(
                    'SELECT $1::int=ANY(pg_blocking_pids($2::int))', pids[holder], pids[contender]
                )
                if blocked or tasks[1].done():
                    break
                await asyncio.sleep(0.01)
            before = [
                dict(row)
                for row in await pg.fetch(
                    'SELECT pid,query,pg_blocking_pids(pid) AS blockers FROM pg_stat_activity WHERE pid=ANY($1::int[])',
                    list(pids.values()),
                )
            ]
            files_before_release = bool(source_entries)
            release.set()
            matrix = []
            for _ in range(100):
                matrix = [
                    dict(row)
                    for row in await pg.fetch(
                        'SELECT pid,pg_blocking_pids(pid) AS blockers FROM pg_stat_activity WHERE pid=ANY($1::int[])',
                        list(pids.values()),
                    )
                ]
                if all(task.done() for task in tasks) or all(row['blockers'] for row in matrix):
                    break
                await asyncio.sleep(0.01)
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=8)
            evidence(
                'cancel-lock-' + holder + '-first',
                {
                    'pids': pids,
                    'holder_first': holder,
                    'blocked': blocked,
                    'waiting_graph': before,
                    'after_release_graph': matrix,
                    'cancel_had_sources_before_release': files_before_release,
                    'base_lock_requests': base_calls,
                    'results': results,
                    'injection': 'Scheduling barriers only; original SQL locks and HTTP retained',
                },
            )
            assert blocked and not files_before_release
            assert base_calls[0] is True
            result = {row['mode']: row for row in results}
            assert result['cancel']['status'] == 200
            if cancel_first:
                assert result['renew']['status'] == 'error'
                assert any(row['code'] == 'graph_cancelled' for row in result['renew']['chain'])
            else:
                assert result['renew']['status'] == 200
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            async with container.database.sessions.begin() as session:
                await container.graph_store.finish(
                    session,
                    claim,
                    status='interrupted',
                    result=None,
                    error={'code': 'test_finished', 'message': 'No SDK was started'},
                    stopped=True,
                )
        # Terminal/historical cancellation remains valid without running lease or enabled models.
        response = await admin.patch('/api/models/' + str(claim.frozen.config.llm_model_id), json={'enabled': False})
        assert response.status_code == 200
        first = await admin.post(path + '/jobs/' + job['id'] + '/cancel', json={})
        second = await admin.post(path + '/jobs/' + job['id'] + '/cancel', json={})
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
