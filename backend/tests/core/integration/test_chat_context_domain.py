from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from zhiheng.shared.errors import AppError


async def test_summary_result_is_an_immutable_chat_fact_with_idempotent_completion(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    from zhiheng.modules.chat.public import ContextMemory, SummaryTaskDraft

    container = app.state.container
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    cid = UUID(conversation['id'])
    memory = ContextMemory()
    async with container.database.sessions.begin() as session:
        actor = await container.identity.authenticate(session, admin.cookies.get('zhiheng_session'))
        state = await memory.state(session, cid, actor.id, lock=True)
        binding = await container.models.bindings.resolve(session, UUID(model_id))
        parent = await container.runs.create(
            session,
            owner_id=actor.id,
            resource_type='chat',
            resource_id=cid,
            request_id=uuid4(),
            fingerprint='p' * 64,
            payload={},
        )
        parent_claim = await container.runs.claim(session, parent.id, 60)
        assert parent_claim is not None
        task = await memory.create_task(
            session,
            SummaryTaskDraft(
                id=uuid4(),
                parent_run_id=parent.id,
                parent_attempt_id=parent_claim.attempt_id,
                conversation_id=cid,
                owner_id=actor.id,
                expected_version=state.version,
                covered_count=1,
                source_hash='a' * 64,
                source_refs=[],
                file_refs=[],
                model_binding=binding.model_dump(mode='json'),
            ),
        )
        run = await container.runs.create(
            session,
            owner_id=actor.id,
            resource_type='chat_summary',
            resource_id=task.id,
            request_id=uuid4(),
            fingerprint='a' * 64,
            payload={},
        )
        state = await memory.attach_run(session, state, task, run.id)
        with pytest.raises(ValidationError):
            state.version = 9
        await memory.complete_agent_summary(session, task.id, 'A durable summary')
        await memory.complete_agent_summary(session, task.id, 'A durable summary')
        published = await memory.state(session, cid, actor.id)
        assert published.version == 1 and published.pending_task_id is None
        with pytest.raises(AppError) as stale:
            await memory.complete_agent_summary(session, task.id, 'A different replacement')
        assert stale.value.code == 'chat_summary_stale'
        fresh = await memory.task(session, task.id)
        assert fresh is not None and fresh.summary == 'A durable summary' and fresh.status == 'published'
