import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from exceptions.exception import ServiceException
from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_management_client import LiteLLMManagementClient
from plugins.ai.service import runtime_model_access_service as access_module
from plugins.ai.service.runtime_model_access_service import RuntimeModelAccessService


class RuntimeFixture:
    def __init__(self) -> None:
        self.models = ['old-model']
        self.deployed = ['old-model', 'new-one', 'new-two']
        self.updates = []
        self.fail_update = False
        self.read_entered = asyncio.Event()
        self.block_read = False
        self.release_read = asyncio.Event()
        self.lock = asyncio.Lock()
        self.lock_entries = 0
        self.client = LiteLLMManagementClient(
            LiteLLMSettings(
                LITELLM_BASE_URL='https://runtime.example',
                LITELLM_RUNTIME_KEY='runtime-secret',
                LITELLM_MANAGEMENT_KEY='admin-secret',
            ),
            transport=httpx.MockTransport(self.handle),
        )

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v2/model/info':
            return httpx.Response(200, json={'data': [{'model_name': alias} for alias in self.deployed]})
        if request.url.path == '/v2/key/info':
            snapshot = list(self.models)
            self.read_entered.set()
            if self.block_read:
                await self.release_read.wait()
            await asyncio.sleep(0)
            return httpx.Response(200, json={'info': [{'models': snapshot}]})
        assert request.url.path == '/key/update'
        if self.fail_update:
            return httpx.Response(403, text='private upstream message with admin-secret')
        body = json.loads(request.content)
        self.updates.append(body)
        self.models = body['models']
        return httpx.Response(200, json={'models': self.models})

    @asynccontextmanager
    async def serialized(self) -> AsyncIterator[None]:
        async with self.lock:
            self.lock_entries += 1
            yield

    def service(self) -> RuntimeModelAccessService:
        return RuntimeModelAccessService(self.client, access_lock=self.serialized)


@pytest.mark.asyncio
async def test_sync_preserves_models_and_is_idempotent() -> None:
    fixture = RuntimeFixture()
    try:
        await fixture.service().ensure_model_access('new-one')
        await fixture.service().ensure_model_access('new-one')
        assert fixture.models == ['old-model', 'new-one']
        assert len(fixture.updates) == 1
        assert set(fixture.updates[0]) == {'key', 'models'}
    finally:
        await fixture.client.aclose()


@pytest.mark.asyncio
async def test_sync_serializes_separate_requests_without_losing_either_new_model() -> None:
    fixture = RuntimeFixture()
    try:
        await asyncio.gather(
            fixture.service().ensure_model_access('new-one'), fixture.service().ensure_model_access('new-two')
        )
        assert set(fixture.models) == {'old-model', 'new-one', 'new-two'}
        assert fixture.lock_entries == len(fixture.updates)
    finally:
        await fixture.client.aclose()


@pytest.mark.asyncio
async def test_missing_deployment_never_grants_access() -> None:
    fixture = RuntimeFixture()
    try:
        with pytest.raises(ServiceException) as error:
            await fixture.service().ensure_model_access('absent')
        assert '部署' in error.value.message
        assert fixture.updates == []
    finally:
        await fixture.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('models', [[], ['*'], ['all-proxy-models']])
async def test_unrestricted_keys_are_not_narrowed(models: list[str]) -> None:
    fixture = RuntimeFixture()
    fixture.models = models
    try:
        await fixture.service().ensure_model_access('new-one')
        assert fixture.models == models
        assert fixture.updates == []
    finally:
        await fixture.client.aclose()


@pytest.mark.asyncio
async def test_failed_sync_is_sanitized_and_can_be_retried() -> None:
    fixture = RuntimeFixture()
    fixture.fail_update = True
    try:
        with pytest.raises(ServiceException) as error:
            await fixture.service().ensure_model_access('new-one')
        assert '授权同步失败' in error.value.message
        assert 'secret' not in repr(vars(error.value))
        assert error.value.__context__ is None
        assert fixture.models == ['old-model']
        fixture.fail_update = False
        await fixture.service().ensure_model_access('new-one')
        assert fixture.models == ['old-model', 'new-one']
    finally:
        await fixture.client.aclose()


@pytest.mark.asyncio
async def test_cancelled_sync_releases_lock_without_updating_models() -> None:
    fixture = RuntimeFixture()
    fixture.block_read = True
    try:
        task = asyncio.create_task(fixture.service().ensure_model_access('new-one'))
        await fixture.read_entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not fixture.lock.locked()
        assert fixture.updates == []
        fixture.release_read.set()
        await fixture.service().ensure_model_access('new-one')
        assert fixture.models == ['old-model', 'new-one']
    finally:
        await fixture.client.aclose()


@pytest.mark.asyncio
async def test_sync_timeout_preserves_models_and_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = RuntimeFixture()
    fixture.block_read = True
    monkeypatch.setattr(access_module, 'ACCESS_SYNC_TIMEOUT_SECONDS', 0.02)
    try:
        with pytest.raises(ServiceException) as error:
            await fixture.service().ensure_model_access('new-one')
        assert '授权同步失败' in error.value.message
        assert fixture.updates == []
        assert not fixture.lock.locked()
    finally:
        await fixture.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('alias', ['*', 'all-proxy-models', 'provider/*'])
async def test_sync_refuses_to_grant_wildcard_model_access(alias: str) -> None:
    fixture = RuntimeFixture()
    fixture.deployed.append(alias)
    try:
        with pytest.raises(ServiceException):
            await fixture.service().ensure_model_access(alias)
        assert fixture.updates == []
    finally:
        await fixture.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_kind', ['cancel', 'timeout'])
async def test_inflight_write_finishes_under_lock_before_next_publisher(
    stop_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = RuntimeFixture()
    original_handler = fixture.handle
    write_entered = asyncio.Event()
    commit_write = asyncio.Event()
    first_write_finished = asyncio.Event()
    server_tasks = []

    async def server_write(request: httpx.Request) -> httpx.Response:
        # A real HTTP server can commit even after its caller disconnects.
        await commit_write.wait()
        response = await original_handler(request)
        first_write_finished.set()
        return response

    async def delayed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/key/update' and not write_entered.is_set():
            write_entered.set()
            server = asyncio.create_task(server_write(request))
            server_tasks.append(server)
            return await asyncio.shield(server)
        return await original_handler(request)

    await fixture.client.aclose()
    fixture.client = LiteLLMManagementClient(
        LiteLLMSettings(
            LITELLM_BASE_URL='https://runtime.example',
            LITELLM_RUNTIME_KEY='runtime-secret',
            LITELLM_MANAGEMENT_KEY='admin-secret',
        ),
        transport=httpx.MockTransport(delayed_handler),
    )
    if stop_kind == 'timeout':
        monkeypatch.setattr(access_module, 'ACCESS_SYNC_TIMEOUT_SECONDS', 0.03)
    first = asyncio.create_task(fixture.service().ensure_model_access('new-one'))
    second = None
    try:
        await write_entered.wait()
        if stop_kind == 'cancel':
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
        await asyncio.sleep(0.06)
        assert fixture.lock.locked(), 'In-flight update must retain the serialization lock'
        assert not first.done(), 'Cancellation must drain the already sent write'
        monkeypatch.setattr(access_module, 'ACCESS_SYNC_TIMEOUT_SECONDS', 20)
        second = asyncio.create_task(fixture.service().ensure_model_access('new-two'))
        await asyncio.sleep(0)
        commit_write.set()
        with pytest.raises(asyncio.CancelledError if stop_kind == 'cancel' else ServiceException):
            await first
        await second
        assert first_write_finished.is_set()
        assert set(fixture.models) == {'old-model', 'new-one', 'new-two'}
    finally:
        commit_write.set()
        await asyncio.gather(first, *([second] if second else []), *server_tasks, return_exceptions=True)
        await fixture.client.aclose()
