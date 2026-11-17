import asyncio
import base64
import hashlib
import json
import zlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from zhiheng.infrastructure.sandbox.adapters.opensandbox import (
    CollectedFile,
    OpenSandboxFactory,
    OpenSandboxSession,
)
from zhiheng.infrastructure.sandbox.paths import MAX_FILE_BYTES, sandbox_path
from zhiheng.shared.errors import AppError


@pytest.mark.parametrize(
    'path',
    [
        '../outside',
        '/etc/passwd',
        'outputs/../private',
        'inputs/a\\b',
        'outputs//x',
        'outputs/./x',
        'outputs/\x00x',
        '/run/zhiheng/x',
        'outputs/a\x01.txt',
        'outputs/a\x7f.txt',
        'outputs/a\x85.txt',
        'outputs/a\ud800.txt',
        '\noutputs/a.txt',
    ],
)
def test_task_paths_never_escape_their_resource_roots(path: str) -> None:
    with pytest.raises(AppError):
        sandbox_path(path)


def test_input_paths_cannot_be_used_by_mutating_file_tools() -> None:
    assert sandbox_path('/workspace/outputs/汇总.xlsx') == 'outputs/汇总.xlsx'
    with pytest.raises(AppError):
        sandbox_path('inputs/original/data.csv', roots=('working', 'outputs'))


def test_model_command_is_encoded_as_data_in_a_fixed_trusted_launcher() -> None:
    payload = {'operation': 'run', 'code': 'print("`$(cat /etc/secret)`")', 'kind': 'python', 'timeout': 1}
    command = OpenSandboxSession._payload_command(payload)
    prefix, encoded = command.rsplit(' ', 1)
    assert prefix == '/usr/local/bin/python -I /opt/zhiheng/runtime_runner.py'
    assert json.loads(zlib.decompress(base64.b64decode(encoded))) == payload
    assert 'cat /etc/secret' not in command


@pytest.fixture
def session() -> OpenSandboxSession:
    factory = OpenSandboxFactory('http://manager', 'private-manager-key', 'runtime')
    factory.kill = AsyncMock()
    client = SimpleNamespace(
        id='owned-test-sandbox',
        close=AsyncMock(),
        files=SimpleNamespace(write_files=AsyncMock(), read_bytes_stream=AsyncMock()),
        commands=SimpleNamespace(run=AsyncMock()),
        isolation=SimpleNamespace(create=AsyncMock()),
    )
    transport = MagicMock(spec=httpx.AsyncHTTPTransport)
    transport.aclose = AsyncMock()
    return OpenSandboxSession(factory, client, transport)


async def test_model_isolation_requests_private_network_nonroot_and_readonly_inputs(
    session: OpenSandboxSession,
) -> None:
    isolated = await session._isolate()
    assert isolated is session._client.isolation.create.return_value
    request = session._client.isolation.create.call_args.args[0]
    assert request.profile == 'strict' and request.uid == request.gid == 10001
    assert request.uid_mode == 'userns' and request.share_net is False
    assert request.binds[0].readonly is True and request.binds[0].source == '/workspace/inputs'
    assert request.env_passthrough.keys == ['PATH', 'LANG', 'PYTHONUTF8']


async def test_quiesce_keeps_container_alive_and_new_execution_can_get_a_fresh_namespace(
    session: OpenSandboxSession,
) -> None:
    old = SimpleNamespace(delete=AsyncMock())
    session._isolation = old
    await session._quiesce()
    old.delete.assert_awaited_once()
    assert session._closed is False and session._isolation is None
    await session._isolate()
    session._client.isolation.create.assert_awaited_once()
    session.factory.kill.assert_not_awaited()


async def test_explicit_close_does_not_wait_for_a_running_command_lock(session: OpenSandboxSession) -> None:
    await session._lock.acquire()
    try:
        await asyncio.wait_for(session.close(), timeout=1)
    finally:
        session._lock.release()
    session.factory.kill.assert_awaited_once_with(session.id)
    session._client.close.assert_awaited_once()
    session._transport.aclose.assert_awaited_once()


async def test_command_cancellation_reclaims_its_container(session: OpenSandboxSession) -> None:
    isolated = SimpleNamespace(run=AsyncMock(side_effect=asyncio.CancelledError()))
    session._isolation = isolated
    with pytest.raises(asyncio.CancelledError):
        await session.execute('while True: pass')
    session.factory.kill.assert_awaited_once_with(session.id)
    assert session._closed


async def test_dynamic_dependencies_reject_direct_urls_before_any_runtime_call(session: OpenSandboxSession) -> None:
    with pytest.raises(AppError) as error:
        await session._prepare_packages(('demo @ https://untrusted.example/package.whl',))
    assert error.value.code == 'sandbox_invalid_dependency'
    session._client.commands.run.assert_not_awaited()


async def test_cancellation_during_dependency_preparation_reclaims_the_container(session: OpenSandboxSession) -> None:
    session._prepare_packages = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await session.execute('print(42)', packages=('openpyxl',))
    session.factory.kill.assert_awaited_once_with(session.id)


async def test_large_uploads_do_not_enter_command_line_arguments(session: OpenSandboxSession) -> None:
    session._helper = AsyncMock()
    content = b'x' * MAX_FILE_BYTES
    await session._write('inputs/source/data.bin', content, original=True)
    entry = session._client.files.write_files.call_args.args[0][0]
    assert entry.data == content and entry.mode == 600
    payload = session._helper.call_args.args[0]
    assert 'data' not in payload and payload['stage'].startswith('/run/zhiheng/upload-')


async def test_snapshot_transport_rechecks_hash_and_closes_stream_on_failure(session: OpenSandboxSession) -> None:
    class Stream:
        closed = False

        def __aiter__(self) -> 'Stream':
            return self

        async def __anext__(self) -> bytes:
            if not hasattr(self, 'sent'):
                self.sent = True
                return b'corrupt'
            raise StopAsyncIteration

        async def aclose(self) -> None:
            self.closed = True

    stream = Stream()
    session._client.files.read_bytes_stream.return_value = stream
    nonce = uuid4().hex
    item = CollectedFile(
        path='outputs/report.csv',
        snapshot_path='/run/zhiheng/' + nonce + '/0',
        size=7,
        sha256=hashlib.sha256(b'correct').hexdigest(),
        mime='text/csv',
    )
    with pytest.raises(AppError) as error:
        await session._download(item, nonce)
    assert error.value.code == 'sandbox_integrity_error' and stream.closed


async def test_manager_health_without_enforced_profile_does_not_enable_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_type = httpx.AsyncClient

    def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'status': 'healthy'}) if request.url.path == '/health' else httpx.Response(404)

    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(response), **kwargs)
    )
    factory = OpenSandboxFactory('http://manager', 'key', 'runtime')
    assert not await factory.check() and not factory.available


async def test_main_container_deletion_is_not_enough_to_confirm_sidecar_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_type = httpx.AsyncClient

    def response(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith('/platform/'):
            return httpx.Response(503, json={'clean': False})
        return httpx.Response(204 if request.method == 'DELETE' else 404)

    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(response), **kwargs)
    )
    factory = OpenSandboxFactory('http://manager', 'key', 'runtime')
    with pytest.raises(AppError) as error:
        await factory.kill(str(uuid4()))
    assert error.value.code == 'sandbox_cleanup_pending'
