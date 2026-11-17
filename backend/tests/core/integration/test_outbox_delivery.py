"""Persisted Run resource type reaches the real publisher, not a copied routing helper."""

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from sqlalchemy import select

from zhiheng.application.execution.dispatch import MODEL_TYPE_PATH
from zhiheng.runtime.orm import Outbox
from zhiheng.worker.publisher import publish_outbox

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI


async def test_chat_summary_outbox_is_delivered_to_short_queue(app: 'FastAPI', admin: 'httpx.AsyncClient') -> None:
    container = app.state.container
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    async with container.database.sessions.begin() as session:
        summary = await container.runs.create(
            session,
            owner_id=owner,
            resource_type='chat_summary',
            resource_id=uuid4(),
            request_id=uuid4(),
            fingerprint='summary-dispatch',
            payload={'binding': {'model_type': 'chat'}},
        )
        reply = await container.runs.create(
            session,
            owner_id=owner,
            resource_type='chat',
            resource_id=uuid4(),
            request_id=uuid4(),
            fingerprint='reply-dispatch',
            payload={'binding': {'model_type': 'chat'}},
        )
    async with container.database.sessions() as session:
        deliveries = await container.outbox.pending(session, payload_path=MODEL_TYPE_PATH)
    by_run = {delivery.run_id: delivery for delivery in deliveries}
    assert by_run[summary.id].resource_type == 'chat_summary'
    assert by_run[summary.id].hint == by_run[reply.id].hint == 'chat'

    queue = SimpleNamespace(enqueue_job=AsyncMock())
    await publish_outbox(container, queue)
    queued = {
        UUID(call.args[1]): (call.kwargs['_job_id'], call.kwargs['_queue_name'])
        for call in queue.enqueue_job.await_args_list
    }
    assert queued[summary.id] == (str(by_run[summary.id].id), container.settings.worker_short_queue)
    assert queued[reply.id] == (str(by_run[reply.id].id), container.settings.worker_queue)
    async with container.database.sessions() as session:
        sent = list(await session.scalars(select(Outbox.sent_at).where(Outbox.run_id.in_((summary.id, reply.id)))))
    assert len(sent) == 2 and all(timestamp is not None for timestamp in sent)
