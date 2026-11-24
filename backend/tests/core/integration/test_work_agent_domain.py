import asyncio
import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.core.permission_support import account, grants
from zhiheng.modules.files.public import FileInfo
from zhiheng.modules.work.orm import WorkTask
from zhiheng.modules.work.public import (
    AgentTurn,
    CreateAgentTask,
    TaskDetail,
    UpdateAgentTask,
    WorkAgentStore,
    WorkbenchWork,
    WorkPlanItem,
)
from zhiheng.runtime.contracts import ClaimedRun, RunUsage
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


async def owned_task(app: FastAPI, goal: str = '分析资料') -> tuple[WorkAgentStore, Principal, TaskDetail]:
    container = app.state.container
    store = WorkAgentStore(container.work)
    async with container.database.sessions.begin() as session:
        users = await container.identity.directory(session, search='admin')
        principal = await container.identity.principal_for_user(session, users[0].id)
        body = CreateAgentTask(goal=goal, model_id=uuid4(), client_request_id=uuid4())
        task = await store.create(session, principal, body, 'create-fingerprint')
    return store, principal, task


async def managed_file(app: FastAPI, principal: Principal, content: bytes, filename: str = 'result.txt') -> FileInfo:
    container = app.state.container
    key = await container.files.storage.put(content)
    async with container.database.sessions.begin() as session:
        return await container.files.create(
            session, principal, filename=filename, content=content, storage_key=key, purpose='work_artifact'
        )


async def admitted_run(
    app: FastAPI, store: WorkAgentStore, principal: Principal, task: TaskDetail, *, claim: bool = True
) -> tuple[UUID, ClaimedRun | None]:
    container = app.state.container
    body = AgentTurn(client_request_id=task.create_client_request_id, expected_version=task.version, message=task.goal)
    async with container.database.sessions.begin() as session:
        run = await container.runs.create(
            session,
            owner_id=principal.id,
            resource_type='work_agent',
            resource_id=task.id,
            request_id=body.client_request_id,
            fingerprint='turn-fingerprint',
            payload={},
        )
        await store.admit(session, principal, task.id, body, run, 'turn-fingerprint')
        claimed = await container.runs.claim(session, run.id, 60) if claim else None
    return run.id, claimed


async def test_draft_create_is_idempotent_and_even_admin_cannot_read_another_owner(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app, '')
    assert task.mode == 'agent' and task.goal == '' and task.messages == []
    async with container.database.sessions.begin() as session:
        body = CreateAgentTask(model_id=task.model_id, client_request_id=task.create_client_request_id)
        repeated = await store.create(session, principal, body, 'create-fingerprint')
        assert repeated.id == task.id and repeated.version == 1
    with pytest.raises(AppError) as changed:
        async with container.database.sessions.begin() as session:
            await store.create(session, principal, body, 'different-fingerprint')
    assert changed.value.code == 'idempotency_conflict'
    with pytest.raises(AppError) as denied:
        async with container.database.sessions() as session:
            await store.detail(session, Principal(uuid4(), 'other-admin', True), task.id)
    assert denied.value.status_code == 404


async def test_same_name_input_versions_preserve_both_managed_objects(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    first = await managed_file(app, principal, b'amount\n1\n', 'amount.csv')
    second = await managed_file(app, principal, b'amount\n2\n', 'amount.csv')
    async with container.database.sessions.begin() as session:
        a = await store.attach_input(session, principal, task.id, first)
        b = await store.attach_input(session, principal, task.id, second)
        repeated = await store.attach_input(session, principal, task.id, first)
        assert (a.version, b.version, repeated.version) == (1, 2, 1)
        current = await store.detail(session, principal, task.id)
        assert current.version == 3 and len(current.inputs) == 2
        assert current.inputs[0].acl_version == first.acl_version
    assert (await container.file_queries.read(principal, first.id))[1] == b'amount\n1\n'


async def test_pending_cancel_projects_without_a_live_attempt_and_reuses_initial_message(
    app: FastAPI, admin: AsyncClient
) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, _ = await admitted_run(app, store, principal, task, claim=False)
    progress_path = f'/api/work/tasks/{task.id}/runs/{run_id}/progress'
    progress = await admin.get(progress_path)
    assert progress.status_code == 200
    assert progress.json() == {'execution_state': 'queued', 'steps': [], 'artifacts': [], 'plan': []}
    async with container.database.sessions.begin() as session:
        snapshot, release = await container.runs.cancel(session, run_id, principal.id)
        assert release and snapshot.attempt_id is None
        await store.project_snapshot(session, snapshot)
    async with container.database.sessions() as session:
        current = await store.detail(session, principal, task.id)
        assert current.current_run_id is None and current.execution_state == 'cancelled'
        assert [message.role for message in current.messages] == ['user', 'assistant']
        assert current.messages[-1].status == 'cancelled'
    finished = await admin.get(progress_path)
    assert finished.status_code == 200 and finished.json() is None


async def test_work_routes_share_one_database_connection(
    app: FastAPI, admin: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, principal, task = await owned_task(app)
    run_id, _ = await admitted_run(app, store, principal, task, claim=False)
    _, _, draft = await owned_task(app, '')
    database = app.state.container.database
    engine = create_async_engine(database.engine.url, pool_size=1, max_overflow=0, pool_timeout=2)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(database, 'engine', engine)
            patch.setattr(database, 'sessions', async_sessionmaker(engine, expire_on_commit=False))
            run_path = f'/api/work/tasks/{task.id}/runs/{run_id}'
            snapshots, stopped, deleted = await asyncio.gather(
                asyncio.gather(*(admin.get(run_path) for _ in range(4))),
                admin.post(run_path + '/cancel', json={}),
                admin.delete(
                    f'/api/work/tasks/{draft.id}',
                    params={'expected_version': draft.version},
                    headers={'Content-Type': 'application/json'},
                ),
            )
        assert all(response.status_code == 200 for response in snapshots)
        assert stopped.status_code == 200 and stopped.json()['status'] == 'cancelled'
        assert deleted.status_code == 204
    finally:
        await engine.dispose()


async def test_work_owner_can_stop_after_losing_use_permission(app: FastAPI, admin: AsyncClient, model_id: str) -> None:
    async with account(admin, app, *grants('platform:work', 'use', 'create')) as (owner, role, _):
        response = await owner.post(
            '/api/work/tasks', json={'goal': '处理资料', 'model_id': model_id, 'client_request_id': str(uuid4())}
        )
        assert response.status_code == 201, response.text
        task = response.json()
        path = f'/api/work/tasks/{task["id"]}/runs'
        body = {
            'message': task['goal'],
            'expected_version': task['version'],
            'client_request_id': task['create_client_request_id'],
        }
        admitted = await owner.post(path, json=body)
        assert admitted.status_code == 202, admitted.text
        run_path = path + '/' + admitted.json()['id']
        role_update = await admin.patch(
            f'/api/admin/roles/{role["id"]}',
            json={'expected_version': role['version'], 'permissions': sorted(grants('platform:work'))},
        )
        assert role_update.status_code == 200, role_update.text
        async with account(admin, app, *grants('platform:work')) as (other, _, _):
            assert (await other.post(run_path + '/cancel', json={})).status_code == 404
        stopped = await owner.post(run_path + '/cancel', json={})
        assert stopped.status_code == 200 and stopped.json()['status'] == 'cancelled'
        assert (await owner.post(path, json={**body, 'client_request_id': str(uuid4())})).status_code == 403
        assert (await owner.get(run_path)).json()['status'] == 'cancelled'


async def test_step_facts_reject_stale_attempts_and_expired_leases(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    with pytest.raises(AppError) as stale:
        async with container.database.sessions.begin() as session:
            await store.record_step(
                session,
                task.id,
                run_id,
                uuid4(),
                action_id='wrong-attempt',
                kind='tool',
                name='read_sheet',
                status='running',
            )
    assert stale.value.code == 'work_execution_lost'
    async with container.database.sessions.begin() as session:
        await container.runs.project(session, run_id, claimed.attempt_id, '已核对资料')
        await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='unfinished',
            kind='tool',
            name='run_python',
            status='running',
        )
        snapshot = await container.runs.finish(
            session,
            run_id,
            claimed.attempt_id,
            status='interrupted',
            content='已核对资料',
            usage=RunUsage(input_tokens=0, output_tokens=0, status='known'),
            result={'execution_state': 'paused'},
        )
        await store.project_snapshot(session, snapshot)
    async with container.database.sessions() as session:
        current = await store.detail(session, principal, task.id)
        assert current.execution_state == 'paused' and current.steps[0].status == 'unknown'
    with pytest.raises(AppError) as terminal:
        async with container.database.sessions.begin() as session:
            await store.record_step(
                session,
                task.id,
                run_id,
                claimed.attempt_id,
                action_id='after-end',
                kind='tool',
                name='run_python',
                status='running',
            )
    assert terminal.value.code == 'work_execution_lost'


async def test_artifact_versions_are_immutable_and_equal_bytes_reuse_existing_version(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    first = await managed_file(app, principal, b'version one')
    second = await managed_file(app, principal, b'version two')
    async with container.database.sessions.begin() as session:
        a = await store.record_artifact(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            file=first,
            logical_path='outputs/result.txt',
            validation={'valid': True},
        )
        same = await store.record_artifact(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            file=first,
            logical_path='outputs/result.txt',
            validation={'valid': True},
        )
        normalized = await store.record_artifact(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            file=first,
            logical_path='outputs//result.txt',
            validation={'valid': True},
        )
        b = await store.record_artifact(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            file=second,
            logical_path='outputs/result.txt',
            validation={'valid': True},
        )
        assert a.id == same.id == normalized.id and (a.version, b.version) == (1, 2)
        assert (await store.artifact(session, principal, task.id, a.id)).file_id == first.id
    assert (await container.file_queries.read(principal, first.id))[1] == b'version one'
    with pytest.raises(AppError) as traversal:
        async with container.database.sessions.begin() as session:
            await store.record_artifact(
                session,
                task.id,
                run_id,
                claimed.attempt_id,
                file=first,
                logical_path='outputs/../private.txt',
                validation={'valid': True},
            )
    assert traversal.value.code == 'invalid_work_artifact_path'


async def test_late_sandbox_id_is_retained_for_cleanup_after_cancellation(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    async with container.database.sessions.begin() as session:
        lease = await store.reserve_sandbox(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            image='office-runtime:test',
            expires_at=utcnow() + timedelta(minutes=10),
        )
        await session.execute(update(Run).where(Run.id == run_id).values(lease_until=utcnow() - timedelta(seconds=1)))
    with pytest.raises(AppError):
        async with container.database.sessions.begin() as session:
            await store.bind_sandbox(session, lease.id, claimed.attempt_id, 'owned-sandbox')
    async with container.database.sessions.begin() as session:
        await store.close_sandbox(
            session,
            lease.id,
            claimed.attempt_id,
            status='cleanup_pending',
            sandbox_id='owned-sandbox',
            error='temporary cleanup outage',
        )
        pending = await store.sandbox_leases(session)
        assert len(pending) == 1 and pending[0].sandbox_id == 'owned-sandbox'
    with pytest.raises(AppError):
        async with container.database.sessions.begin() as session:
            await store.close_sandbox(session, lease.id, uuid4(), sandbox_id='another-sandbox')


async def test_inputs_frozen_in_run_history_cannot_be_detached_but_drafts_can(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    used = await managed_file(app, principal, b'used input', 'used.txt')
    async with container.database.sessions.begin() as session:
        await store.attach_input(session, principal, task.id, used)
        await container.work_agent.bind_file(session, principal, task.id, used)
        task = await store.detail(session, principal, task.id)
        assert not await store.has_consumed_input(session, principal, task.id, used.id)
    run_id, _ = await admitted_run(app, store, principal, task, claim=False)
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(Run).where(Run.id == run_id).values(payload={'inputs': [{'file_id': str(used.id)}]})
        )
        snapshot, _ = await container.runs.cancel(session, run_id, principal.id)
        await store.project_snapshot(session, snapshot)
        task = await store.detail(session, principal, task.id)
        assert await store.has_consumed_input(session, principal, task.id, used.id)
        assert [(message.role, message.input_file_ids) for message in task.messages] == [
            ('user', [used.id]),
            ('assistant', []),
        ]
    with pytest.raises(AppError) as error:
        await container.work_agent.detach_input(principal, task.id, used.id, task.version)
    assert error.value.code == 'work_input_referenced'
    unused = await managed_file(app, principal, b'unused input', 'unused.txt')
    async with container.database.sessions.begin() as session:
        await store.attach_input(session, principal, task.id, unused)
        await container.work_agent.bind_file(session, principal, task.id, unused)
        task = await store.detail(session, principal, task.id)
        assert not await store.has_consumed_input(session, principal, task.id, unused.id)
        assert task.messages[0].input_file_ids == [used.id]
    current = await container.work_agent.detach_input(principal, task.id, unused.id, task.version)
    assert [item.file_id for item in current.inputs] == [used.id]
    async with container.database.sessions() as session:
        assert await container.files.references.list(session, principal, unused.id) == []
        assert [item.resource_id for item in await container.files.references.list(session, principal, used.id)] == [
            task.id
        ]


async def test_archive_and_restore_work_with_unavailable_model(app: FastAPI) -> None:
    container = app.state.container
    _, principal, task = await owned_task(app)
    archived = await container.work_agent.update(
        principal, task.id, UpdateAgentTask(expected_version=task.version, status='archived')
    )
    async with container.database.sessions() as session:
        recent = await container.work.list_tasks(
            session, principal, page=1, page_size=20, search='', status=None, include_archived=False
        )
        history = await container.work.list_tasks(
            session, principal, page=1, page_size=20, search='', status='archived', include_archived=False
        )
        assert recent.items == [] and [item.id for item in history.items] == [task.id]
    restored = await container.work_agent.update(
        principal, task.id, UpdateAgentTask(expected_version=archived.version, status='active')
    )
    assert restored.status == 'active'
    assert [item.content for item in restored.messages] == [task.goal]


async def test_empty_intermediate_snapshots_are_preserved_without_managed_empty_files(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    empty = {'path': 'working/empty.txt', 'empty': True, 'sha256': hashlib.sha256(b'').hexdigest()}
    async with container.database.sessions.begin() as session:
        await store.save_execution_memory(
            session, task.id, run_id, claimed.attempt_id, checkpoint_id='checkpoint-one', files=[empty]
        )
        assert await store.execution_memory(session, principal, task.id) == {
            'checkpoint_id': 'checkpoint-one',
            'files': [empty],
        }
    for invalid in ({**empty, 'sha256': 'a' * 64}, {**empty, 'file_id': str(uuid4())}):
        with pytest.raises(AppError) as error:
            async with container.database.sessions.begin() as session:
                await store.save_execution_memory(
                    session, task.id, run_id, claimed.attempt_id, checkpoint_id='checkpoint-two', files=[invalid]
                )
        assert error.value.code == 'invalid_work_snapshot'


async def test_control_characters_in_tool_facts_are_display_escaped_before_jsonb_storage(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    arguments = {'code': 'print("\\0")\x00'}
    async with container.database.sessions.begin() as session:
        step = await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='binary-output',
            kind='tool',
            name='execute',
            status='succeeded',
            details=arguments,
            result={'stdout': 'binary\x00output', 'stderr': '\ud800'},
        )
        assert step.result == {'stdout': 'binary\\u0000output', 'stderr': '\\ud800'}
        assert arguments['code'].endswith('\x00')
    async with container.database.sessions() as session:
        stored = await store.cached_step(session, task.id, 'binary-output')
        assert stored is not None and stored.result == step.result


async def test_plan_projection_is_jsonb_safe_without_changing_raw_graph_plan(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    raw = {'content': '检查\x00资料', 'status': 'in_progress'}
    async with container.database.sessions.begin() as session:
        await container.runs.project(session, run_id, claimed.attempt_id, '处理中')
        await store.project(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            content='处理中',
            status='running',
            usage={},
            plan=[WorkPlanItem.model_validate(raw)],
        )
    assert raw['content'] == '检查\x00资料'
    async with container.database.sessions() as session:
        current = await store.detail(session, principal, task.id)
        assert current.plan[0].content == '检查\\u0000资料'


async def test_deleted_task_checkpoints_are_removed_by_real_reconcile_after_grace_period(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    async with container.database.sessions.begin() as session:
        await store.save_execution_memory(
            session, task.id, run_id, claimed.attempt_id, checkpoint_id='saved-head', files=[]
        )
        snapshot = await container.runs.finish(
            session,
            run_id,
            claimed.attempt_id,
            status='completed',
            content='完成',
            usage=RunUsage(input_tokens=0, output_tokens=0, status='known'),
        )
        await store.project_snapshot(session, snapshot)
        current = await store.detail(session, principal, task.id)
        await store.work.remove(session, principal, task.id, current.version)
        assert await store.due_checkpoint_cleanup(session) == ()
        await session.execute(
            text("""INSERT INTO work_checkpoints.checkpoints
            (thread_id,checkpoint_ns,checkpoint_id,type,checkpoint,metadata)
            VALUES (:thread,'','saved-head','json','{}','{}')"""),
            {'thread': str(task.id)},
        )
        await session.execute(
            update(WorkTask)
            .where(WorkTask.id == task.id)
            .values(checkpoint_cleanup_after=utcnow() - timedelta(seconds=1))
        )
    await container.work_agent.reconcile()
    async with container.database.sessions() as session:
        assert (
            await session.scalar(
                text('SELECT count(*) FROM work_checkpoints.checkpoints WHERE thread_id=:id'), {'id': str(task.id)}
            )
            == 0
        )
        row = await session.get(WorkTask, task.id)
        assert row is not None and row.agent_checkpoint_id is None and row.workspace_files == []
        assert row.checkpoint_cleanup_after is None


async def test_terminal_usage_repair_updates_only_old_message_after_a_new_run_started(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    first_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    async with container.database.sessions.begin() as session:
        first = await container.runs.finish(
            session,
            first_id,
            claimed.attempt_id,
            status='completed',
            content='第一轮结果',
            usage=RunUsage(status='pending'),
            result={'execution_state': 'waiting_input'},
        )
        await store.project_snapshot(session, first)
        task = await store.detail(session, principal, task.id)
        next_turn = AgentTurn(client_request_id=uuid4(), expected_version=task.version, message='继续处理')
        second = await container.runs.create(
            session,
            owner_id=principal.id,
            resource_type='work_agent',
            resource_id=task.id,
            request_id=next_turn.client_request_id,
            fingerprint='second-turn',
            payload={},
        )
        await store.admit(session, principal, task.id, next_turn, second, 'second-turn')
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(Run)
            .where(Run.id == first_id)
            .values(input_tokens=9, output_tokens=2, usage_status='known', revision=Run.revision + 1)
        )
        repaired = await container.runs.snapshot(session, first_id, principal.id)
        await store.project_snapshot(session, repaired)
    async with container.database.sessions() as session:
        current = await store.detail(session, principal, task.id)
        assert current.current_run_id == second.id and current.execution_state == 'queued'
        historical = next(
            message for message in current.messages if message.run_id == first_id and message.role == 'assistant'
        )
        assert historical.content == '第一轮结果' and historical.status == 'completed'
        assert historical.usage == {'input_tokens': 9, 'output_tokens': 2, 'status': 'known'}
        assert current.messages[-1].run_id == second.id and current.messages[-1].usage is None


async def test_latest_workspace_snapshot_uses_sequence_and_ignores_cached_replay_timestamp(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    older = [{'path': 'working/state.txt', 'file_id': str(uuid4()), 'sha256': 'a' * 64}]
    newer = [{'path': 'working/state.txt', 'file_id': str(uuid4()), 'sha256': 'b' * 64}]
    async with container.database.sessions.begin() as session:
        first = await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='snapshot-one',
            kind='tool',
            name='run_python',
            status='succeeded',
            result={'_workspace_sequence': 1, '_workspace_files': older},
        )
        await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='snapshot-two',
            kind='tool',
            name='run_python',
            status='succeeded',
            result={'_workspace_sequence': 2, '_workspace_files': newer},
        )
        await session.execute(
            text('UPDATE work_steps SET updated_at=:at WHERE id=:id'),
            {'at': utcnow() + timedelta(days=1), 'id': first.id},
        )
        latest = await store.latest_workspace_files(session, task.id)
        assert latest is not None and latest.sequence == 2 and latest.files == newer
        assert latest.run_id == run_id and latest.failed_operations == []
        await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='snapshot-empty',
            kind='tool',
            name='run_python',
            status='failed',
            result={'_workspace_sequence': 3, '_workspace_files': []},
        )
        latest = await store.latest_workspace_files(session, task.id)
        assert latest is not None and latest.sequence == 3 and latest.files == []
        assert latest.failed_operations == ['execution']
        await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='snapshot-repaired',
            kind='tool',
            name='run_python',
            status='succeeded',
            result={'_workspace_sequence': 4, '_workspace_files': newer, '_workspace_failures': []},
        )
        latest = await store.latest_workspace_files(session, task.id)
        assert latest is not None and latest.sequence == 4 and latest.failed_operations == []
        await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='snapshot-failed-again',
            kind='tool',
            name='execute',
            status='failed',
            result={'_workspace_sequence': 5, '_workspace_files': newer, '_workspace_failures': ['execution']},
        )
        final = await container.runs.finish(
            session,
            run_id,
            claimed.attempt_id,
            status='interrupted',
            content='待继续',
            usage=RunUsage(input_tokens=0, output_tokens=0, status='known'),
        )
        await store.project_snapshot(session, final)
        current = await store.detail(session, principal, task.id)
        new_turn = AgentTurn(client_request_id=uuid4(), expected_version=current.version, message='新问题')
        new_run = await container.runs.create(
            session,
            owner_id=principal.id,
            resource_type='work_agent',
            resource_id=task.id,
            request_id=new_turn.client_request_id,
            fingerprint='new-logical-turn',
            payload={},
        )
        await store.admit(session, principal, task.id, new_turn, new_run, 'new-logical-turn')
        latest = await store.latest_workspace_files(session, task.id)
        assert latest is not None and latest.sequence == 5 and latest.run_id == run_id
        assert latest.files == newer and latest.failed_operations == []


async def test_workbench_uses_successful_actual_skill_loads_without_parent_provider_marker(app: FastAPI) -> None:
    container = app.state.container
    store, principal, task = await owned_task(app)
    run_id, claimed = await admitted_run(app, store, principal, task)
    assert claimed is not None
    async with container.database.sessions.begin() as session:
        await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='load-success',
            kind='tool',
            name='load_skill',
            status='succeeded',
            details={'arguments': {'name': 'document-summary'}},
        )
        await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='load-failed',
            kind='tool',
            name='load_skill',
            status='failed',
            details={'arguments': {'name': 'data-analysis'}},
        )
        await store.record_step(
            session,
            task.id,
            run_id,
            claimed.attempt_id,
            action_id='other-tool',
            kind='tool',
            name='inspect_file',
            status='succeeded',
            details={'arguments': {'name': 'data-analysis'}},
        )
        facts = await WorkbenchWork().skill_uses(session, principal)
        assert [slug for slug, _ in facts] == ['document-summary']
        assert await WorkbenchWork().skill_uses(session, Principal(uuid4(), 'foreign-admin', True)) == []
        await session.execute(update(WorkTask).where(WorkTask.id == task.id).values(deleted_at=utcnow()))
        assert await WorkbenchWork().skill_uses(session, principal) == []
