"""Publish actual jobs to the selected lane, including dependent chat summaries."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zhiheng.application.execution.dispatch import MODEL_TYPE_PATH
from zhiheng.runtime.outbox import OutboxDelivery
from zhiheng.worker.publisher import publish_outbox


@pytest.mark.parametrize(
    'resource_type,hint,expected',
    [
        ('chat_summary', 'chat', 'short'),
        ('chat', 'chat', 'generation'),
        ('agent_chat', 'chat', 'generation'),
        ('agent_preview', 'chat', 'generation'),
        ('knowledge_graph', 'chat', 'generation'),
        ('public', 'chat', 'generation'),
        ('knowledge_embedding', 'embedding', 'short'),
        ('knowledge_rerank', 'rerank', 'short'),
        ('', 'chat', 'generation'),
    ],
)
async def test_publisher_places_dependent_summary_in_short_lane(
    resource_type: str, hint: object, expected: str
) -> None:
    delivery = OutboxDelivery(uuid4(), uuid4(), hint, resource_type)
    outbox = SimpleNamespace(pending=AsyncMock(return_value=[delivery]), mark_sent=AsyncMock())

    @asynccontextmanager
    async def session() -> AsyncIterator[object]:
        yield object()

    container = SimpleNamespace(
        database=SimpleNamespace(sessions=SimpleNamespace(__call__=session)),
        outbox=outbox,
        settings=SimpleNamespace(worker_short_queue='test:short', worker_queue='test:generation'),
    )

    class Sessions:
        def __call__(self) -> AbstractAsyncContextManager[object]:
            return session()

        def begin(self) -> AbstractAsyncContextManager[object]:
            return session()

    container.database.sessions = Sessions()
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    await publish_outbox(container, queue)

    outbox.pending.assert_awaited_once_with(outbox.pending.await_args.args[0], payload_path=MODEL_TYPE_PATH)
    queue.enqueue_job.assert_awaited_once_with(
        'execute_chat',
        str(delivery.run_id),
        _job_id=str(delivery.id),
        _queue_name=f'test:{expected}',
    )
    outbox.mark_sent.assert_awaited_once_with(outbox.mark_sent.await_args.args[0], delivery.id)
    assert delivery.hint == hint


def test_old_delivery_constructor_keeps_default_routing_fact() -> None:
    delivery = OutboxDelivery(uuid4(), uuid4(), 'chat')
    assert delivery.resource_type == ''
