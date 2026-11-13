"""Native skill imports and management, shared by Console and resource adapters."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.application.files.queries import FileQueries
from zhiheng.application.skills.management import SkillManagementService
from zhiheng.infrastructure.database import Database
from zhiheng.modules.developer.public import APIRequestInfo
from zhiheng.modules.skills.public import (
    DraftInfo,
    ResourceSkillInfo,
    SkillInfo,
    SkillPackageParser,
    SkillService,
    SkillStatus,
)
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability
from zhiheng.shared.principal import Principal


class SkillCommands:
    def __init__(
        self,
        database: Database,
        skills: SkillService,
        management: SkillManagementService,
        access: ResourceAccessService,
        files: FileQueries,
    ) -> None:
        self.database, self.skills, self.management, self.access, self.files = (
            database,
            skills,
            management,
            access,
            files,
        )

    async def prepare(self, caller: CallerContext, file_id: UUID, key: UUID) -> DraftInfo:
        info, content = await self.files.read(caller.principal, file_id, caller=caller)
        parsed = SkillPackageParser().parse(content, filename=info.original_filename)
        resources: tuple[dict[str, str], ...] = ({'kind': 'file', 'id': str(file_id), 'action': 'read'},)
        async with self.database.sessions.begin() as session:
            caller = await self.access.current(session, caller, 'skills:write')
            assert caller.identity is not None
            request = await self.access.requests.begin(
                session,
                caller.identity,
                'skills.import',
                key,
                {'file_id': str(file_id)},
                scope='skills:write',
                resources=resources,
            )
            await self.authorize_inputs(session, caller, request, lock=False)
            if request.replayed:
                assert request.snapshot is not None
                return DraftInfo.model_validate(request.snapshot)
            target = await self.skills.target(session, caller.principal.id, parsed.slug)
            if target is not None:
                await self.access.require(session, caller, 'skill', target, 'write', lock=False)

            async def check_target(locked_session: AsyncSession, actual_id: UUID | None) -> None:
                if actual_id is not None:
                    await self.access.require(locked_session, caller, 'skill', actual_id, 'write', lock=False)
                await self.authorize_inputs(locked_session, caller, request)

            draft = await self.skills.prepare(
                session,
                caller.principal,
                content,
                filename=info.original_filename,
                parsed=parsed,
                target_check=check_target,
            )
            if draft.target_skill_id is not None:
                resources = (*resources, {'kind': 'skill', 'id': str(draft.target_skill_id), 'action': 'write'})
            await self.access.requests.complete(
                session,
                request.id,
                result_kind='skill_draft',
                result_id=draft.draft_id,
                snapshot=draft.model_dump(mode='json'),
                resources=resources,
            )
            return draft

    async def confirm(self, caller: CallerContext, draft_id: UUID, key: UUID) -> ResourceSkillInfo:
        async with self.database.sessions.begin() as session:
            await self.authorize_draft(session, caller, draft_id)
            package = await self.skills.draft_package(session, caller.principal.id, draft_id)
        parsed = SkillPackageParser().parse(package[0], filename=package[1]) if package is not None else None
        async with self.database.sessions.begin() as session:
            original = await self.authorize_draft(session, caller, draft_id)
            request = await self.access.begin_request(
                session, caller, 'skills.confirm', key, {'draft_id': str(draft_id)}, scope='skills:write'
            )
            if request.replayed:
                assert request.snapshot is not None
                return ResourceSkillInfo.model_validate(request.snapshot)

            async def check_target(locked_session: AsyncSession, actual_id: UUID | None) -> None:
                if actual_id is not None:
                    await self.access.require(locked_session, caller, 'skill', actual_id, 'write', lock=False)
                await self.authorize_inputs(locked_session, caller, original)

            outcome = await self.skills.confirm_with_outcome(
                session, caller.principal, draft_id, parsed=parsed, target_check=check_target
            )
            receipt = outcome.info
            await self.skills.access(session, caller.principal.id, receipt.id, lock=True, include_deleted=True)
            if outcome.created:
                await self.access.autobind(session, caller, 'skill', receipt.id)
            else:
                await self.access.require(session, caller, 'skill', receipt.id, 'write')
            await self.authorize_inputs(session, caller, original)
            await self.access.requests.complete(
                session,
                original.id,
                result_kind='skill_draft',
                result_id=draft_id,
                snapshot=original.snapshot or {},
                resources=({'kind': 'skill', 'id': str(receipt.id), 'action': 'write'},),
            )
            result = await self.skills.resource_info(
                session, caller.principal.id, receipt.id, include_deleted=True, receipt=receipt
            )
            await self.access.requests.complete(
                session,
                request.id,
                result_kind='skill',
                result_id=receipt.id,
                snapshot=result.model_dump(mode='json'),
                resources=(*original.resources, {'kind': 'skill', 'id': str(receipt.id), 'action': 'write'}),
            )
            return result

    async def authorize_inputs(
        self, session: AsyncSession, caller: CallerContext, request: APIRequestInfo, *, lock: bool = True
    ) -> None:
        await self.access.authorize_request(session, caller, request, lock=lock)
        for requirement in request.resources:
            if requirement['kind'] == 'file':
                await self.access.files.require(
                    session, self.access.personal(caller.principal), UUID(requirement['id']), active=True, lock=lock
                )

    async def authorize_draft(self, session: AsyncSession, caller: CallerContext, draft_id: UUID) -> APIRequestInfo:
        caller = await self.access.current(session, caller, 'skills:write')
        assert caller.identity is not None
        origin = await self.access.requests.origin(session, 'skill_draft', draft_id)
        if origin is None or origin.application_id != caller.identity.application_id:
            raise AppError('skill_draft_not_found', '技能导入草稿不存在', 404)
        original = await self.access.requests.get(session, caller.identity.application_id, origin.request_id)
        await self.authorize_inputs(session, caller, original, lock=False)
        return original

    async def status(
        self, session: AsyncSession, principal: Principal, skill_id: UUID, status: SkillStatus, expected_version: int
    ) -> SkillInfo:
        skill = await self.skills.get(session, principal.id, skill_id)
        require_capability(principal, 'skills', 'use' if skill.source == 'builtin' else 'toggle')
        return await self.skills.set_status(
            session, principal, skill_id, status=status, expected_version=expected_version
        )

    async def remove(self, session: AsyncSession, principal: Principal, skill_id: UUID) -> SkillInfo:
        return await self.management.remove(session, principal, skill_id)

    async def mutate(
        self, caller: CallerContext, skill_id: UUID, version: int, *, status: SkillStatus | None = None
    ) -> ResourceSkillInfo:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'skill', skill_id, 'write' if status else 'delete')
            current = await self.skills.access(session, caller.principal.id, skill_id, lock=True)
            if current.version != version:
                raise AppError('precondition_failed', '技能版本已变化', 412)
            if status:
                await self.status(session, caller.principal, skill_id, status, version)
            else:
                await self.remove(session, caller.principal, skill_id)
            return await self.skills.resource_info(session, caller.principal.id, skill_id, include_deleted=True)
