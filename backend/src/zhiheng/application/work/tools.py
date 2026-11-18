"""Task-scoped tools over public file services and the reusable sandbox adapter."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import timedelta
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid5

from zhiheng.application.work.snapshots import skill_selection
from zhiheng.infrastructure.sandbox import SandboxFile, SandboxInput, SandboxSession, sandbox_path
from zhiheng.modules.files.public import FileInfo
from zhiheng.modules.search.public import WebSearchRequest
from zhiheng.modules.work.public import StepStatus, TaskDetail, WorkArtifactInfo, WorkPlanItem, normalize_work_fact
from zhiheng.runtime.contracts import ClaimedRun
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import has_capability
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.application.work.agent import WorkAgentService
    from zhiheng.application.work.execution import ExecutionProgress

CODE_TOOLS = frozenset({'run_python', 'execute', 'write_file', 'read_file', 'list_files', 'publish_outputs'})
MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024
MAX_SANDBOXES = 2
MAX_TOOL_PREVIEW = 10000
MAX_DEPENDENCY_ERROR = 600
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()


class WorkspaceTools:
    def __init__(
        self,
        service: 'WorkAgentService',
        claim: ClaimedRun,
        principal: Principal,
        progress: 'ExecutionProgress',
        project: Callable[[ClaimedRun, 'ExecutionProgress'], Awaitable[None]],
    ) -> None:
        self.service, self.context, self.claim = service, service.context, claim
        self.principal, self.progress, self.project = principal, progress, project
        self.session: SandboxSession | None = None
        self.lease_id: UUID | None = None
        self._failed_operations: set[str] = set()
        files = claim.payload.get('workspace_files', [])
        self.workspace_files = (
            [dict(item) for item in files if isinstance(item, dict)] if isinstance(files, list) else []
        )
        self.staged: dict[str, SandboxInput] = {}
        self._workspace_sequence = 0
        self._workspace_loaded = False
        self._mutation_pending = False
        self._lock = asyncio.Lock()

    @property
    def blocking(self) -> bool:
        return bool(self._failed_operations)

    def _failure(self, operation: str, *, failed: bool) -> None:
        if failed:
            self._failed_operations.add(operation)
        else:
            self._failed_operations.discard(operation)

    async def _restore_workspace(self) -> None:
        if self._workspace_loaded:
            return
        async with self.context.database.sessions() as session:
            latest = await self.context.tasks.latest_workspace_files(session, self.claim.resource_id)
        if latest is not None:
            self._workspace_sequence, self.workspace_files = latest.sequence, latest.files
            if self.claim.payload.get('resume') is True or latest.run_id == self.claim.id:
                self._failed_operations = set(latest.failed_operations)
        self._workspace_loaded = True

    async def _detail(self) -> TaskDetail:
        async with self.context.database.sessions() as session:
            return await self.context.tasks.detail(session, self.principal, self.claim.resource_id)

    async def _file(self, identifier: str) -> tuple[FileInfo, bytes, str]:
        try:
            file_id = UUID(identifier)
        except ValueError:
            raise AppError('invalid_file_id', '请使用当前任务中的文件编号', 422) from None
        detail = await self._detail()
        item = next((item for item in detail.inputs if item.file_id == file_id), None)
        artifact = next((item for item in detail.artifacts if file_id in (item.id, item.file_id)), None)
        if item is None and artifact is None:
            async with self.context.database.sessions() as session:
                artifact = await self.context.tasks.artifact(session, self.principal, self.claim.resource_id, file_id)
        native_id = item.file_id if item is not None else artifact.file_id if artifact is not None else file_id
        info, content = await self.context.queries.read(self.principal.personal(), native_id)
        path = f'inputs/{file_id.hex}/{info.original_filename}'
        staged = SandboxInput(path, content)
        if path not in self.staged:
            if self.session is not None:
                await self.session.add_input(staged)
            self.staged[path] = staged
        return info, content, path

    async def _sandbox(self) -> SandboxSession:
        if self.session is not None:
            return self.session
        await self.service.guard(self.claim, code=True)
        self.progress.code_execution_started = True
        await self._restore_workspace()
        if not await self.context.sandbox.check():
            raise AppError('work_sandbox_unavailable', '文件处理环境暂时不可用，已有资料和成果保留', 503)
        detail = await self._detail()
        for task_input in detail.inputs:
            await self._file(str(task_input.file_id))
        initial = []
        for snapshot in self.workspace_files:
            if snapshot.get('empty') is True:
                if snapshot.get('sha256') != EMPTY_SHA256:
                    raise AppError('file_integrity_error', '工作现场快照校验失败', 503)
                initial.append(SandboxInput(str(snapshot['path']), b''))
                continue
            file_id = UUID(str(snapshot['file_id']))
            info, content = await self.context.queries.read(self.principal.personal(), file_id)
            if info.sha256 != snapshot['sha256']:
                raise AppError('file_integrity_error', '工作现场快照校验失败', 503)
            initial.append(SandboxInput(str(snapshot['path']), content))
        async with self.context.database.sessions.begin() as session:
            await self.context.runs.lock_request(session, UUID(int=0), uuid5(UUID(int=0), 'workspace-sandbox-slots'))
            leases = await self.context.tasks.sandbox_leases(session)
            active = [lease for lease in leases if lease.status != 'closed' and lease.expires_at > utcnow()]
            if len(active) >= MAX_SANDBOXES:
                raise AppError('work_sandbox_busy', '执行环境繁忙，请稍后继续任务', 503)
            lease = await self.context.tasks.reserve_sandbox(
                session,
                self.claim.resource_id,
                self.claim.id,
                self.claim.attempt_id,
                image=self.context.policy.sandbox_image,
                expires_at=utcnow() + timedelta(seconds=self.context.policy.run_seconds + 180),
            )
            self.lease_id = lease.id
        created = await self.context.sandbox.create(
            self.claim.id, self.claim.attempt_id, inputs=tuple(self.staged.values()), initial_files=tuple(initial)
        )
        self.session = created
        try:
            async with self.context.database.sessions.begin() as session:
                await self.context.tasks.bind_sandbox(session, lease.id, self.claim.attempt_id, created.id)
        except BaseException:
            await self.close()
            raise
        return created

    async def _register(self, path: str, content: bytes, purpose: str, *, parent: UUID | None = None) -> FileInfo:
        key = await self.context.files.storage.put(content)
        async with self.context.objects.registration(key) as session:
            await self.context.runs.lock_execution(session, self.claim.id, self.claim.attempt_id, allow_cancel=False)
            return await self._create_file(session, path, content, key, purpose, parent=parent)

    async def _create_file(
        self,
        session: 'AsyncSession',
        path: str,
        content: bytes,
        key: str,
        purpose: str,
        *,
        parent: UUID | None = None,
    ) -> FileInfo:
        info = await self.context.files.create(
            session,
            self.principal.personal(),
            filename=PurePosixPath(path).name,
            content=content,
            storage_key=key,
            source_file_id=parent,
            purpose=purpose,
        )
        if parent is None:
            await self.service.bind_file(session, self.principal, self.claim.resource_id, info)
        else:
            snapshot = await self.context.files.source_snapshot(
                session, self.principal.personal(), parent, artifact_file_id=info.id
            )
            await self.context.files.references.bind_sources(
                session,
                self.principal.personal(),
                (snapshot,),
                resource_type='work_agent_task',
                resource_id=self.claim.resource_id,
            )
        return info

    async def _store_artifact(self, item: SandboxFile) -> WorkArtifactInfo:
        preview = next(
            (value for value in item.previews if value.content and value.path.lower().endswith(('.pdf', '.png'))), None
        )
        keys = []
        try:
            keys.append(await self.context.files.storage.put(item.content))
            if preview is not None:
                keys.append(await self.context.files.storage.put(preview.content))
        except BaseException:
            for key in keys:
                await self.context.objects.discard_unregistered(key)
            raise
        async with self.context.objects.registrations(tuple(keys)) as session:
            await self.context.runs.lock_execution(session, self.claim.id, self.claim.attempt_id, allow_cancel=False)
            info = await self._create_file(session, item.path, item.content, keys[0], 'work_artifact')
            preview_id = None
            if preview is not None:
                preview_info = await self._create_file(
                    session, preview.path, preview.content, keys[1], 'work_preview', parent=info.id
                )
                preview_id = preview_info.id
            return await self.context.tasks.record_artifact(
                session,
                self.claim.resource_id,
                self.claim.id,
                self.claim.attempt_id,
                file=info,
                logical_path=item.path,
                validation=item.validation,
                preview_file_id=preview_id,
            )

    async def _snapshot(self) -> None:
        if self.session is None:
            return
        values = await self.session.snapshot_files()
        if sum(len(item.content) for item in values) > MAX_SNAPSHOT_BYTES:
            raise AppError('work_snapshot_too_large', '工作现场超过100MiB，请清理中间文件后继续', 422)
        previous = {str(item['path']): item for item in self.workspace_files}
        records = []
        for item in values:
            sandbox_path(item.path, roots=('working', 'outputs'))
            sha = hashlib.sha256(item.content).hexdigest()
            old = previous.get(item.path)
            if old is not None and old['sha256'] == sha:
                records.append(old)
                continue
            if not item.content:
                records.append({'path': item.path, 'empty': True, 'sha256': sha})
                continue
            info = await self._register(item.path, item.content, 'work_checkpoint')
            records.append({'path': item.path, 'file_id': str(info.id), 'sha256': info.sha256})
        self.workspace_files = records

    async def _publish(self, *, required: bool = True) -> dict[str, object]:
        sandbox = await self._sandbox()
        files = await sandbox.collect_outputs()
        if not files and not required:
            return {'success': True, 'data': {'artifacts': []}, 'error': None}
        existing = (await self._detail()).artifacts
        published: list[WorkArtifactInfo] = []
        rejected = []
        for item in files:
            sandbox_path(item.path, roots=('outputs',))
            if not item.content:
                rejected.append(
                    {'path': item.path, 'code': 'work_artifact_empty', 'message': '成果文件为空，请补充内容后重新发布'}
                )
                continue
            prior = next((old for old in existing if old.logical_path == item.path), None)
            if prior is not None and prior.sha256 == item.sha256 and prior.size == len(item.content):
                await self.context.queries.read(self.principal.personal(), prior.file_id)
                published.append(prior)
                continue
            self._mutation_pending = True
            published.append(await self._store_artifact(item))
        valid = not rejected and all(item.validation.get('valid') is True for item in published)
        self._failure('publication', failed=not valid or not published)
        return {
            'success': valid and bool(published),
            'data': {'artifacts': [item.model_dump(mode='json') for item in published], 'rejected': rejected},
            'error': None
            if valid and published
            else {'code': 'work_artifact_invalid', 'message': '没有可交付成果或有文件未通过检查，请修正后重新发布'},
        }

    async def invoke(self, name: str, arguments: dict[str, object], action_id: str) -> dict[str, object]:
        async with self._lock:
            return await self._invoke(name, arguments, action_id)

    async def _invoke(self, name: str, arguments: dict[str, object], action_id: str) -> dict[str, object]:
        self.principal = await self.service.guard(self.claim, code=name in CODE_TOOLS)
        await self._restore_workspace()
        cached = await self._cached_result(name, arguments, action_id)
        if cached is not None:
            return self._present(cached, action_id)
        if self.progress.tool_calls >= self.context.policy.tool_calls:
            raise AppError('work_budget_exhausted', '本轮工具调用预算已用完，可以继续任务', 409)
        self.progress.tool_calls += 1
        await self._record(name, arguments, action_id, status='running')
        await self.project(self.claim, self.progress)
        self._mutation_pending = False
        try:
            raw = await self._dispatch(name, arguments)
            result = normalize_work_fact(raw)
            if json.dumps(result, ensure_ascii=True) != json.dumps(raw, ensure_ascii=True):
                result['text_escaping'] = '工具文本中的控制字符以 Unicode 转义显示，文件内容保持原样'
            success = result.get('success') is not False
            await self._record(
                name,
                arguments,
                action_id,
                status='succeeded' if success else 'failed',
                result=self._confirmed_result(result),
            )
            self._mutation_pending = False
            await self.project(self.claim, self.progress)
            return self._present(result, action_id)
        except AppError as error:
            uncertain = self._mutation_pending or error.code in {
                'sandbox_execution_uncertain',
                'work_execution_uncertain',
                'work_step_uncertain',
            }
            if name in {'run_python', 'execute', 'write_file', 'publish_outputs'}:
                operation = (
                    'execution'
                    if name in {'run_python', 'execute'}
                    else 'publication'
                    if name == 'publish_outputs'
                    else 'write_file'
                )
                self._failure(operation, failed=True)
            failure: dict[str, object] = {'success': False, 'error': {'code': error.code, 'message': error.message}}
            await self._record(
                name,
                arguments,
                action_id,
                status='unknown' if uncertain else 'failed',
                summary=error.message[:2000],
                result=failure if uncertain else self._confirmed_result(failure),
            )
            if uncertain:
                raise AppError(
                    'work_execution_uncertain', '文件操作结果尚未完整确认，请检查执行记录和成果后继续', 409
                ) from error
            if error.code == 'sandbox_dependency_failed':
                return self._present(failure, action_id)
            raise

    async def _cached_result(
        self,
        name: str,
        arguments: dict[str, object],
        action_id: str,
    ) -> dict[str, object] | None:
        async with self.context.database.sessions() as session:
            cached = await self.context.tasks.cached_step(session, self.claim.resource_id, action_id)
        if cached is None:
            return None
        if cached.kind != 'tool' or cached.name != name or cached.details != self._tool_details(arguments):
            raise AppError('idempotency_conflict', '执行编号已用于其他工具或不同参数', 409)
        if cached.status in {'succeeded', 'completed', 'failed'} and cached.result is not None:
            data = cached.result.get('data')
            if name == 'write_todos' and isinstance(data, dict) and isinstance(data.get('todos'), list):
                self.progress.plan = tuple(
                    WorkPlanItem.model_validate(item).model_dump(mode='json') for item in data['todos']
                )
            return {key: value for key, value in cached.result.items() if not key.startswith('_workspace_')}
        if cached.status in {'running', 'unknown', 'cancelled', 'succeeded', 'completed', 'failed'}:
            raise AppError('work_execution_uncertain', '上次操作结果尚未确认，请检查成果后补充新的处理要求', 409)
        return None

    def _confirmed_result(self, result: dict[str, object]) -> dict[str, object]:
        self._workspace_sequence += 1
        return {
            **result,
            '_workspace_files': self.workspace_files,
            '_workspace_sequence': self._workspace_sequence,
            '_workspace_failures': sorted(self._failed_operations),
        }

    async def _record(
        self,
        name: str,
        arguments: dict[str, object],
        action_id: str,
        *,
        status: StepStatus,
        summary: str | None = None,
        result: dict[str, object] | None = None,
    ) -> None:
        async with self.context.database.sessions.begin() as session:
            await self.context.tasks.record_step(
                session,
                self.claim.resource_id,
                self.claim.id,
                self.claim.attempt_id,
                action_id=action_id,
                kind='tool',
                name=name,
                status=status,
                summary=str(
                    normalize_work_fact({'text': summary if summary is not None else self._label(name)})['text']
                ),
                details=self._tool_details(arguments),
                result=normalize_work_fact(result) if result is not None else None,
            )

    @staticmethod
    def _tool_details(arguments: dict[str, object]) -> dict[str, object]:
        normalized = normalize_work_fact(arguments)
        try:
            encoded = json.dumps(arguments, sort_keys=True, ensure_ascii=True, allow_nan=False).encode('ascii')
        except (TypeError, ValueError):
            raise AppError('invalid_work_fact', '工具参数必须是有效 JSON', 422) from None
        return {'arguments': normalized, 'arguments_fingerprint': hashlib.sha256(encoded).hexdigest()}

    @staticmethod
    def _present(result: dict[str, object], action_id: str) -> dict[str, object]:
        # Audit facts keep full runtime metadata; the model needs business output first.
        data = result.get('data')
        if isinstance(data, dict) and 'exit_code' in data and 'environment' in data:
            environment = data['environment']
            shown = {key: value for key, value in data.items() if key != 'environment'}
            if environment:
                shown['full_message_id'] = action_id
            result = {**result, 'data': shown}
        error = result.get('error')
        if isinstance(error, dict) and error.get('code') == 'sandbox_dependency_failed':
            message = str(error.get('message', '依赖准备失败'))
            if any(value in message.lower() for value in ('timed out', 'timeouterror', 'readtimeout')):
                message = '依赖下载超时，本次业务代码尚未执行。可稍后重试依赖准备；完整日志见执行记录。'
            else:
                lines = [line for line in message.splitlines() if line.strip()]
                message = '\n'.join(lines[-4:])[-MAX_DEPENDENCY_ERROR:]
            return {
                'success': False,
                'error': {**error, 'message': message},
                'data': {'phase': 'dependency_preparation', 'full_message_id': action_id},
            }
        encoded = json.dumps(result, ensure_ascii=False)
        if len(encoded) > MAX_TOOL_PREVIEW:
            return {
                'success': result.get('success') is not False,
                'data': {
                    'preview': encoded[:6000],
                    'full_message_id': action_id,
                    'read_more': '使用 read_history 按编号分页读取完整工具结果',
                },
                'error': result.get('error'),
            }
        return result

    async def _dispatch(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        handlers: dict[str, Callable[[dict[str, object]], Awaitable[dict[str, object]]]] = {
            'list_inputs': self._list_inputs,
            'inspect_file': self._inspect_file,
            'read_document': self._read_document,
            'read_sheet': self._read_sheet,
            'load_skill': self._load_skill,
            'request_input': self._request_input,
            'write_todos': self._write_todos,
            'read_history': self._read_history,
            'web_search': self._web_search,
            'run_python': self._run_python,
            'execute': self._execute,
            'write_file': self._write_file,
            'read_file': self._read_file,
            'list_files': self._list_files,
            'publish_outputs': self._publish_outputs,
        }
        handler = handlers.get(name)
        if handler is None:
            raise AppError('work_tool_unknown', '当前任务不支持该工具', 422)
        return await handler(arguments)

    @staticmethod
    def _success(data: object) -> dict[str, object]:
        return {'success': True, 'data': data, 'error': None}

    async def _web_search(self, arguments: dict[str, object]) -> dict[str, object]:
        try:
            request = WebSearchRequest.model_validate(arguments)
        except ValueError:
            raise AppError('invalid_web_search', '请输入有效的搜索关键词、时间范围和结果数量', 422) from None
        search = self.context.web_search
        if search is None:
            raise AppError('web_search_unavailable', '联网搜索尚未配置，请联系管理员', 503)
        result = await search.search(request)
        return {'success': True, 'data': result.model_dump(mode='json'), 'error': None}

    async def _list_inputs(self, _arguments: dict[str, object]) -> dict[str, object]:
        task = await self._detail()
        return self._success(
            {
                'goal': task.goal,
                'inputs': [
                    {**item.model_dump(mode='json'), 'workspace_path': f'inputs/{item.file_id.hex}/{item.name}'}
                    for item in task.inputs
                ],
                'artifacts': [item.model_dump(mode='json') for item in task.artifacts],
            }
        )

    @staticmethod
    def _file_result(info: FileInfo, path: str, data: dict[str, object]) -> dict[str, object]:
        return {
            'success': True,
            'data': {**data, 'file_id': str(info.id), 'workspace_path': path},
            'source': ['file:' + str(info.id)],
            'error': None,
        }

    async def _inspect_file(self, arguments: dict[str, object]) -> dict[str, object]:
        info, content, path = await self._file(str(arguments['file_id']))
        data = await asyncio.to_thread(self.context.reader.inspect, content, info.original_filename)
        return self._file_result(info, path, data)

    async def _read_document(self, arguments: dict[str, object]) -> dict[str, object]:
        info, content, path = await self._file(str(arguments['file_id']))
        data = await asyncio.to_thread(
            self.context.reader.read_document,
            content,
            info.original_filename,
            start=int(cast('int', arguments.get('start', 1))),
            limit=int(cast('int', arguments.get('limit', 40))),
        )
        return self._file_result(info, path, data)

    async def _read_sheet(self, arguments: dict[str, object]) -> dict[str, object]:
        info, content, path = await self._file(str(arguments['file_id']))
        data = await asyncio.to_thread(
            self.context.reader.read_sheet,
            content,
            info.original_filename,
            sheet=str(arguments.get('sheet', '')),
            start_row=int(cast('int', arguments.get('start_row', 1))),
            limit=int(cast('int', arguments.get('limit', 40))),
        )
        return self._file_result(info, path, data)

    async def _load_skill(self, arguments: dict[str, object]) -> dict[str, object]:
        requested = str(arguments['name'])
        selection = skill_selection(self.claim.payload)
        item = next((item for item in selection.revisions if item.slug == requested), None)
        if item is None:
            raise AppError('skill_unavailable', '请使用当前任务已选择的技能', 422)
        return self._success({'name': item.slug, 'instructions': item.content})

    async def _request_input(self, arguments: dict[str, object]) -> dict[str, object]:
        return self._success({'question': str(arguments['question'])})

    async def _write_todos(self, arguments: dict[str, object]) -> dict[str, object]:
        values = arguments.get('todos', [])
        if not isinstance(values, list):
            raise AppError('invalid_work_plan', '任务清单格式无效', 422)
        plan = tuple(WorkPlanItem.model_validate(item).model_dump(mode='json') for item in values)
        self.progress.plan = plan
        return self._success({'todos': list(plan)})

    async def _read_history(self, arguments: dict[str, object]) -> dict[str, object]:
        async with self.context.database.sessions() as session:
            step = await self.context.tasks.cached_step(session, self.claim.resource_id, str(arguments['message_id']))
        if step is None or step.result is None:
            raise AppError('work_step_not_found', '当前任务中没有该执行记录', 404)
        value = json.dumps(
            {key: value for key, value in step.result.items() if not key.startswith('_workspace_')}, ensure_ascii=False
        )
        offset = max(0, int(cast('int', arguments.get('offset', 0))))
        limit = max(1, min(MAX_TOOL_PREVIEW, int(cast('int', arguments.get('limit', 2000)))))
        return self._success(
            {
                'content': value[offset : offset + limit],
                'total_characters': len(value),
                'next_offset': offset + limit if offset + limit < len(value) else None,
            }
        )

    async def _run_python(self, arguments: dict[str, object]) -> dict[str, object]:
        return await self._command(arguments, python=True)

    async def _execute(self, arguments: dict[str, object]) -> dict[str, object]:
        return await self._command(arguments, python=False)

    async def _command(self, arguments: dict[str, object], *, python: bool) -> dict[str, object]:
        packages = arguments.get('packages', [])
        if not isinstance(packages, list) or any(not isinstance(item, str) for item in packages):
            raise AppError('invalid_dependencies', '依赖列表格式无效', 422)
        sandbox = await self._sandbox()
        command = str(arguments['code' if python else 'command'])
        outcome = await sandbox.execute(
            command,
            kind='python' if python else 'shell',
            timeout=int(cast('int', arguments.get('timeout', 45))),
            packages=tuple(packages),
        )
        self._mutation_pending = True
        self._failure('execution', failed=outcome.exit_code != 0)
        await self._snapshot()
        return {
            'success': outcome.exit_code == 0,
            'data': asdict(outcome),
            'error': None
            if outcome.exit_code == 0
            else {'code': 'command_failed', 'message': '请根据真实执行错误修正后重试'},
        }

    async def _write_file(self, arguments: dict[str, object]) -> dict[str, object]:
        sandbox = await self._sandbox()
        self._mutation_pending = True
        await sandbox.write_file(str(arguments['path']), str(arguments['content']))
        await self._snapshot()
        self._failure('write_file', failed=False)
        return self._success({'path': str(arguments['path'])})

    async def _read_file(self, arguments: dict[str, object]) -> dict[str, object]:
        content = await (await self._sandbox()).read_file(str(arguments['path']))
        return self._success({'content': content})

    async def _list_files(self, arguments: dict[str, object]) -> dict[str, object]:
        entries = await (await self._sandbox()).list_files(str(arguments.get('path', '.')))
        return self._success({'entries': list(entries)})

    async def _publish_outputs(self, _arguments: dict[str, object]) -> dict[str, object]:
        return await self._publish()

    @staticmethod
    def _label(name: str) -> str:
        return {
            'list_inputs': '查看任务资料',
            'inspect_file': '检查文件结构',
            'read_document': '读取文档',
            'read_sheet': '读取表格',
            'load_skill': '准备处理方法',
            'write_todos': '更新任务计划',
            'run_python': '处理数据与文件',
            'execute': '执行文件处理',
            'write_file': '整理工作文件',
            'read_file': '读取工作文件',
            'list_files': '查看工作目录',
            'publish_outputs': '检查并保存成果',
            'request_input': '等待补充信息',
            'read_history': '核对历史执行记录',
            'web_search': '搜索公开网页',
        }.get(name, '处理任务')

    async def finalize_outputs(self) -> None:
        self.principal = await self.service.guard(self.claim)
        await self._restore_workspace()
        code_enabled = has_capability(self.principal, 'work', 'execute_code')
        has_outputs = any(str(item.get('path', '')).startswith('outputs/') for item in self.workspace_files)
        if self.session is None and (not code_enabled or not has_outputs):
            return
        await self.service.guard(self.claim, code=True)
        await self._publish(required=False)

    async def close(self) -> None:
        if self.session is None:
            return
        sandbox, self.session = self.session, None
        error: str | None = None
        try:
            await sandbox.close()
        except Exception:
            error = '执行环境回收尚未确认，后台将继续清理'
        if self.lease_id is not None:
            async with self.context.database.sessions.begin() as session:
                await self.context.tasks.close_sandbox(
                    session,
                    self.lease_id,
                    self.claim.attempt_id,
                    status='cleanup_pending' if error else 'closed',
                    error=error,
                    sandbox_id=sandbox.id,
                )
