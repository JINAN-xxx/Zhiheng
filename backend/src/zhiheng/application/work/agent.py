"""Goal-driven workspace admission reuses native ownership, files and execution services."""

from contextlib import suppress
from datetime import timedelta
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.work.context import WorkAgentContext
from zhiheng.application.work.execution import WorkAgentExecutor
from zhiheng.application.work.snapshots import fingerprint, frozen_skills, skill_selection
from zhiheng.modules.files.public import FileInfo, validate_filename
from zhiheng.modules.models.public import ModelBinding, ModelRequest
from zhiheng.modules.skills.public import (
    SkillSelection,
    require_frozen_available,
    resolve_selection,
)
from zhiheng.modules.work.public import (
    AgentTaskConfiguration,
    AgentTurn,
    CreateAgentTask,
    TaskDetail,
    UpdateAgentTask,
    WorkModelOption,
    WorkOptions,
    WorkSkillOption,
)
from zhiheng.runtime.contracts import ClaimedRun, RunSnapshot
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import has_capability, require_capability
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

MAX_FILE_BYTES = 20 * 1024 * 1024


class WorkAgentService:
    def __init__(self, context: WorkAgentContext) -> None:
        self.context = context

    async def execute(self, run_id: UUID) -> None:
        await WorkAgentExecutor(self).execute(run_id)

    async def reconcile(self) -> None:
        async with self.context.database.sessions() as session:
            children = await self.context.runs.orphaned_children(session, resource_type='work_agent_model')
        for child in children:
            # Release each child's execution and billing locks before touching another Run.
            async with self.context.database.sessions.begin() as session:
                _, release = await self.context.runs.cancel(session, child.id, child.owner_id)
                if release:
                    await self.context.usage.settle(session, child.id, rejected_before_execution=True)
        async with self.context.database.sessions() as session:
            parents = await self.context.runs.pending_parent_usage(session, resource_type='work_agent')
        for parent_id in parents:
            # Parent-first projection uses plain child reads; never acquire a parent under child locks.
            async with self.context.database.sessions.begin() as session:
                snapshot = await self.context.runs.project_child_usage(session, parent_id, pending_only=True)
                await self.context.tasks.project_snapshot(session, snapshot)
        async with self.context.database.sessions() as session:
            leases = await self.context.tasks.sandbox_leases(session)
        for lease in leases:
            async with self.context.database.sessions() as session:
                run = await self.context.runs.projection(session, lease.run_id)
            if run.status in {'pending', 'running', 'cancel_requested'} and lease.expires_at > utcnow():
                continue
            if lease.sandbox_id is None and lease.expires_at > utcnow():
                continue
            try:
                if lease.sandbox_id is not None:
                    await self.context.sandbox.kill(lease.sandbox_id)
                async with self.context.database.sessions.begin() as session:
                    await self.context.tasks.close_sandbox(session, lease.id, lease.attempt_id)
            except Exception:
                continue
        if self.context.checkpoint_cleanup is not None:
            async with self.context.database.sessions() as session:
                tasks = await self.context.tasks.due_checkpoint_cleanup(session)
            for task_id in tasks:
                with suppress(Exception):
                    await self.context.checkpoint_cleanup(str(task_id))
                    async with self.context.database.sessions.begin() as session:
                        await self.context.tasks.finish_checkpoint_cleanup(session, task_id)

    async def current(self, session: AsyncSession, principal: Principal, action: str = 'use') -> Principal:
        current = await self.context.identity.principal_for_user(session, principal.id)
        require_capability(current, 'work', action)
        return current

    async def _configuration(
        self, session: AsyncSession, principal: Principal, configuration: AgentTaskConfiguration
    ) -> tuple[ModelBinding, SkillSelection]:
        binding = await self.context.models.bindings.resolve(session, configuration.model_id)
        if binding.model_type != 'chat':
            raise AppError('work_model_invalid', '请选择聊天模型', 422)
        if configuration.skill_slugs:
            require_capability(principal, 'skills', 'use')
        selection = await resolve_selection(session, principal.id, configuration.skill_slugs)
        return binding, selection

    async def options(self, principal: Principal) -> WorkOptions:
        ready = await self.context.sandbox.check()
        async with self.context.database.sessions() as session:
            principal = await self.current(session, principal, 'read')
            models = await self.context.models.catalog.list(session)
            skills = (
                await self.context.skills.available(session, principal.id)
                if has_capability(principal, 'skills', 'use')
                else []
            )
        return WorkOptions(
            models=[
                WorkModelOption(id=model.id, name=model.name, max_output_tokens=model.max_output_tokens)
                for model in models
                if model.model_type == 'chat'
            ],
            skills=[
                WorkSkillOption(slug=skill.slug, name=skill.name, description=skill.description, version=skill.version)
                for skill in skills
            ],
            can_execute_code=has_capability(principal, 'work', 'execute_code'),
            sandbox_available=ready,
        )

    async def create(self, principal: Principal, body: CreateAgentTask) -> TaskDetail:
        async with self.context.database.sessions.begin() as session:
            principal = await self.current(session, principal, 'create')
            await self.context.runs.lock_request(session, principal.id, body.client_request_id)
            await self._configuration(session, principal, body)
            task = await self.context.tasks.create(session, principal, body, fingerprint(body.model_dump(mode='json')))
            for file_id in body.file_ids:
                info = await self.context.files.get(session, principal.personal(), file_id)
                await self.context.tasks.attach_input(session, principal, task.id, info)
                await self.bind_file(session, principal, task.id, info)
            return await self.context.tasks.detail(session, principal, task.id)

    async def detail(self, principal: Principal, task_id: UUID) -> TaskDetail:
        async with self.context.database.sessions() as session:
            principal = await self.current(session, principal, 'read')
            return await self.context.tasks.detail(session, principal, task_id)

    async def update(self, principal: Principal, task_id: UUID, body: UpdateAgentTask) -> TaskDetail:
        async with self.context.database.sessions.begin() as session:
            principal = await self.current(session, principal, 'update')
            current = await self.context.tasks.configuration(session, principal, task_id)
            fields = body.model_dump(exclude_unset=True, include=set(AgentTaskConfiguration.model_fields))
            candidate = AgentTaskConfiguration.model_validate({**current.model_dump(), **fields})
            await self._configuration(session, principal, candidate)
            return await self.context.tasks.update(session, principal, task_id, body)

    async def remove(self, principal: Principal, task_id: UUID, expected_version: int) -> None:
        async with self.context.database.sessions.begin() as session:
            principal = await self.current(session, principal, 'delete')
            await self.context.tasks.configuration(session, principal, task_id)
            await self.context.tasks.work.remove(session, principal, task_id, expected_version)
            await self.context.files.references.release_resources(
                session, resource_type='work_agent_task', resource_ids=(task_id,), actor_id=principal.id
            )

    async def bind_file(self, session: AsyncSession, principal: Principal, task_id: UUID, info: FileInfo) -> None:
        snapshot = await self.context.files.source_snapshot(session, principal.personal(), info.id)
        await self.context.files.references.bind_sources(
            session, principal.personal(), (snapshot,), resource_type='work_agent_task', resource_id=task_id
        )

    async def upload(self, principal: Principal, task_id: UUID, filename: str, content: bytes) -> TaskDetail:
        validate_filename(filename)
        if not content or len(content) > MAX_FILE_BYTES:
            raise AppError('invalid_file_size', '文件为空或超过20MiB', 422)
        async with self.context.database.sessions() as session:
            principal = await self.current(session, principal)
            await self.context.tasks.configuration(session, principal, task_id)
        key = await self.context.files.storage.put(content)
        async with self.context.objects.registration(key) as session:
            principal = await self.current(session, principal)
            await self.context.tasks.configuration(session, principal, task_id, lock=True)
            info = await self.context.files.create(
                session,
                principal.personal(),
                filename=filename,
                content=content,
                storage_key=key,
                purpose='work_attachment',
            )
            await self.context.tasks.attach_input(session, principal, task_id, info)
            await self.bind_file(session, principal, task_id, info)
            return await self.context.tasks.detail(session, principal, task_id)

    async def detach_input(
        self, principal: Principal, task_id: UUID, file_id: UUID, expected_version: int
    ) -> TaskDetail:
        async with self.context.database.sessions.begin() as session:
            principal = await self.current(session, principal, 'update')
            return await self.context.tasks.detach_input(session, principal, task_id, file_id, expected_version)

    async def download_input(self, principal: Principal, task_id: UUID, file_id: UUID) -> tuple[FileInfo, bytes]:
        async with self.context.database.sessions() as session:
            principal = await self.current(session, principal, 'read')
            items = await self.context.tasks.inputs(session, principal, task_id)
            if not any(item.file_id == file_id for item in items):
                raise AppError('file_not_found', '任务文件不存在', 404)
        result = await self.context.queries.read(principal.personal(), file_id)
        async with self.context.database.sessions() as session:
            await self.current(session, principal, 'read')
            items = await self.context.tasks.inputs(session, principal, task_id)
            if not any(item.file_id == file_id for item in items):
                raise AppError('file_not_found', '任务文件不存在', 404)
        return result

    async def download_artifact(
        self, principal: Principal, task_id: UUID, artifact_id: UUID, *, preview: bool = False
    ) -> tuple[FileInfo, bytes]:
        async with self.context.database.sessions() as session:
            principal = await self.current(session, principal, 'read')
            artifact = await self.context.tasks.artifact(session, principal, task_id, artifact_id)
            file_id = artifact.preview_file_id if preview and artifact.preview_file_id else artifact.file_id
        result = await self.context.queries.read(principal.personal(), file_id)
        async with self.context.database.sessions() as session:
            await self.current(session, principal, 'read')
            await self.context.tasks.artifact(session, principal, task_id, artifact_id)
        return result

    async def admit(self, principal: Principal, task_id: UUID, body: AgentTurn) -> RunSnapshot:
        intent = fingerprint({'task_id': str(task_id), **body.model_dump(mode='json')})
        async with self.context.database.sessions.begin() as session:
            principal = await self.current(session, principal)
            await self.context.tasks.configuration(session, principal, task_id)
            await self.context.runs.lock_request(session, principal.id, body.client_request_id)
            existing = await self.context.runs.existing_request(session, principal.id, body.client_request_id, intent)
            if existing is not None:
                if existing.resource_type != 'work_agent' or existing.resource_id != task_id:
                    raise AppError('idempotency_conflict', '请求编号已用于其他操作', 409)
                return existing
            await self.context.runs.lock_request(session, principal.id, uuid5(principal.id, 'workspace-active'))
            if await self.context.runs.owner_active(session, principal.id, 'work_agent'):
                raise AppError('work_busy', '你已有一个任务正在执行，请等待完成或先停止', 409)
            task = await self.context.tasks.detail(session, principal, task_id)
            if task.version != body.expected_version:
                raise AppError('work_version_conflict', '任务已更新，请刷新后重试', 409)
            configuration = await self.context.tasks.configuration(session, principal, task_id)
            binding, skills = await self._configuration(session, principal, configuration)
            for file_id in body.file_ids:
                info = await self.context.files.get(session, principal.personal(), file_id)
                await self.context.tasks.attach_input(session, principal, task_id, info)
                await self.bind_file(session, principal, task_id, info)
            task = await self.context.tasks.detail(session, principal, task_id)
            if sum(item.size for item in task.inputs) > self.context.policy.max_input_bytes:
                raise AppError('work_inputs_too_large', '任务资料合计超过100MiB，请拆分处理', 422)
            memory = await self.context.tasks.execution_memory(session, principal, task_id)
            message_id = str(uuid5(body.client_request_id, 'user'))
            reseed_message: str | None = None
            web_search_enabled = self.context.web_search is not None and self.context.web_search.available
            latest_request = next((item for item in reversed(task.messages) if item.role == 'user'), None)
            if body.resume and latest_request is not None and latest_request.run_id is not None:
                original = await self.context.runs.resource_payload(
                    session, latest_request.run_id, 'work_agent', task_id
                )
                web_search_enabled = original.get('web_search_enabled') is True
                # No trusted checkpoint advanced past this request. Reuse its
                # exact user input instead of resuming an older completed turn.
                if memory.get('checkpoint_id') == original.get('checkpoint_id'):
                    reseed_message = latest_request.content
                    message_id = str(original.get('message_id') or message_id)
            binding = binding.model_copy(
                update={
                    'options': binding.options.model_copy(
                        update={'provider_output_default': True, 'response_format': None}
                    )
                }
            )
            run = await self.context.runs.create(
                session,
                owner_id=principal.id,
                resource_type='work_agent',
                resource_id=task_id,
                request_id=body.client_request_id,
                fingerprint=intent,
                payload={
                    'version': 1,
                    'execution_deadline': (utcnow() + timedelta(seconds=self.context.policy.run_seconds)).isoformat(),
                    'binding': binding.model_dump(mode='json'),
                    'configuration': configuration.model_dump(mode='json'),
                    'skills': frozen_skills(skills),
                    'web_search_enabled': web_search_enabled,
                    'inputs': [item.model_dump(mode='json') for item in task.inputs],
                    'checkpoint_id': memory.get('checkpoint_id'),
                    'workspace_files': memory.get('files', []),
                    'message': body.message,
                    'message_id': message_id,
                    'reseed_message': reseed_message,
                    'latest_request': latest_request.content
                    if latest_request is not None and body.resume
                    else body.message,
                    'resume': body.resume,
                    'goal': task.goal or body.message,
                    'sandbox_image': self.context.policy.sandbox_image,
                    'limits': {
                        'model_calls': self.context.policy.model_calls,
                        'tool_calls': self.context.policy.tool_calls,
                        'run_seconds': self.context.policy.run_seconds,
                    },
                },
            )
            await self.context.tasks.admit(
                session, principal, task_id, body.model_copy(update={'expected_version': task.version}), run, intent
            )
            return run

    async def snapshot_in(
        self, session: AsyncSession, principal: Principal, task_id: UUID, run_id: UUID
    ) -> RunSnapshot:
        require_capability(principal, 'work', 'read')
        await self.context.tasks.configuration(session, principal, task_id)
        return await self.context.runs.resource_snapshot(session, run_id, 'work_agent', task_id)

    async def snapshot(self, principal: Principal, task_id: UUID, run_id: UUID) -> RunSnapshot:
        async with self.context.database.sessions() as session:
            principal = await self.current(session, principal, 'read')
            return await self.snapshot_in(session, principal, task_id, run_id)

    async def cancel(self, principal: Principal, task_id: UUID, run_id: UUID) -> RunSnapshot:
        async with self.context.database.sessions.begin() as session:
            principal = await self.current(session, principal)
            await self.context.tasks.configuration(session, principal, task_id)
            run, _ = await self.context.runs.resource_cancel(session, run_id, 'work_agent', task_id)
            for child in await self.context.runs.child_snapshots(session, run_id):
                current, release = await self.context.runs.cancel(session, child.id, principal.id)
                if release:
                    await self.context.usage.settle(session, current.id, rejected_before_execution=True)
            await self.context.tasks.project_snapshot(session, run)
            return run

    async def guard(self, claim: ClaimedRun, *, code: bool = False) -> Principal:
        async with self.context.database.sessions.begin() as session:
            principal = await self.context.identity.principal_for_user(session, claim.owner_id)
            require_capability(principal, 'work', 'use')
            await self.context.runs.lock_execution(session, claim.id, claim.attempt_id, allow_cancel=False)
            await self.context.tasks.configuration(session, principal, claim.resource_id)
            if code:
                require_capability(principal, 'work', 'execute_code')
                if claim.payload.get('sandbox_image') != self.context.policy.sandbox_image:
                    raise AppError('work_runtime_changed', '文件处理环境已更新，请重新发送要求', 409)
            frozen = ModelBinding.model_validate(claim.payload['binding'])
            live = await self.context.models.bindings.resolve(session, frozen.model_id)
            if (
                live.connection_revision_id != frozen.connection_revision_id
                or live.upstream_model != frozen.upstream_model
            ):
                raise AppError('work_model_changed', '模型连接已更新，请重新发送要求', 409)
            selection = skill_selection(claim.payload)
            if selection.revisions:
                require_capability(principal, 'skills', 'use')
                await require_frozen_available(session, principal.id, selection)
            for item in await self.context.tasks.inputs(session, principal, claim.resource_id):
                info = await self.context.files.get(session, principal.personal(), item.file_id)
                if (info.version, info.acl_version, info.sha256) != (item.file_version, item.acl_version, item.sha256):
                    raise AppError('work_input_changed', '任务资料或访问权限已变化，请重新选择资料', 409)
            return principal

    async def guard_model(self, claim: ClaimedRun) -> None:
        request = ModelRequest.model_validate(claim.payload)
        if request.parent_run_id is None or request.parent_attempt_id is None:
            raise AppError('work_execution_lost', '模型调用缺少任务归属', 409)
        async with self.context.database.sessions() as session:
            parent = await self.context.runs.snapshot(session, request.parent_run_id, claim.owner_id)
            payload = await self.context.runs.resource_payload(session, parent.id, 'work_agent', parent.resource_id)
        if parent.attempt_id != request.parent_attempt_id or parent.resource_id != claim.resource_id:
            raise AppError('work_execution_lost', '模型调用不属于当前任务执行', 409)
        frozen = ModelBinding.model_validate(payload['binding'])
        if (request.binding.model_id, request.binding.connection_revision_id, request.binding.upstream_model) != (
            frozen.model_id,
            frozen.connection_revision_id,
            frozen.upstream_model,
        ) or request.binding.max_output_tokens > frozen.max_output_tokens:
            raise AppError('work_model_changed', '模型调用与任务配置不匹配', 409)
        await self.guard(
            ClaimedRun(
                parent.id,
                'work_agent',
                parent.resource_id,
                claim.owner_id,
                request.parent_attempt_id,
                payload,
                claim.billing_owner_id,
            )
        )
