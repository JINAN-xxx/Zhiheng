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


@asynccontextmanager
async def session() -> AsyncIterator[object]:
    yield object()


class Sessions:
    def __call__(self) -> AbstractAsyncContextManager[object]:
        return session()

    def begin(self) -> AbstractAsyncContextManager[object]:
        return session()


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
        ('work_agent', 'chat', 'workspace'),
    ],
)
async def test_publisher_places_dependent_summary_in_short_lane(
    resource_type: str, hint: object, expected: str
) -> None:
    delivery = OutboxDelivery(uuid4(), uuid4(), hint, resource_type)
    outbox = SimpleNamespace(pending=AsyncMock(return_value=[delivery]), mark_sent=AsyncMock())

    container = SimpleNamespace(
        database=SimpleNamespace(sessions=Sessions()),
        outbox=outbox,
        settings=SimpleNamespace(
            worker_short_queue='test:short', worker_queue='test:generation', worker_workspace_queue='test:workspace'
        ),
    )
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    await publish_outbox(container, queue)

    outbox.pending.assert_awaited_once_with(outbox.pending.await_args.args[0], payload_path=MODEL_TYPE_PATH)
    queue.enqueue_job.assert_awaited_once_with(
        'execute_workspace' if expected == 'workspace' else 'execute_chat',
        str(delivery.run_id),
        _job_id=str(delivery.id),
        _queue_name=f'test:{expected}',
    )
    outbox.mark_sent.assert_awaited_once_with(outbox.mark_sent.await_args.args[0], delivery.id)
    assert delivery.hint == hint


async def test_unrecognized_outbox_row_is_deferred_without_blocking_known_deliveries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    unknown = OutboxDelivery(uuid4(), uuid4(), 'private-hint', 'future_kind')
    chat = OutboxDelivery(uuid4(), uuid4(), 'chat', 'chat')
    outbox = SimpleNamespace(pending=AsyncMock(return_value=[unknown, chat]), mark_sent=AsyncMock(), defer=AsyncMock())
    container = SimpleNamespace(
        database=SimpleNamespace(sessions=Sessions()),
        outbox=outbox,
        settings=SimpleNamespace(worker_queue='generation'),
    )
    queue = SimpleNamespace(enqueue_job=AsyncMock())

    await publish_outbox(container, queue)

    outbox.defer.assert_awaited_once_with(outbox.defer.await_args.args[0], unknown.id)
    outbox.mark_sent.assert_awaited_once_with(outbox.mark_sent.await_args.args[0], chat.id)
    queue.enqueue_job.assert_awaited_once_with(
        'execute_chat', str(chat.run_id), _job_id=str(chat.id), _queue_name='generation'
    )
    assert str(unknown.run_id) in caplog.text and 'private-hint' not in caplog.text
