"""Owned task metadata and fenced execution facts for the workspace Agent."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import TypeVar, cast, get_args
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import BigInteger, case, delete, func, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.files.public import FileInfo
from zhiheng.modules.work.agent_contracts import (
    AgentTaskConfiguration,
    AgentTurn,
    CreateAgentTask,
    ExecutionState,
    StepStatus,
    UpdateAgentTask,
    WorkArtifactInfo,
    WorkInputInfo,
    WorkPlanItem,
    WorkSandboxInfo,
    WorkStepInfo,
    WorkWorkspaceState,
)
from zhiheng.modules.work.contracts import TaskDetail
from zhiheng.modules.work.facts import normalize_work_fact
from zhiheng.modules.work.facts import visible_work_text as _visible_text
from zhiheng.modules.work.orm import WorkArtifact, WorkInput, WorkMessage, WorkSandboxLease, WorkStep, WorkTask
from zhiheng.modules.work.service import WorkService
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

MAX_STEP_PAGE = 200
MAX_ARTIFACT_PAGE = 100
MAX_FACT_BYTES = 262144
MAX_ACTION_LENGTH = 200
MAX_KIND_LENGTH = 32
MAX_NAME_LENGTH = 100
MAX_SUMMARY_LENGTH = 4000
MAX_PATH_LENGTH = 500
MIN_ARTIFACT_PARTS = 2
MAX_CHECKPOINT_LENGTH = 128
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()
UNSAFE_PATH_CHARACTERS = re.compile(r'[\x00-\x1f\x7f-\x9f\ud800-\udfff]')
SUCCESSFUL_STEPS = frozenset({'completed', 'succeeded'})
EXECUTION_STATES = frozenset(get_args(ExecutionState))
T = TypeVar('T', bound=BaseModel)


def bounded_fact(value: dict[str, object]) -> dict[str, object]:
    value = normalize_work_fact(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError):
        raise AppError('invalid_work_fact', '执行记录必须是有效 JSON', 422) from None
    if len(encoded) > MAX_FACT_BYTES:
        raise AppError('work_fact_too_large', '执行记录过大，请保存为文件后引用', 422)
    return value


class WorkAgentStore:
    def __init__(self, work: WorkService | None = None) -> None:
        self.work = work or WorkService()

    async def _task(
        self, session: AsyncSession, principal: Principal, task_id: UUID, *, lock: bool = False
    ) -> WorkTask:
        row = await self.work._row(session, principal, task_id, lock=lock)
        if row.mode != 'agent':
            raise AppError('work_legacy_read_only', '历史任务只读，请新建任务继续处理', 409)
        return row

    async def configuration(
        self, session: AsyncSession, principal: Principal, task_id: UUID, *, lock: bool = False
    ) -> AgentTaskConfiguration:
        row = await self._task(session, principal, task_id, lock=lock)
        return AgentTaskConfiguration.model_validate(row.configuration)

    async def execution_memory(self, session: AsyncSession, principal: Principal, task_id: UUID) -> dict[str, object]:
        row = await self._task(session, principal, task_id)
        return {'checkpoint_id': row.agent_checkpoint_id, 'files': list(row.workspace_files)}

    async def save_execution_memory(
        self,
        session: AsyncSession,
        task_id: UUID,
        run_id: UUID,
        attempt_id: UUID,
        *,
        checkpoint_id: str | None,
        files: list[dict[str, object]] | None = None,
    ) -> None:
        row = await self._execution(session, task_id, run_id, attempt_id)
        if checkpoint_id is not None and (not checkpoint_id or len(checkpoint_id) > MAX_CHECKPOINT_LENGTH):
            raise AppError('invalid_work_checkpoint', '任务检查点标识无效', 422)
        if files is not None:
            normalized: list[dict[str, object]] = []
            paths = set()
            for item in files:
                path, file_id, sha = item.get('path'), item.get('file_id'), item.get('sha256')
                if not isinstance(path, str) or not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
                    raise AppError('invalid_work_snapshot', '任务工作文件快照无效', 422)
                relative = PurePosixPath(path)
                if (
                    relative.is_absolute()
                    or '..' in relative.parts
                    or '\\' in path
                    or UNSAFE_PATH_CHARACTERS.search(path)
                    or len(relative.parts) < MIN_ARTIFACT_PARTS
                    or relative.parts[0] not in {'working', 'outputs'}
                    or relative.as_posix() in paths
                    or len(path) > MAX_PATH_LENGTH
                ):
                    raise AppError('invalid_work_snapshot', '任务工作文件路径无效', 422)
                if item.get('empty') is True:
                    if sha != EMPTY_SHA256 or 'file_id' in item:
                        raise AppError('invalid_work_snapshot', '空文件快照无效', 422)
                    normalized.append({'path': relative.as_posix(), 'empty': True, 'sha256': sha})
                else:
                    try:
                        identifier = UUID(str(file_id))
                    except ValueError:
                        raise AppError('invalid_work_snapshot', '任务工作文件标识无效', 422) from None
                    normalized.append({'path': relative.as_posix(), 'file_id': str(identifier), 'sha256': sha})
                paths.add(relative.as_posix())
            bounded_fact({'files': normalized})
            row.workspace_files = normalized
        row.agent_checkpoint_id = checkpoint_id

    async def cached_step(self, session: AsyncSession, task_id: UUID, action_id: str) -> WorkStepInfo | None:
        """Trusted execution lookup after the application has checked task ownership."""
        row = await session.scalar(select(WorkStep).where(WorkStep.task_id == task_id, WorkStep.action_id == action_id))
        return self._value(WorkStepInfo, row) if row is not None else None

    async def latest_workspace_files(self, session: AsyncSession, task_id: UUID) -> WorkWorkspaceState | None:
        """Trusted execution lookup after ownership guard; cache replay never rewinds this watermark."""
        raw_sequence = WorkStep.result['_workspace_sequence'].astext
        sequence = case(
            (raw_sequence.op('~')(r'^(0|[1-9][0-9]{0,17})$'), sql_cast(raw_sequence, BigInteger)),
            else_=0,
        )
        row = await session.execute(
            select(WorkStep.result, sequence, WorkStep.run_id, WorkStep.name, WorkStep.status)
            .where(
                WorkStep.task_id == task_id,
                WorkStep.kind == 'tool',
                WorkStep.status.in_(('succeeded', 'completed', 'failed')),
                func.jsonb_typeof(WorkStep.result['_workspace_files']) == 'array',
            )
            .order_by(sequence.desc(), WorkStep.updated_at.desc(), WorkStep.id.desc())
            .limit(1)
        )
        selected = row.first()
        if selected is None:
            return None
        fact, number, run_id, name, status = selected
        if not isinstance(fact, dict):
            raise AppError('invalid_work_snapshot', '最近的任务工作文件快照无效', 409)
        files = fact.get('_workspace_files')
        if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
            raise AppError('invalid_work_snapshot', '最近的任务工作文件快照无效', 409)
        if '_workspace_failures' in fact:
            failures = fact['_workspace_failures']
            if not isinstance(failures, list) or any(not isinstance(item, str) for item in failures):
                raise AppError('invalid_work_snapshot', '最近的任务执行状态无效', 409)
        else:
            operation = {
                'run_python': 'execution',
                'execute': 'execution',
                'write_file': 'write_file',
                'publish_outputs': 'publication',
            }.get(name)
            failures = [operation] if status == 'failed' and operation is not None else []
        last_user = await session.scalar(
            select(func.max(WorkMessage.sequence)).where(
                WorkMessage.task_id == task_id,
                WorkMessage.role == 'user',
            )
        )
        step_turn = await session.scalar(
            select(WorkMessage.sequence).where(
                WorkMessage.task_id == task_id,
                WorkMessage.run_id == run_id,
                WorkMessage.role == 'assistant',
            )
        )
        if last_user is not None and step_turn is not None and last_user > step_turn:
            failures = []
        return WorkWorkspaceState(
            sequence=int(number), files=[dict(item) for item in files], failed_operations=list(failures), run_id=run_id
        )

    async def due_checkpoint_cleanup(self, session: AsyncSession, limit: int = 20) -> tuple[UUID, ...]:
        if limit < 1 or limit > MAX_STEP_PAGE:
            raise ValueError('Invalid checkpoint cleanup batch size')
        return tuple(
            await session.scalars(
                select(WorkTask.id)
                .where(
                    WorkTask.mode == 'agent',
                    WorkTask.deleted_at.is_not(None),
                    WorkTask.current_run_id.is_(None),
                    WorkTask.checkpoint_cleanup_after <= utcnow(),
                )
                .order_by(WorkTask.checkpoint_cleanup_after, WorkTask.id)
                .limit(limit)
            )
        )

    async def finish_checkpoint_cleanup(self, session: AsyncSession, task_id: UUID) -> None:
        row = await session.scalar(
            select(WorkTask).where(WorkTask.id == task_id).with_for_update().execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.mode != 'agent'
            or row.deleted_at is None
            or row.current_run_id is not None
            or row.checkpoint_cleanup_after is None
            or row.checkpoint_cleanup_after > utcnow()
        ):
            return
        row.agent_checkpoint_id = None
        row.workspace_files = []
        row.checkpoint_cleanup_after = None

    async def create(
        self, session: AsyncSession, principal: Principal, body: CreateAgentTask, fingerprint: str
    ) -> TaskDetail:
        existing = await self.work.existing_create(session, principal.id, body.client_request_id, fingerprint)
        if existing is not None:
            return await self.detail(session, principal, existing)
        configuration = AgentTaskConfiguration.model_validate(
            body.model_dump(include=set(AgentTaskConfiguration.model_fields))
        )
        row = WorkTask(
            owner_id=principal.id,
            mode='agent',
            goal=body.goal,
            title=' '.join(body.goal.split())[:40] or '新任务',
            configuration=configuration.model_dump(mode='json'),
            execution_state='idle',
            plan=[],
            create_client_request_id=body.client_request_id,
            create_fingerprint=fingerprint,
            active_agent_id=None,
            create_active_agent_id=None,
        )
        session.add(row)
        await session.flush()
        if body.goal:
            session.add(
                WorkMessage(
                    task_id=row.id, client_request_id=body.client_request_id, sequence=1, role='user', content=body.goal
                )
            )
        await session.flush()
        return await self.detail(session, principal, row.id)

    async def detail(self, session: AsyncSession, principal: Principal, task_id: UUID) -> TaskDetail:
        row = await self._task(session, principal, task_id)
        detail = await self.work.detail(session, principal, task_id)
        return detail.model_copy(
            update={
                'configuration': AgentTaskConfiguration.model_validate(row.configuration),
                'inputs': await self.inputs(session, principal, task_id),
                'steps': await self.steps(session, principal, task_id),
                'artifacts': await self.artifacts(session, principal, task_id),
                'plan': [WorkPlanItem.model_validate(item) for item in row.plan],
            }
        )

    async def update(
        self, session: AsyncSession, principal: Principal, task_id: UUID, body: UpdateAgentTask
    ) -> TaskDetail:
        row = await self._task(session, principal, task_id)
        row = await self.work.editable(session, principal, task_id, body.expected_version, allow_completed=True)
        changes = body.model_fields_set - {'expected_version'}
        configuration = dict(row.configuration)
        for key in changes:
            if key in AgentTaskConfiguration.model_fields:
                value = getattr(body, key)
                configuration[key] = str(value) if isinstance(value, UUID) else value
            else:
                setattr(row, key, getattr(body, key))
        row.configuration = AgentTaskConfiguration.model_validate(configuration).model_dump(mode='json')
        self.work.touch(row)
        await session.flush()
        return await self.detail(session, principal, task_id)

    async def inputs(self, session: AsyncSession, principal: Principal, task_id: UUID) -> list[WorkInputInfo]:
        await self._task(session, principal, task_id)
        rows = await session.scalars(
            select(WorkInput).where(WorkInput.task_id == task_id).order_by(WorkInput.created_at)
        )
        return [self._value(WorkInputInfo, row) for row in rows]

    async def attach_input(
        self, session: AsyncSession, principal: Principal, task_id: UUID, file: FileInfo
    ) -> WorkInputInfo:
        task = await self._task(session, principal, task_id, lock=True)
        if task.current_run_id is not None or task.status != 'active':
            raise AppError('work_busy', '任务执行期间不能修改输入文件', 409)
        existing = await session.get(WorkInput, (task_id, file.id))
        if existing is not None:
            return self._value(WorkInputInfo, existing)
        version = (
            await session.scalar(
                select(func.max(WorkInput.version)).where(
                    WorkInput.task_id == task_id, WorkInput.name == file.original_filename
                )
            )
            or 0
        )
        row = WorkInput(
            task_id=task_id,
            file_id=file.id,
            name=file.original_filename,
            version=version + 1,
            size=file.size,
            mime=file.mime,
            sha256=file.sha256,
            file_version=file.version,
            acl_version=file.acl_version,
        )
        session.add(row)
        self.work.touch(task)
        await session.flush()
        return self._value(WorkInputInfo, row)

    async def detach_input(
        self, session: AsyncSession, principal: Principal, task_id: UUID, file_id: UUID, expected_version: int
    ) -> TaskDetail:
        await self._task(session, principal, task_id)
        task = await self.work.editable(session, principal, task_id, expected_version)
        if await self.has_consumed_input(session, principal, task_id, file_id):
            raise AppError('work_input_referenced', '文件已用于任务历史，不能移除', 409)
        await session.execute(delete(WorkInput).where(WorkInput.task_id == task_id, WorkInput.file_id == file_id))
        self.work.touch(task)
        return await self.detail(session, principal, task_id)

    async def has_consumed_input(
        self, session: AsyncSession, principal: Principal, task_id: UUID, file_id: UUID
    ) -> bool:
        await self._task(session, principal, task_id)
        return (
            await session.scalar(
                select(Run.id)
                .where(
                    Run.resource_type == 'work_agent',
                    Run.resource_id == task_id,
                    Run.owner_id == principal.id,
                    Run.payload['inputs'].contains([{'file_id': str(file_id)}]),
                )
                .limit(1)
            )
            is not None
        )

    async def admit(
        self,
        session: AsyncSession,
        principal: Principal,
        task_id: UUID,
        body: AgentTurn,
        run: RunSnapshot,
        fingerprint: str,
    ) -> None:
        await self._task(session, principal, task_id)
        task = await self.work.editable(session, principal, task_id, body.expected_version)
        if run.resource_id != task_id or run.resource_type != 'work_agent':
            raise AppError('invalid_work_run', '运行不属于当前任务', 409)
        if body.resume and task.execution_state not in {'paused', 'failed', 'cancelled', 'waiting_input'}:
            raise AppError('work_not_paused', '当前任务没有可继续的执行', 409)
        last = await session.scalar(select(func.max(WorkMessage.sequence)).where(WorkMessage.task_id == task_id)) or 0
        initial = await session.scalar(
            select(WorkMessage).where(
                WorkMessage.task_id == task_id,
                WorkMessage.client_request_id == body.client_request_id,
                WorkMessage.role == 'user',
            )
        )
        if initial is not None:
            if last != 1 or initial.content != body.message or body.resume:
                raise AppError('idempotency_conflict', '首轮目标已过期或内容不同', 409)
            initial.run_id = run.id
        elif not body.resume:
            last += 1
            session.add(
                WorkMessage(
                    task_id=task_id,
                    client_request_id=body.client_request_id,
                    sequence=last,
                    role='user',
                    content=body.message,
                    run_id=run.id,
                )
            )
        session.add(
            WorkMessage(
                task_id=task_id,
                client_request_id=body.client_request_id,
                sequence=last + 1,
                role='assistant',
                content='',
                status='pending',
                run_id=run.id,
                fingerprint=fingerprint,
                audit={'configuration': dict(task.configuration)},
            )
        )
        if not task.goal and body.message:
            task.goal = body.message
            task.title = ' '.join(body.message.split())[:40]
        task.current_run_id = run.id
        task.execution_state = 'queued'
        self.work.touch(task)
        await session.flush()

    async def project(  # noqa: PLR0913 - project one complete, fenced execution observation
        self,
        session: AsyncSession,
        task_id: UUID,
        run_id: UUID,
        attempt_id: UUID | None,
        *,
        content: str,
        status: str,
        usage: dict[str, object],
        error: dict[str, object] | None = None,
        terminal: bool = False,
        execution_state: ExecutionState | None = None,
        plan: list[WorkPlanItem] | None = None,
    ) -> None:
        run = await session.scalar(
            select(Run).where(Run.id == run_id).with_for_update().execution_options(populate_existing=True)
        )
        if run is None or run.attempt_id != attempt_id:
            return
        if terminal:
            if run.status not in TERMINAL_STATUSES or run.status != status or run.text != content:
                return
        elif attempt_id is None or run.status != 'running' or run.lease_until is None or run.lease_until <= utcnow():
            raise AppError('work_execution_lost', '任务执行权已失效', 409)
        task = await session.scalar(
            select(WorkTask).where(WorkTask.id == task_id).with_for_update().execution_options(populate_existing=True)
        )
        if task is None or task.current_run_id != run_id or task.mode != 'agent':
            return
        if plan is not None:
            task.plan = [item.model_dump(mode='json') for item in plan]
        if execution_state is not None:
            task.execution_state = execution_state
        elif terminal:
            task.execution_state = {'interrupted': 'paused', 'pending': 'queued'}.get(status, status)
        else:
            task.execution_state = 'running'
        await self.work.project(
            session,
            task_id,
            run_id,
            content=content,
            reasoning='',
            status=status,
            usage=usage,
            error=error,
            terminal=terminal,
        )

    async def project_snapshot(self, session: AsyncSession, snapshot: RunSnapshot) -> None:
        """Project terminal native facts, including cancellation before the first claim."""
        if snapshot.resource_type != 'work_agent' or snapshot.status not in TERMINAL_STATUSES:
            return
        run = await session.scalar(
            select(Run).where(Run.id == snapshot.id).with_for_update().execution_options(populate_existing=True)
        )
        if (
            run is None
            or run.resource_id != snapshot.resource_id
            or run.resource_type != 'work_agent'
            or run.attempt_id != snapshot.attempt_id
            or run.status != snapshot.status
            or run.revision != snapshot.revision
            or run.text != snapshot.text
        ):
            return
        task = await session.scalar(
            select(WorkTask)
            .where(WorkTask.id == snapshot.resource_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is None or task.mode != 'agent':
            return
        # Financial facts can settle after this round released the task or a later round started.
        # Repair only the matching historical message; current execution/plan/head remain untouched.
        await session.execute(
            update(WorkMessage)
            .where(
                WorkMessage.task_id == snapshot.resource_id,
                WorkMessage.run_id == snapshot.id,
                WorkMessage.role == 'assistant',
            )
            .values(usage=snapshot.usage.model_dump(mode='json'))
        )
        await self.project(
            session,
            snapshot.resource_id,
            snapshot.id,
            snapshot.attempt_id,
            content=snapshot.text,
            status=snapshot.status,
            usage=snapshot.usage.model_dump(mode='json'),
            error=snapshot.error.model_dump(mode='json') if snapshot.error else None,
            terminal=True,
            execution_state=cast('ExecutionState', (run.result or {}).get('execution_state'))
            if (run.result or {}).get('execution_state') in EXECUTION_STATES
            else None,
        )
        unfinished = await session.scalars(
            select(WorkStep)
            .where(
                WorkStep.run_id == snapshot.id,
                WorkStep.attempt_id == snapshot.attempt_id,
                WorkStep.status.in_(('pending', 'running')),
            )
            .with_for_update()
        )
        for step in unfinished:
            step.status = 'cancelled' if snapshot.status == 'cancelled' or step.status == 'pending' else 'unknown'
            step.summary = '运行已停止' if step.status == 'cancelled' else '执行结果尚未确认，请检查后继续'
            step.updated_at = utcnow()

    async def steps(self, session: AsyncSession, principal: Principal, task_id: UUID) -> list[WorkStepInfo]:
        await self._task(session, principal, task_id)
        rows = list(
            await session.scalars(
                select(WorkStep)
                .where(WorkStep.task_id == task_id)
                .order_by(WorkStep.created_at.desc(), WorkStep.id.desc())
                .limit(MAX_STEP_PAGE)
            )
        )
        return [self._value(WorkStepInfo, row) for row in reversed(rows)]

    async def _execution(
        self, session: AsyncSession, task_id: UUID, run_id: UUID, attempt_id: UUID, *, allow_cancel: bool = False
    ) -> WorkTask:
        run = await session.scalar(
            select(Run).where(Run.id == run_id).with_for_update().execution_options(populate_existing=True)
        )
        if (
            run is None
            or run.resource_id != task_id
            or run.resource_type != 'work_agent'
            or run.attempt_id != attempt_id
            or run.status not in ({'running', 'cancel_requested'} if allow_cancel else {'running'})
            or run.lease_until is None
            or run.lease_until <= utcnow()
        ):
            raise AppError('work_execution_lost', '任务执行权已失效', 409)
        task = await session.scalar(
            select(WorkTask).where(WorkTask.id == task_id).with_for_update().execution_options(populate_existing=True)
        )
        if task is None or task.mode != 'agent' or task.current_run_id != run_id or task.deleted_at is not None:
            raise AppError('work_execution_lost', '任务执行权已失效', 409)
        return task

    async def record_step(  # noqa: PLR0913 - persist one complete, fenced tool execution fact
        self,
        session: AsyncSession,
        task_id: UUID,
        run_id: UUID,
        attempt_id: UUID,
        *,
        action_id: str,
        kind: str,
        name: str,
        status: StepStatus,
        summary: str = '',
        details: dict[str, object] | None = None,
        result: dict[str, object] | None = None,
    ) -> WorkStepInfo:
        await self._execution(session, task_id, run_id, attempt_id, allow_cancel=status == 'cancelled')
        name, kind, summary = _visible_text(name), _visible_text(kind), _visible_text(summary)
        if (
            not action_id
            or len(action_id) > MAX_ACTION_LENGTH
            or len(kind) > MAX_KIND_LENGTH
            or len(name) > MAX_NAME_LENGTH
            or len(summary) > MAX_SUMMARY_LENGTH
        ):
            raise AppError('invalid_work_step', '执行步骤标识或说明过长', 422)
        details = bounded_fact(details or {})
        result = bounded_fact(result) if result is not None else None
        row = await session.scalar(select(WorkStep).where(WorkStep.task_id == task_id, WorkStep.action_id == action_id))
        if row is not None:
            if row.kind != kind or row.name != name:
                raise AppError('idempotency_conflict', '执行编号已用于其他操作', 409)
            if row.status in SUCCESSFUL_STEPS:
                return self._value(WorkStepInfo, row)
            if row.status == 'unknown' and status == 'running':
                raise AppError('work_step_uncertain', '上次操作结果尚未确认，请先核查执行记录', 409)
            row.status, row.summary, row.details, row.result = status, summary, details, result
            row.run_id, row.attempt_id, row.updated_at = run_id, attempt_id, utcnow()
        else:
            row = WorkStep(
                task_id=task_id,
                run_id=run_id,
                attempt_id=attempt_id,
                action_id=action_id,
                kind=kind,
                name=name,
                status=status,
                summary=summary,
                details=details,
                result=result,
            )
            session.add(row)
        await session.flush()
        return self._value(WorkStepInfo, row)

    async def artifacts(self, session: AsyncSession, principal: Principal, task_id: UUID) -> list[WorkArtifactInfo]:
        await self._task(session, principal, task_id)
        rows = await session.scalars(
            select(WorkArtifact)
            .where(WorkArtifact.task_id == task_id)
            .order_by(WorkArtifact.created_at.desc(), WorkArtifact.id.desc())
            .limit(MAX_ARTIFACT_PAGE)
        )
        return [self._artifact(row) for row in rows]

    async def artifact(
        self, session: AsyncSession, principal: Principal, task_id: UUID, artifact_id: UUID
    ) -> WorkArtifactInfo:
        await self._task(session, principal, task_id)
        row = await session.scalar(
            select(WorkArtifact).where(WorkArtifact.task_id == task_id, WorkArtifact.id == artifact_id)
        )
        if row is None:
            raise AppError('work_artifact_not_found', '成果不存在', 404)
        return self._artifact(row)

    async def record_artifact(
        self,
        session: AsyncSession,
        task_id: UUID,
        run_id: UUID,
        attempt_id: UUID,
        *,
        file: FileInfo,
        logical_path: str,
        validation: dict[str, object],
        preview_file_id: UUID | None = None,
    ) -> WorkArtifactInfo:
        await self._execution(session, task_id, run_id, attempt_id)
        if file.size <= 0:
            raise AppError('work_artifact_invalid', '空文件不能登记为交付成果', 422)
        path = PurePosixPath(logical_path)
        if (
            path.is_absolute()
            or '..' in path.parts
            or '\\' in logical_path
            or UNSAFE_PATH_CHARACTERS.search(logical_path)
            or not path.parts
            or path.parts[0] != 'outputs'
            or len(path.parts) < MIN_ARTIFACT_PARTS
            or len(logical_path) > MAX_PATH_LENGTH
        ):
            raise AppError('invalid_work_artifact_path', '成果必须来自 outputs 目录', 422)
        logical_path = path.as_posix()
        previous = await session.scalar(
            select(WorkArtifact)
            .where(WorkArtifact.task_id == task_id, WorkArtifact.logical_path == logical_path)
            .order_by(WorkArtifact.version.desc())
            .limit(1)
        )
        if previous is not None and previous.sha256 == file.sha256 and previous.size == file.size:
            return self._artifact(previous)
        row = WorkArtifact(
            task_id=task_id,
            run_id=run_id,
            file_id=file.id,
            preview_file_id=preview_file_id,
            logical_path=logical_path,
            name=file.original_filename,
            version=previous.version + 1 if previous is not None else 1,
            size=file.size,
            mime=file.mime,
            sha256=file.sha256,
            validation=bounded_fact(validation),
        )
        session.add(row)
        await session.flush()
        return self._artifact(row)

    async def reserve_sandbox(
        self, session: AsyncSession, task_id: UUID, run_id: UUID, attempt_id: UUID, *, image: str, expires_at: datetime
    ) -> WorkSandboxInfo:
        await self._execution(session, task_id, run_id, attempt_id)
        if not image or len(image) > MAX_PATH_LENGTH or expires_at <= utcnow():
            raise AppError('invalid_work_sandbox', '执行环境配置无效', 422)
        row = WorkSandboxLease(
            task_id=task_id, run_id=run_id, attempt_id=attempt_id, image=image, expires_at=expires_at, status='creating'
        )
        session.add(row)
        await session.flush()
        return self._value(WorkSandboxInfo, row)

    async def bind_sandbox(
        self, session: AsyncSession, lease_id: UUID, attempt_id: UUID, sandbox_id: str
    ) -> WorkSandboxInfo:
        row = await session.get(WorkSandboxLease, lease_id)
        if row is None or row.attempt_id != attempt_id or row.status != 'creating':
            raise AppError('work_execution_lost', '任务执行权已失效', 409)
        await self._execution(session, row.task_id, row.run_id, attempt_id)
        row = await session.scalar(
            select(WorkSandboxLease)
            .where(WorkSandboxLease.id == lease_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.attempt_id != attempt_id or row.status != 'creating':
            raise AppError('work_execution_lost', '任务执行权已失效', 409)
        if not sandbox_id or len(sandbox_id) > MAX_ACTION_LENGTH:
            raise AppError('invalid_work_sandbox', '执行环境标识无效', 422)
        row.sandbox_id, row.status, row.updated_at = sandbox_id, 'active', utcnow()
        await session.flush()
        return self._value(WorkSandboxInfo, row)

    async def close_sandbox(
        self,
        session: AsyncSession,
        lease_id: UUID,
        attempt_id: UUID,
        *,
        status: str = 'closed',
        error: str | None = None,
        sandbox_id: str | None = None,
    ) -> None:
        row = await session.scalar(
            select(WorkSandboxLease)
            .where(WorkSandboxLease.id == lease_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.attempt_id != attempt_id:
            raise AppError('work_execution_lost', '任务执行权已失效', 409)
        if status not in {'closed', 'cleanup_pending'}:
            raise ValueError('Invalid sandbox cleanup status')
        if sandbox_id is not None:
            if not sandbox_id or len(sandbox_id) > MAX_ACTION_LENGTH or row.sandbox_id not in {None, sandbox_id}:
                raise AppError('work_sandbox_conflict', '执行环境标识与登记不一致', 409)
            row.sandbox_id = sandbox_id
        row.status, row.error, row.updated_at = status, _visible_text(error)[:500] if error else None, utcnow()

    async def sandbox_leases(
        self, session: AsyncSession, *, expired_before: datetime | None = None
    ) -> list[WorkSandboxInfo]:
        query = select(WorkSandboxLease).where(WorkSandboxLease.status != 'closed')
        if expired_before is not None:
            query = query.where(WorkSandboxLease.expires_at <= expired_before)
        rows = await session.scalars(query.order_by(WorkSandboxLease.expires_at).limit(MAX_STEP_PAGE))
        return [self._value(WorkSandboxInfo, row) for row in rows]

    @staticmethod
    def _artifact(row: WorkArtifact) -> WorkArtifactInfo:
        prefix = f'/api/work/tasks/{row.task_id}/artifacts/{row.id}'
        return WorkArtifactInfo.model_validate(
            {
                **{
                    key: getattr(row, key) for key in WorkArtifactInfo.model_fields if key not in {'url', 'preview_url'}
                },
                'url': prefix + '/download',
                'preview_url': prefix + '/preview' if row.preview_file_id is not None else None,
            }
        )

    @staticmethod
    def _value(model: type[T], row: object) -> T:
        return model.model_validate({key: getattr(row, key) for key in model.model_fields})
