"""Official OpenSandbox transport with task-local, strictly isolated model processes."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import shlex
import zlib
from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from zhiheng.infrastructure.sandbox.contracts import SandboxCommandResult, SandboxFile, SandboxInput, SandboxSession
from zhiheng.infrastructure.sandbox.paths import (
    MAX_COMMAND_SECONDS,
    MAX_FILE_BYTES,
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_FILES,
    sandbox_path,
)
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from opensandbox import Sandbox
    from opensandbox.services.isolated import IsolationSession

RUNNER = '/opt/zhiheng/runtime_runner.py'
ENVIRONMENT_RUNNER = '/opt/zhiheng/environment_runner.py'
INITIALIZE_SECONDS = 15
CLEANUP_SECONDS = 20
PACKAGE_SECONDS = 180
MAX_PACKAGES = 20
MAX_CODE_BYTES = 60000
MAX_REQUIREMENT_CHARACTERS = 200


class CollectedPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str
    snapshot_path: str
    size: int = Field(ge=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern='^[a-f0-9]{64}$')
    mime: str


class CollectedFile(CollectedPreview):
    validation: dict[str, object] = Field(default_factory=dict)
    previews: list[CollectedPreview] = Field(default_factory=list, max_length=1)


class HelperResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    success: bool
    error: str = ''
    message: str = ''
    files: list[CollectedFile] = Field(default_factory=list, max_length=MAX_SNAPSHOT_FILES)
    data: str = ''
    entries: list[dict[str, object]] = Field(default_factory=list, max_length=100)
    exit_code: int = 0
    stdout: str = ''
    stderr: str = ''
    timed_out: bool = False
    truncated: bool = False
    python: str = ''
    resolved: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    prepared: list[dict[str, object]] = Field(default_factory=list, max_length=MAX_SNAPSHOT_FILES)


@dataclass(frozen=True)
class OpenSandboxProfile:
    image: str
    cpu: str
    memory_mb: int
    lifetime_seconds: int
    ready_seconds: int
    pip_index_url: str


class OpenSandboxFactory:
    def __init__(
        self,
        url: str,
        api_key: str,
        image: str,
        *,
        cpu: str = '1',
        memory_mb: int = 1024,
        lifetime_seconds: int = 600,
        ready_seconds: int = 60,
        pip_index_url: str = 'https://pypi.org/simple',
    ) -> None:
        self._url, self._api_key = url.rstrip('/'), api_key
        self.profile = OpenSandboxProfile(image, cpu, memory_mb, lifetime_seconds, ready_seconds, pip_index_url)
        self._sessions: dict[str, OpenSandboxSession] = {}
        self._ready = False

    @property
    def available(self) -> bool:
        return bool(self._url and self._api_key and self.profile.image and self._ready)

    async def check(self) -> bool:
        self._ready = False
        if not self._url or not self._api_key:
            return False
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.get(self._url + '/health', headers=self._headers())
                profile = await client.get(self._url + '/platform/profile', headers=self._headers())
                if response.status_code == profile.status_code == HTTPStatus.OK:
                    policy = profile.json()
                    self._ready = policy.get('policy_version') == 1 and policy.get('workspace_bytes', 0) > 0
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass
        return self._ready

    def _headers(self) -> dict[str, str]:
        return {'OPEN-SANDBOX-API-KEY': self._api_key}

    async def create(
        self,
        run_id: UUID,
        attempt_id: UUID,
        *,
        inputs: tuple[SandboxInput, ...] = (),
        initial_files: tuple[SandboxInput, ...] = (),
    ) -> SandboxSession:
        if not await self.check():
            raise AppError('sandbox_unavailable', '文件执行环境尚未就绪，请联系管理员', 503)
        total = 0
        paths: set[str] = set()
        for roots, files in ((('inputs',), inputs), (('working', 'outputs'), initial_files)):
            for item in files:
                path = sandbox_path(item.path, roots=roots)
                total += len(item.content)
                if path in paths or len(item.content) > MAX_FILE_BYTES or total > MAX_SNAPSHOT_BYTES:
                    raise AppError('sandbox_input_limit', '任务资料重复或超过执行环境大小限制', 422)
                paths.add(path)
        from opensandbox import Sandbox  # noqa: PLC0415 - only provision when execution is requested
        from opensandbox.config import ConnectionConfig  # noqa: PLC0415
        from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule  # noqa: PLC0415

        address = urlsplit(self._url)
        transport = httpx.AsyncHTTPTransport(trust_env=False)
        client: Sandbox | None = None
        try:
            client = await Sandbox.create(
                self.profile.image,
                timeout=timedelta(seconds=self.profile.lifetime_seconds),
                ready_timeout=timedelta(seconds=self.profile.ready_seconds),
                resource={'cpu': self.profile.cpu, 'memory': f'{self.profile.memory_mb}Mi'},
                metadata={'owner': 'zhiheng', 'run_id': str(run_id), 'attempt_id': str(attempt_id)},
                extensions={'bootstrap.execd.isolation': 'enable'},
                network_policy=NetworkPolicy(
                    default_action='deny',
                    egress=[
                        NetworkRule(action='allow', target=host)
                        for host in sorted(
                            {'files.pythonhosted.org', urlsplit(self.profile.pip_index_url).hostname or 'pypi.org'}
                        )
                    ],
                ),
                connection_config=ConnectionConfig(
                    domain=address.netloc,
                    protocol=address.scheme,
                    api_key=self._api_key,
                    request_timeout=timedelta(seconds=PACKAGE_SECONDS + 30),
                    use_server_proxy=True,
                    disable_metrics=True,
                    transport=transport,
                ),
            )
            session = OpenSandboxSession(self, client, transport)
            self._sessions[client.id] = session
            await session.initialize(inputs, initial_files)
            return session
        except BaseException as error:
            if client is not None:
                try:
                    await asyncio.shield(self.kill(client.id))
                finally:
                    await client.close()
            await transport.aclose()
            if isinstance(error, (asyncio.CancelledError, AppError)):
                raise
            raise AppError('sandbox_unavailable', '文件执行环境创建失败，请检查服务与运行镜像', 503) from error

    async def kill(self, sandbox_id: str) -> None:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', sandbox_id):
            raise AppError('sandbox_invalid_identity', '执行环境标识无效', 422)
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=CLEANUP_SECONDS) as client:
                target = self._url + '/v1/sandboxes/' + sandbox_id
                response = await client.delete(target, headers=self._headers())
                if response.status_code not in {200, 202, 204, 404}:
                    raise AppError('sandbox_cleanup_pending', '执行环境回收尚未确认', 503)
                deadline = asyncio.get_running_loop().time() + CLEANUP_SECONDS
                while True:
                    status = await client.get(target, headers=self._headers())
                    if status.status_code == HTTPStatus.NOT_FOUND:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise AppError('sandbox_cleanup_pending', '执行环境回收尚未确认', 503)
                    await asyncio.sleep(0.1)
                residue = await client.delete(
                    self._url + '/platform/sandboxes/' + sandbox_id + '/residue', headers=self._headers()
                )
                if residue.status_code != HTTPStatus.OK or residue.json().get('clean') is not True:
                    raise AppError('sandbox_cleanup_pending', '执行环境附属资源回收尚未确认', 503)
        except httpx.HTTPError as error:
            raise AppError('sandbox_cleanup_pending', '执行环境回收尚未确认', 503) from error
        self._sessions.pop(sandbox_id, None)

    async def close(self) -> None:
        errors = []
        for session in tuple(self._sessions.values()):
            try:
                await session.close()
            except AppError as error:  # noqa: PERF203 - every owned environment receives a cleanup attempt
                errors.append(error)
        if errors:
            raise errors[0]


class OpenSandboxSession:
    def __init__(self, factory: OpenSandboxFactory, client: Sandbox, transport: httpx.AsyncHTTPTransport) -> None:
        self.factory, self._client, self._transport = factory, client, transport
        self._isolation: IsolationSession | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._cleanup_task: asyncio.Task[None] | None = None
        self._python = '/usr/local/bin/python'
        self._environment: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self._client.id

    async def initialize(self, inputs: tuple[SandboxInput, ...], initial_files: tuple[SandboxInput, ...]) -> None:
        await self._helper({'operation': 'initialize'})
        for item in inputs:
            await self._write(item.path, item.content, original=True)
        for item in initial_files:
            await self._write(item.path, item.content)
        # Fail closed when the runtime's isolated execution feature is unavailable.
        isolation = await self._isolate()
        from opensandbox.models.isolated import IsolatedRunOpts  # noqa: PLC0415

        probe = await isolation.run(
            "python -I -c 'import os,socket; assert os.getuid()==10001; "
            'assert int(next(x.split()[1] for x in open("/proc/self/status") if x.startswith("CapEff:")),16)==0; '
            's=socket.socket(); assert s.connect_ex(("1.1.1.1",443))!=0; print("isolated")\'',
            opts=IsolatedRunOpts(timeout_seconds=INITIALIZE_SECONDS),
        )
        text = ''.join(item.text for item in probe.logs.stdout)
        if probe.error is not None or 'isolated' not in text:
            raise AppError('sandbox_isolation_unavailable', '运行镜像未通过隔离检查', 503)

    async def _isolate(self) -> IsolationSession:
        if self._closed:
            raise AppError('sandbox_closed', '本轮执行环境已经关闭', 409)
        if self._isolation is None:
            from opensandbox.models.isolated import (  # noqa: PLC0415
                BindMount,
                CreateIsolatedSessionRequest,
                EnvPassthroughSpec,
                IsolatedWorkspaceSpec,
            )

            try:
                self._isolation = await self._client.isolation.create(
                    CreateIsolatedSessionRequest(
                        workspace=IsolatedWorkspaceSpec(path='/workspace', mode='rw'),
                        binds=[BindMount(source='/workspace/inputs', dest='/workspace/inputs', readonly=True)],
                        profile='strict',
                        share_net=False,
                        uid=10001,
                        gid=10001,
                        uid_mode='userns',
                        env_passthrough=EnvPassthroughSpec(mode='allow', keys=['PATH', 'LANG', 'PYTHONUTF8']),
                        idle_timeout_seconds=self.factory.profile.lifetime_seconds,
                    )
                )
            except Exception as error:
                raise AppError('sandbox_isolation_unavailable', '隔离执行不可用，任务已停止', 503) from error
        return self._isolation

    async def _quiesce(self) -> None:
        if self._isolation is not None:
            isolation, self._isolation = self._isolation, None
            try:
                await isolation.delete()
            except Exception as error:
                raise AppError('sandbox_cleanup_pending', '代码进程停止状态尚未确认，暂不能采集成果', 503) from error

    @staticmethod
    def _payload_command(payload: dict[str, object], runner: str = RUNNER) -> str:
        encoded = base64.b64encode(zlib.compress(json.dumps(payload, ensure_ascii=False).encode())).decode()
        return '/usr/local/bin/python -I ' + runner + ' ' + shlex.quote(encoded)

    async def _helper(
        self, payload: dict[str, object], *, runner: str = RUNNER, timeout: int = INITIALIZE_SECONDS
    ) -> HelperResult:
        if self._closed:
            raise AppError('sandbox_closed', '本轮执行环境已经关闭', 409)
        from opensandbox.models.execd import RunCommandOpts  # noqa: PLC0415

        execution = await self._client.commands.run(
            self._payload_command(payload, runner), opts=RunCommandOpts(timeout=timedelta(seconds=timeout))
        )
        if execution.error is not None:
            raise AppError('sandbox_operation_failed', '执行环境文件操作失败', 503)
        output = ''.join(item.text for item in execution.logs.stdout)
        try:
            result = HelperResult.model_validate_json(output)
        except ValueError as error:
            raise AppError('sandbox_invalid_response', '执行环境返回了无效结果', 503) from error
        if not result.success:
            raise AppError('sandbox_operation_failed', result.message or '执行环境操作失败', 422)
        return result

    async def _write(self, path: str, content: bytes, *, original: bool = False) -> None:
        value = sandbox_path(path, roots=('inputs',) if original else ('working', 'outputs'))
        if len(content) > MAX_FILE_BYTES:
            raise AppError('sandbox_file_too_large', '文件超过 20 MiB', 422)
        from opensandbox.models import WriteEntry  # noqa: PLC0415

        stage = '/run/zhiheng/upload-' + uuid4().hex
        await self._client.files.write_files(
            [WriteEntry(path=stage, data=content, mode=600, owner='root', group='root')]
        )
        await self._helper({'operation': 'write', 'path': value, 'stage': stage, 'original': original})

    async def execute(
        self,
        code: str,
        *,
        kind: Literal['python', 'shell'] = 'python',
        timeout: int = 45,
        packages: tuple[str, ...] = (),
    ) -> SandboxCommandResult:
        if not code or len(code.encode()) > MAX_CODE_BYTES or not 1 <= timeout <= MAX_COMMAND_SECONDS:
            raise AppError('sandbox_invalid_command', '执行内容或时限无效', 422)
        async with self._lock:
            try:
                if packages:
                    await self._prepare_packages(packages)
                isolation = await self._isolate()
            except asyncio.CancelledError:
                await asyncio.shield(self._close_unlocked())
                raise
            from opensandbox.models.isolated import IsolatedRunOpts  # noqa: PLC0415

            try:
                execution = await isolation.run(
                    self._payload_command(
                        {
                            'operation': 'run',
                            'kind': kind,
                            'code': code,
                            'timeout': timeout,
                            'python': self._python,
                        }
                    ),
                    opts=IsolatedRunOpts(timeout_seconds=timeout + 5),
                )
                if execution.error is not None:
                    raise ValueError('Isolated execution transport failed')
                result = HelperResult.model_validate_json(''.join(item.text for item in execution.logs.stdout))
                if not result.success:
                    raise ValueError('Invalid isolated command result')
            except BaseException as error:
                await asyncio.shield(self._close_unlocked())
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise AppError('sandbox_execution_uncertain', '执行结果尚未确认，执行环境已请求回收', 503) from error
            if result.timed_out or result.truncated:
                await self._quiesce()
            return SandboxCommandResult(
                result.exit_code, result.stdout, result.stderr, result.timed_out, result.truncated, self._environment
            )

    async def _prepare_packages(self, packages: tuple[str, ...]) -> None:
        from packaging.requirements import InvalidRequirement, Requirement  # noqa: PLC0415

        if len(packages) > MAX_PACKAGES:
            raise AppError('sandbox_dependency_limit', '单次最多声明 20 个依赖', 422)
        normalized = []
        for value in packages:
            try:
                requirement = Requirement(value)
            except InvalidRequirement as error:
                raise AppError('sandbox_invalid_dependency', '依赖应为包名和版本要求', 422) from error
            if requirement.url or len(value) > MAX_REQUIREMENT_CHARACTERS:
                raise AppError('sandbox_invalid_dependency', '依赖不能包含 URL、本地路径或安装命令', 422)
            normalized.append(str(requirement))
        await self._quiesce()
        prepared = await self._helper(
            {'operation': 'download', 'packages': sorted(set(normalized)), 'index': self.factory.profile.pip_index_url},
            runner=ENVIRONMENT_RUNNER,
            timeout=PACKAGE_SECONDS,
        )
        if prepared.missing:
            isolation = await self._isolate()
            from opensandbox.models.isolated import IsolatedRunOpts  # noqa: PLC0415

            execution = await isolation.run(
                self._payload_command(
                    {'operation': 'install', 'packages': sorted(set(normalized))}, ENVIRONMENT_RUNNER
                ),
                opts=IsolatedRunOpts(timeout_seconds=PACKAGE_SECONDS),
            )
            if execution.error is not None:
                raise AppError('sandbox_dependency_failed', '依赖安装失败', 422)
            prepared = HelperResult.model_validate_json(''.join(item.text for item in execution.logs.stdout))
            if not prepared.success:
                raise AppError('sandbox_dependency_failed', prepared.message or '依赖安装失败', 422)
        self._python = prepared.python or '/usr/local/bin/python'
        self._environment = tuple(prepared.resolved)

    async def read_binary(self, path: str, *, max_bytes: int = MAX_FILE_BYTES) -> bytes:
        value = sandbox_path(path)
        if not 0 <= max_bytes <= MAX_FILE_BYTES:
            raise AppError('sandbox_file_too_large', '文件读取上限无效', 422)
        async with self._lock:
            await self._quiesce()
            nonce = uuid4().hex
            try:
                result = await self._helper({'operation': 'read', 'path': value, 'limit': max_bytes, 'nonce': nonce})
                if len(result.files) != 1:
                    raise AppError('sandbox_invalid_response', '文件读取结果无效', 503)
                return (await self._download(result.files[0], nonce)).content
            finally:
                await self._helper({'operation': 'cleanup_snapshot', 'nonce': nonce})

    async def read_file(self, path: str, *, max_bytes: int = 65536) -> str:
        try:
            return (await self.read_binary(path, max_bytes=max_bytes)).decode('utf-8-sig')
        except UnicodeError as error:
            raise AppError('sandbox_binary_file', '该文件不是 UTF-8 文本，请使用文件工具读取', 422) from error

    async def write_file(self, path: str, content: str) -> None:
        async with self._lock:
            await self._quiesce()
            await self._write(path, content.encode())

    async def add_input(self, item: SandboxInput) -> None:
        async with self._lock:
            await self._quiesce()
            await self._write(item.path, item.content, original=True)

    async def list_files(self, path: str = '.') -> tuple[dict[str, object], ...]:
        value = sandbox_path(path, directory=True)
        async with self._lock:
            await self._quiesce()
            return tuple((await self._helper({'operation': 'list', 'path': value})).entries)

    async def _collect(self, *, working: bool) -> tuple[SandboxFile, ...]:
        await self._quiesce()
        nonce = uuid4().hex
        try:
            prepared = []
            if not working:
                from opensandbox.models.isolated import IsolatedRunOpts  # noqa: PLC0415

                isolation = await self._isolate()
                rendering = await isolation.run(
                    self._payload_command({'operation': 'render', 'nonce': nonce}),
                    opts=IsolatedRunOpts(timeout_seconds=100),
                )
                if rendering.error is not None:
                    raise AppError('sandbox_preview_failed', '成果检查未完成，请修正文件后重试', 422)
                reviewed = HelperResult.model_validate_json(''.join(item.text for item in rendering.logs.stdout))
                if not reviewed.success:
                    raise AppError('sandbox_preview_failed', reviewed.message or '成果检查未完成', 422)
                prepared = reviewed.prepared
                await self._quiesce()
            manifest = await self._helper(
                {'operation': 'collect', 'nonce': nonce, 'working': working, 'prepared': prepared}, timeout=60
            )
            result = []
            total = 0
            for item in manifest.files:
                sandbox_path(item.path, roots=('working', 'outputs') if working else ('outputs',))
                result.append(await self._download(item, nonce))
                total += len(result[-1].content) + sum(len(preview.content) for preview in result[-1].previews)
                if total > MAX_SNAPSHOT_BYTES:
                    raise AppError('sandbox_integrity_error', '成果快照超过大小上限', 503)
            return tuple(result)
        finally:
            await self._quiesce()
            await self._helper({'operation': 'cleanup_snapshot', 'nonce': nonce})

    async def _download(self, item: CollectedFile, nonce: str) -> SandboxFile:
        value = sandbox_path(item.path)
        data = await self._read_snapshot(item, nonce)
        previews = [SandboxInput(preview.path, await self._read_snapshot(preview, nonce)) for preview in item.previews]
        return SandboxFile(value, data, item.sha256, item.mime, item.validation, tuple(previews))

    async def _read_snapshot(self, item: CollectedPreview, nonce: str) -> bytes:
        if not re.fullmatch('/run/zhiheng/' + nonce + '/[0-9]+(?:-preview-[0-9]+)?', item.snapshot_path):
            raise AppError('sandbox_invalid_response', '成果快照路径无效', 503)
        content = bytearray()
        stream = await self._client.files.read_bytes_stream(item.snapshot_path)
        try:
            async for block in stream:
                content.extend(block)
                if len(content) > MAX_FILE_BYTES:
                    raise AppError('sandbox_file_too_large', '成果超过文件大小上限', 422)
        finally:
            await stream.aclose()
        data = bytes(content)
        if len(data) != item.size or hashlib.sha256(data).hexdigest() != item.sha256:
            raise AppError('sandbox_integrity_error', '成果快照完整性检查失败', 503)
        return data

    async def collect_outputs(self) -> tuple[SandboxFile, ...]:
        async with self._lock:
            return await self._collect(working=False)

    async def snapshot_files(self) -> tuple[SandboxInput, ...]:
        async with self._lock:
            return tuple(SandboxInput(item.path, item.content) for item in await self._collect(working=True))

    async def _close_unlocked(self) -> None:
        if self._cleanup_task is None:
            self._closed = True
            self._cleanup_task = asyncio.create_task(self._destroy(), name='workspace-sandbox-cleanup')
        await asyncio.shield(self._cleanup_task)

    async def _destroy(self) -> None:
        try:
            await self.factory.kill(self.id)
        finally:
            try:
                await self._client.close()
            finally:
                await self._transport.aclose()

    async def close(self) -> None:
        # Cancellation must not wait behind the long-running command's serialization lock.
        await self._close_unlocked()
