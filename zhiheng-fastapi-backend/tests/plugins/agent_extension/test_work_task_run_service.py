import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.agent_extension.domain.agent_knowledge import KnowledgePassage
from plugins.agent_extension.domain.work_task_context import build_work_history
from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.do.work_task_do import AgentWorkMessage, AgentWorkTask
from plugins.agent_extension.entity.vo.work_task_vo import WorkTaskRunRequestModel, WorkTaskUpdateModel
from plugins.agent_extension.service.work_task_run_service import WorkTaskRunService
from plugins.agent_extension.service.work_task_service import WorkTaskService
from plugins.ai.domain.model_runtime import ModelStreamEvent
from plugins.ai.service.platform_chat_service import ResolvedChatSkills
from tests.plugins.agent_extension.test_work_task_service import create_payload, work_db


class FakeModels:
    def __init__(self, *, slow=False, fail=False, eof=False):
        self.calls = []
        self.closed = False
        self.slow = slow
        self.fail = fail
        self.eof = eof

    def factory(self, db, runtime):
        return self

    async def model(self, db, model_id):
        return SimpleNamespace(model_code='fake', lifecycle_status='active', published_at=True, model_type='chat')

    async def stream_chat(self, model_id, messages, parameters):
        self.calls.append((model_id, messages, parameters))

        async def stream():
            try:
                yield ModelStreamEvent(event_type='content', delta='result')
                yield ModelStreamEvent(event_type='reasoning', delta='thought')
                if self.slow:
                    await asyncio.sleep(60)
                if self.fail:
                    raise RuntimeError('secret-upstream-token')
                if not self.eof:
                    yield ModelStreamEvent(event_type='done')
            finally:
                self.closed = True

        return stream()


def runner(factory, model):
    return WorkTaskRunService(
        factory, model_runtime=None, model_service_factory=model.factory, model_loader=model.model, poll_seconds=0.01
    )


@pytest.mark.asyncio
async def test_run_uses_frozen_context_persists_and_replays(work_db):
    factory, agent_id = work_db
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
    model = FakeModels()
    service = runner(factory, model)
    payload = WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='create', message=task.goal)
    stream = await service.start(task.task_id, payload, actor_user_id=10)
    events = [event async for event in stream]
    assert events[-1].data['status'] == 'completed'
    assert 'Frozen' in model.calls[0][1][0].content
    assert task.goal in model.calls[0][1][0].content
    replay = await service.start(task.task_id, payload, actor_user_id=10)
    assert [event async for event in replay][-1].data['status'] == 'completed'
    assert len(model.calls) == 1
    async with factory() as db:
        detail = await WorkTaskService(db).get_task(task.task_id, actor_user_id=10)
        assert len(detail.messages) == 2
        assert detail.messages[-1].content == 'result'
        assert detail.messages[-1].reasoning_content == 'thought'


@pytest.mark.asyncio
async def test_cross_service_cancel_stops_silent_upstream(work_db):
    factory, agent_id = work_db
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
    model = FakeModels(slow=True)
    service = runner(factory, model)
    stream = await service.start(
        task.task_id,
        WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='create', message=task.goal),
        actor_user_id=10,
    )
    await anext(stream)
    await anext(stream)
    await runner(factory, model).cancel(task.task_id, stream.run_id, actor_user_id=10)
    events = await asyncio.wait_for(_collect(stream), 2)
    assert events[-1].data['status'] == 'cancelled'
    assert model.closed


async def _collect(stream):
    return [event async for event in stream]


@pytest.mark.asyncio
@pytest.mark.parametrize(('mode', 'status'), [('fail', 'failed'), ('eof', 'interrupted')])
async def test_failed_and_truncated_streams_keep_partial_history(work_db, mode, status):
    factory, agent_id = work_db
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
    model = FakeModels(**{mode: True})
    service = runner(factory, model)
    payload = WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='create', message=task.goal)
    stream = await service.start(task.task_id, payload, actor_user_id=10)
    events = await _collect(stream)
    assert events[-1].data['status'] == status
    assert 'secret-upstream-token' not in str(events)
    async with factory() as db:
        detail = await WorkTaskService(db).get_task(task.task_id, actor_user_id=10)
        assert detail.messages[-1].content == 'result'
        assert detail.current_run_id is None
    model.fail = model.eof = False
    retried = await service.start(
        task.task_id, payload.model_copy(update={'client_message_id': 'retry'}), actor_user_id=10
    )
    assert (await _collect(retried))[-1].data['status'] == 'completed'
    assert len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('iterate', [False, True])
async def test_stream_close_before_or_during_iteration_persists_terminal(work_db, iterate):
    factory, agent_id = work_db
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
    model = FakeModels(slow=True)
    stream = await runner(factory, model).start(
        task.task_id,
        WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='create', message=task.goal),
        actor_user_id=10,
    )
    if iterate:
        await anext(stream)
        await anext(stream)
    await asyncio.wait_for(stream.aclose(), 2)
    async with factory() as db:
        detail = await WorkTaskService(db).get_task(task.task_id, actor_user_id=10)
        assert detail.messages[-1].run_status == 'interrupted'
        assert detail.current_run_id is None
    if iterate:
        assert model.closed


@pytest.mark.asyncio
async def test_concurrent_same_key_runs_once_and_live_run_blocks_edits(work_db):
    factory, agent_id = work_db
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
    model = FakeModels(slow=True)
    service = runner(factory, model)
    payload = WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='create', message=task.goal)
    streams = await asyncio.gather(
        service.start(task.task_id, payload, actor_user_id=10), service.start(task.task_id, payload, actor_user_id=10)
    )
    assert streams[0].run_id == streams[1].run_id
    original = next(stream for stream in streams if not stream.replay)
    await anext(original)
    await anext(original)
    assert len(model.calls) == 1
    for changed in (
        payload.model_copy(update={'message': 'different'}),
        payload.model_copy(update={'agent_id': str(uuid4())}),
        payload.model_copy(update={'client_message_id': 'other'}),
    ):
        with pytest.raises(AgentServiceError) as conflict:
            await service.start(task.task_id, changed, actor_user_id=10)
        assert conflict.value.status_code == 409
    async with factory() as db:
        for operation in (
            WorkTaskService(db).update_task(
                task.task_id, WorkTaskUpdateModel(expectedVersion=2, goal='Changed'), actor_user_id=10
            ),
            WorkTaskService(db).delete_task(task.task_id, actor_user_id=10),
        ):
            with pytest.raises(AgentServiceError):
                await operation
    with pytest.raises(AgentServiceError) as forbidden:
        await service.cancel(task.task_id, original.run_id, actor_user_id=11)
    assert forbidden.value.status_code == 404
    with pytest.raises(AgentServiceError):
        await service.cancel(task.task_id, str(uuid4()), actor_user_id=10)
    await original.aclose()


@pytest.mark.asyncio
async def test_unpublished_agent_cannot_execute_frozen_member(work_db):
    factory, agent_id = work_db
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
        agent = await db.get(AgentConfiguration, agent_id)
        agent.status = 'draft'
        await db.commit()
    model = FakeModels()
    with pytest.raises(AgentServiceError):
        await runner(factory, model).start(
            task.task_id,
            WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='create', message=task.goal),
            actor_user_id=10,
        )
    assert model.calls == []
    async with factory() as db:
        detail = await WorkTaskService(db).get_task(task.task_id, actor_user_id=10)
        assert not detail.members[0].available
        assert len(detail.messages) == 1


@pytest.mark.asyncio
async def test_skill_and_knowledge_use_agent_owner_and_audit_sources(work_db):
    factory, agent_id = work_db
    async with factory() as db:
        agent = await db.get(AgentConfiguration, agent_id)
        agent.published_config = {**agent.published_config, 'skillSlugs': ['research'], 'knowledgeBaseIds': ['kb-1']}
        await db.commit()
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=1, is_admin=True)

    class Skills:
        async def resolve(self, user_id, slugs):
            assert user_id == 10 and slugs == ['research']
            return ResolvedChatSkills(system_prompt='Skill instructions', snapshots=())

    class Knowledge:
        async def retrieve(self, *, identity, knowledge_base_ids, query):
            assert identity.owner_user_id == 10 and identity.agent_id == agent_id
            assert knowledge_base_ids == ('kb-1',)
            return [KnowledgePassage(content='Reference text', source_id='source-1', knowledge_base_id='kb-1')]

    model = FakeModels()
    service = runner(factory, model)
    service.skill_resolver = Skills()
    service.knowledge_provider = Knowledge()
    stream = await service.start(
        task.task_id,
        WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='create', message=task.goal),
        actor_user_id=1,
        is_admin=True,
    )
    assert (await _collect(stream))[-1].data['status'] == 'completed'
    assert 'Skill instructions' in model.calls[0][1][0].content
    async with factory() as db:
        message = (
            await db.execute(select(AgentWorkMessage).where(AgentWorkMessage.run_id == stream.run_id))
        ).scalar_one()
        assert message.audit_snapshot['userId'] == 1
        assert message.audit_snapshot['knowledgeSources'] == [{'sourceId': 'source-1', 'knowledgeBaseId': 'kb-1'}]


def test_history_keeps_latest_user_bounds_bytes_and_attributes_assistants():
    def message(sequence, role, content):
        return SimpleNamespace(
            sequence=sequence,
            message_id=str(sequence),
            role=role,
            content=content,
            agent_name_snapshot='Research',
            published_version=2,
        )

    latest = message(120, 'user', 'latest')
    history = build_work_history(
        [message(i, 'assistant' if i % 2 else 'user', 'old') for i in range(120)] + [latest], latest
    )
    assert len(history.messages) == 96
    assert history.messages[-1].content == 'latest'
    assert any('publishedVersion: 2' in item.content for item in history.messages)
    huge = message(122, 'user', '你' * 32000)
    with pytest.raises(AgentServiceError):
        build_work_history([huge], huge)
    failed_empty = message(121, 'assistant', '')
    assert len(build_work_history([failed_empty, latest], latest).messages) == 1


@pytest.mark.asyncio
async def test_lease_recovery_preserves_fresh_and_releases_expired_claim(work_db):
    factory, agent_id = work_db
    run_id = str(uuid4())
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
        row = await db.get(AgentWorkTask, task.task_id)
        row.current_run_id = run_id
        db.add(
            AgentWorkMessage(
                message_id=str(uuid4()),
                task_id=task.task_id,
                sequence=2,
                reply_to_message_id=task.messages[0].message_id,
                role='assistant',
                content='',
                agent_id=agent_id,
                agent_name_snapshot='Published',
                published_version=2,
                run_id=run_id,
                run_status='pending',
            )
        )
        await db.commit()
    service = runner(factory, FakeModels())
    assert await service.recover_expired() == 0
    async with factory() as db:
        message = (await db.execute(select(AgentWorkMessage).where(AgentWorkMessage.run_id == run_id))).scalar_one()
        message.updated_at = datetime.now() - timedelta(seconds=121)
        await db.commit()
    assert await service.recover_expired() == 1
    stream = await service.start(
        task.task_id,
        WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='retry', message='try again'),
        actor_user_id=10,
    )
    assert (await _collect(stream))[-1].data['status'] == 'completed'


@pytest.mark.asyncio
async def test_terminal_storage_failure_still_closes_stream(work_db, monkeypatch):
    factory, agent_id = work_db
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
    model = FakeModels()
    service = runner(factory, model)
    persist = service._persist

    async def fail_terminal(stream, *, terminal=None, error=None):
        if terminal is not None:
            raise RuntimeError('database-secret')
        return await persist(stream, terminal=terminal, error=error)

    monkeypatch.setattr(service, '_persist', fail_terminal)
    stream = await service.start(
        task.task_id,
        WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='create', message=task.goal),
        actor_user_id=10,
    )
    events = await asyncio.wait_for(_collect(stream), 2)
    assert events[-1].event_type == 'done' and events[-1].data['status'] == 'failed'
    assert 'database-secret' not in str(events)
    assert model.closed


@pytest.mark.asyncio
async def test_cancel_keeps_claim_until_old_upstream_closes(work_db):
    factory, agent_id = work_db
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
    old_model = FakeModels(slow=True)
    old_service = runner(factory, old_model)
    old_service.poll_seconds = 30
    payload = WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='create', message=task.goal)
    old_stream = await old_service.start(task.task_id, payload, actor_user_id=10)
    replacement = None
    new_service = runner(factory, FakeModels())
    try:
        await anext(old_stream)
        await anext(old_stream)
        await anext(old_stream)
        await new_service.cancel(task.task_id, old_stream.run_id, actor_user_id=10)
        assert not old_model.closed
        with pytest.raises(AgentServiceError) as blocked:
            replacement = await new_service.start(
                task.task_id, payload.model_copy(update={'client_message_id': 'retry'}), actor_user_id=10
            )
        assert blocked.value.status_code == 409
        async with factory() as db:
            detail = await WorkTaskService(db).get_task(task.task_id, actor_user_id=10)
            assert detail.current_run_id == old_stream.run_id
        await old_stream.aclose()
        assert old_model.closed
        replacement = await new_service.start(
            task.task_id, payload.model_copy(update={'client_message_id': 'retry'}), actor_user_id=10
        )
        assert (await _collect(replacement))[-1].data['status'] == 'completed'
    finally:
        await old_stream.aclose()
        if replacement is not None:
            await replacement.aclose()


@pytest.mark.asyncio
async def test_cancelled_orphan_claim_recovers_only_after_lease_expiry(work_db):
    factory, agent_id = work_db
    run_id = str(uuid4())
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
        row = await db.get(AgentWorkTask, task.task_id)
        row.current_run_id = run_id
        db.add(
            AgentWorkMessage(
                message_id=str(uuid4()),
                task_id=task.task_id,
                sequence=2,
                reply_to_message_id=task.messages[0].message_id,
                role='assistant',
                content='partial',
                agent_id=agent_id,
                agent_name_snapshot='Published',
                published_version=2,
                run_id=run_id,
                run_status='cancelled',
            )
        )
        await db.commit()
    service = runner(factory, FakeModels())
    assert await service.recover_expired() == 0
    async with factory() as db:
        message = (await db.execute(select(AgentWorkMessage).where(AgentWorkMessage.run_id == run_id))).scalar_one()
        message.updated_at = datetime.now() - timedelta(seconds=121)
        await db.commit()
    assert await service.recover_expired() == 1
    async with factory() as db:
        detail = await WorkTaskService(db).get_task(task.task_id, actor_user_id=10)
        assert detail.current_run_id is None
        assert detail.messages[-1].run_status == 'cancelled'
        assert detail.messages[-1].content == 'partial'


@pytest.mark.asyncio
async def test_late_initial_key_conflicts_without_changing_history(work_db):
    factory, agent_id = work_db
    async with factory() as db:
        task = await WorkTaskService(db).create_task(create_payload(agent_id), actor_user_id=10)
    model = FakeModels()
    service = runner(factory, model)
    payload = WorkTaskRunRequestModel(agentId=agent_id, clientMessageId='new-key', message='A later instruction')
    stream = await service.start(task.task_id, payload, actor_user_id=10)
    await _collect(stream)
    with pytest.raises(AgentServiceError) as late:
        await service.start(
            task.task_id,
            payload.model_copy(update={'client_message_id': 'create', 'message': task.goal}),
            actor_user_id=10,
        )
    assert late.value.status_code == 409
    assert len(model.calls) == 1
    async with factory() as db:
        detail = await WorkTaskService(db).get_task(task.task_id, actor_user_id=10)
        assert len(detail.messages) == 3
        assert detail.current_run_id is None
