import asyncio
import threading
from datetime import timedelta

import pytest

from tests.unit.test_milvus_lifecycle import delayed_milvus_transport
from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC
from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


@pytest.mark.parametrize('stop', ['deadline', 'cancel', 'lease'])
async def test_owned_graph_rpc_drains_actual_transport_and_executor(stop: str) -> None:
    with delayed_milvus_transport(0.4, {'GetVersion'}) as (uri, records, entered):
        control = VectorIOControl(
            timeout_seconds=0.15 if stop == 'deadline' else 4,
            lease_until=utcnow() + timedelta(seconds=0.15) if stop == 'lease' else None,
        )
        rpc = GraphVectorRPC(uri=uri, token='', database='default', control=control)
        task = asyncio.create_task(rpc.acall('get_server_version', timeout=3))
        assert await asyncio.to_thread(entered.wait, 1)
        if stop == 'cancel':
            control.cancel()
        with pytest.raises(AppError):
            await task
        await rpc.aclose()
        assert rpc.stopped
        assert rpc.pending_count == 0
        assert rpc.live_threads == 0
        assert records


async def test_shutdown_repeated_cancel_waits_for_real_delayed_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    from pymilvus import MilvusClient

    entered, release = threading.Event(), threading.Event()
    original = MilvusClient.get_server_version

    def delayed(client: object, **kwargs: object) -> str:
        entered.set()
        assert release.wait(2)
        return original(client, **kwargs)

    monkeypatch.setattr(MilvusClient, 'get_server_version', delayed)
    with delayed_milvus_transport(0, set()) as (uri, _, _):
        rpc = GraphVectorRPC(uri=uri, token='', database='default', control=VectorIOControl(timeout_seconds=1))
        task = asyncio.create_task(rpc.acall('get_server_version', timeout=1))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and not rpc.stopped and rpc.pending_count == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await rpc.aclose()
        assert rpc.stopped and rpc.pending_count == 0 and rpc.live_threads == 0


async def test_two_ten_second_waits_cannot_release_a_delayed_real_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    from uuid import uuid4
    from pymilvus import MilvusClient
    from tests.core.resources import resource

    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus required')
    name = 'graph_probe_' + uuid4().hex
    description = 'owned-drain:' + uuid4().hex
    client = MilvusClient(uri=uri, timeout=5)
    resource('intent', 'milvus_collection', name, description=description, database='default')
    client.create_collection(name, dimension=2, description=description)
    entered, release = threading.Event(), threading.Event()
    original = MilvusClient.upsert

    def delayed(source: object, *args: object, **kwargs: object):
        entered.set()
        if not release.wait(30):
            raise TimeoutError('Probe release bound')
        return original(source, *args, **kwargs)

    monkeypatch.setattr(MilvusClient, 'upsert', delayed)
    rpc = GraphVectorRPC(uri=uri, token='', database='default', control=VectorIOControl(timeout_seconds=0.5))
    operation = asyncio.create_task(rpc.acall('upsert', name, [{'id': 1, 'vector': [0.1, 0.2]}]))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(10)
        operation.cancel()
        assert not operation.done() and not rpc.stopped and rpc.pending_count == 1 and rpc.live_threads == 1
        await asyncio.sleep(10)
        assert not operation.done() and not rpc.stopped and rpc.pending_count == 1 and rpc.live_threads == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await rpc.aclose()
        assert rpc.stopped and rpc.pending_count == 0 and rpc.live_threads == 0
        assert client.query(name, filter='', output_fields=['count(*)'], consistency_level='Strong') == [
            {'count(*)': 0}
        ]
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)
        await rpc.aclose()
        assert client.describe_collection(name)['description'] == description
        client.drop_collection(name)
        assert not client.has_collection(name)
        client.close()
        resource('deleted', 'milvus_collection', name)
