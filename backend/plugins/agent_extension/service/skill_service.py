from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from plugins.agent_extension.dao.skill_dao import SkillDao
from plugins.agent_extension.domain.skill_errors import SkillPackageError, SkillServiceError, SkillStorageError
from plugins.agent_extension.domain.skill_id import new_skill_id
from plugins.agent_extension.entity.do.skill_do import AgentSkill
from plugins.agent_extension.entity.vo.skill_vo import (
    AvailableSkillModel,
    SkillContentModel,
    SkillDetailModel,
    SkillDraftModel,
    SkillModel,
    SkillPageQueryModel,
    SkillStatusUpdateModel,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from common.vo import PageModel
    from plugins.agent_extension.config import AgentSkillSettings
    from plugins.agent_extension.infrastructure.skill_storage import SkillStorage, StagedSkillDelete


@dataclass(frozen=True, slots=True)
class ResolvedSkillContext:
    system_prompt: str
    snapshots: tuple[dict[str, str], ...]


def _read_builtin_sources(root: Path) -> list[tuple[str, bytes]]:
    if not root.is_dir():
        return []
    sources: list[tuple[str, bytes]] = []
    for source in sorted(path for path in root.iterdir() if path.is_dir()):
        skill_file = source / 'SKILL.md'
        if skill_file.is_file():
            sources.append((source.name, skill_file.read_bytes()))
    return sources


class SkillService:
    def __init__(
        self,
        db: AsyncSession,
        storage: SkillStorage,
        settings: AgentSkillSettings,
        *,
        dao: type[SkillDao] = SkillDao,
        id_factory: Callable[[], str] = new_skill_id,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._db = db
        self._storage = storage
        self._settings = settings
        self._dao = dao
        self._id_factory = id_factory
        self._clock = clock

    async def list_skills(
        self, query: SkillPageQueryModel, *, is_page: bool = False
    ) -> PageModel | list[dict[str, Any]]:
        try:
            return await self._dao.list_skills(self._db, query, is_page=is_page)
        finally:
            await self._rollback_if_active()

    async def get_skill(self, skill_id: str) -> SkillDetailModel:
        try:
            skill = await self._require_skill(skill_id)
            return SkillDetailModel.model_validate(skill)
        finally:
            await self._rollback_if_active()

    async def get_content(self, skill_id: str) -> SkillContentModel:
        try:
            skill = await self._require_skill(skill_id)
            content = await asyncio.to_thread(self._storage.read_skill_md, skill.storage_path)
            return SkillContentModel(skillId=skill.skill_id, skillSlug=skill.skill_slug, content=content)
        finally:
            await self._rollback_if_active()

    async def prepare_upload(self, payload: bytes, *, filename: str, operator: str) -> SkillDraftModel:
        try:
            prepared = await asyncio.to_thread(
                self._storage.prepare,
                payload,
                filename=filename,
                owner=operator,
            )
        except (SkillPackageError, SkillStorageError) as error:
            raise SkillServiceError('SKILL_PACKAGE_INVALID', str(error)) from error
        package = prepared.package
        return SkillDraftModel(
            draftId=prepared.draft_id,
            skillSlug=package.slug,
            skillName=package.name,
            description=package.description,
            version=package.version,
            fileCount=package.file_count,
            totalBytes=package.total_bytes,
        )

    async def confirm_install(self, draft_id: str, *, operator: str, source_type: str = 'upload') -> SkillModel:
        relative_path: str | None = None
        slug: str | None = None
        try:
            prepared = await asyncio.to_thread(self._storage.load_draft, draft_id, owner=operator)
            slug = prepared.package.slug
            if await self._dao.get_by_slug(self._db, slug) is not None:
                raise SkillServiceError('SKILL_SLUG_CONFLICT', '技能标识已存在')
            relative_path = await asyncio.to_thread(self._storage.confirm, draft_id, owner=operator)
            package = prepared.package
            now = self._clock()
            skill = AgentSkill(
                skill_id=self._id_factory(),
                skill_slug=package.slug,
                skill_name=package.name,
                description=package.description,
                version=package.version,
                status='enabled',
                source_type=source_type,
                storage_path=relative_path,
                content_hash=package.content_hash,
                metadata_json=package.metadata,
                lock_version=1,
                created_by=operator,
                created_at=now,
                updated_by=operator,
                updated_at=now,
            )
            await self._dao.create_skill(self._db, skill)
            result = SkillModel.model_validate(skill)
            await self._db.commit()
            return result
        except SkillServiceError:
            await self._rollback_preserving_error()
            await self._remove_confirmed_directory(slug, relative_path)
            raise
        except (SkillPackageError, SkillStorageError) as error:
            await self._rollback_preserving_error()
            await self._remove_confirmed_directory(slug, relative_path)
            raise SkillServiceError('SKILL_DRAFT_INVALID', str(error)) from error
        except IntegrityError as error:
            await self._rollback_preserving_error()
            await self._remove_confirmed_directory(slug, relative_path)
            raise SkillServiceError('SKILL_SLUG_CONFLICT', '技能标识已存在') from error
        except BaseException:
            await self._rollback_preserving_error()
            await self._remove_confirmed_directory(slug, relative_path)
            raise

    async def update_status(
        self,
        skill_id: str,
        payload: SkillStatusUpdateModel,
        *,
        operator: str,
    ) -> SkillModel:
        try:
            skill = await self._require_locked_skill(skill_id)
            if skill.lock_version != payload.expected_version:
                raise SkillServiceError('SKILL_VERSION_CONFLICT', '技能状态已变化，请刷新后重试')
            values = {
                **SkillModel.model_validate(skill).model_dump(),
                'status': payload.status,
                'lock_version': payload.expected_version + 1,
                'updated_by': operator,
                'updated_at': self._clock(),
            }
            changed = await self._dao.update_status_with_version(
                self._db,
                skill_id,
                expected_version=payload.expected_version,
                status=payload.status,
                operator=operator,
                now=values['updated_at'],
            )
            if changed != 1:
                raise SkillServiceError('SKILL_VERSION_CONFLICT', '技能状态已变化，请刷新后重试')
            result = SkillModel.model_validate(values)
            await self._db.commit()
            return result
        except BaseException:
            await self._rollback_preserving_error()
            raise

    async def delete_skill(self, skill_id: str, *, operator: str) -> None:
        del operator
        staged: StagedSkillDelete | None = None
        try:
            skill = await self._require_locked_skill(skill_id)
            if skill.source_type == 'builtin':
                raise SkillServiceError('SKILL_BUILTIN_DELETE_FORBIDDEN', '内置技能不能删除')
            if skill.status != 'disabled':
                raise SkillServiceError('SKILL_DELETE_REQUIRES_DISABLED', '请先停用技能再删除')
            staged = await asyncio.to_thread(self._storage.stage_delete, skill.skill_slug)
            if await self._dao.delete_disabled_upload(self._db, skill_id) != 1:
                raise SkillServiceError('SKILL_STATE_CONFLICT', '技能状态已变化，请刷新后重试')
            await self._db.commit()
        except BaseException:
            await self._rollback_preserving_error()
            if staged is not None:
                await asyncio.to_thread(self._storage.restore_delete, staged)
            raise
        if staged is not None:
            await asyncio.to_thread(self._storage.purge_delete, staged)

    async def list_available_skills(self) -> list[AvailableSkillModel]:
        try:
            rows = await self._dao.list_available(self._db)
            return [AvailableSkillModel.model_validate(row) for row in rows]
        finally:
            await self._rollback_if_active()

    async def resolve_enabled_skills(self, slugs: list[str]) -> ResolvedSkillContext:
        if len(slugs) > self._settings.max_selected or len(slugs) != len(set(slugs)):
            raise SkillServiceError(
                'SKILL_SELECTION_LIMIT',
                f'每条消息最多选择 {self._settings.max_selected} 个技能，且不能重复',
            )
        if not slugs:
            return ResolvedSkillContext(system_prompt='', snapshots=())
        try:
            rows = await self._dao.get_by_slugs(self._db, slugs)
            by_slug = {row.skill_slug: row for row in rows}
            missing = next((slug for slug in slugs if slug not in by_slug), None)
            if missing is not None:
                raise SkillServiceError('SKILL_NOT_FOUND', '所选技能不存在，请重新选择')
            disabled = next((slug for slug in slugs if by_slug[slug].status != 'enabled'), None)
            if disabled is not None:
                raise SkillServiceError('SKILL_DISABLED', '所选技能已停用，请重新选择')
            parts = ['<selected_skills>']
            snapshots: list[dict[str, str]] = []
            total_bytes = 0
            for slug in slugs:
                skill = by_slug[slug]
                content = await asyncio.to_thread(self._storage.read_skill_md, skill.storage_path)
                total_bytes += len(content.encode('utf-8'))
                if total_bytes > self._settings.context_bytes:
                    raise SkillServiceError('SKILL_CONTENT_TOO_LARGE', '所选技能内容过大，无法用于本次对话')
                parts.extend((f'<skill name="{skill.skill_name}" slug="{skill.skill_slug}">', content, '</skill>'))
                snapshots.append({'slug': skill.skill_slug, 'name': skill.skill_name})
            parts.append('</selected_skills>')
            return ResolvedSkillContext(system_prompt='\n'.join(parts), snapshots=tuple(snapshots))
        finally:
            await self._rollback_if_active()

    async def sync_builtin_skills(self, builtin_root: Path) -> None:
        sources = await asyncio.to_thread(_read_builtin_sources, builtin_root)
        for source_name, content in sources:
            if await self._dao.get_by_slug(self._db, source_name) is not None:
                await self._rollback_if_active()
                continue
            draft = await self.prepare_upload(content, filename='SKILL.md', operator='system')
            if draft.skill_slug != source_name:
                raise SkillServiceError('SKILL_BUILTIN_INVALID', '内置技能目录与标识不一致')
            await self.confirm_install(draft.draft_id, operator='system', source_type='builtin')

    async def _require_skill(self, skill_id: str) -> AgentSkill:
        skill = await self._dao.get_by_id(self._db, skill_id)
        if skill is None:
            raise SkillServiceError('SKILL_NOT_FOUND', '技能不存在')
        return skill

    async def _require_locked_skill(self, skill_id: str) -> AgentSkill:
        skill = await self._dao.get_by_id_for_update(self._db, skill_id)
        if skill is None:
            raise SkillServiceError('SKILL_NOT_FOUND', '技能不存在')
        return skill

    async def _remove_confirmed_directory(self, slug: str | None, relative_path: str | None) -> None:
        if slug is None or relative_path is None:
            return
        try:
            staged = await asyncio.to_thread(self._storage.stage_delete, slug)
            await asyncio.to_thread(self._storage.purge_delete, staged)
        except Exception:
            pass

    async def _rollback_if_active(self) -> None:
        if self._db.in_transaction():
            await self._db.rollback()

    async def _rollback_preserving_error(self) -> None:
        try:
            await self._db.rollback()
        except Exception:
            pass


class SkillContextProvider:
    def __init__(self, storage: SkillStorage, settings: AgentSkillSettings) -> None:
        self._storage = storage
        self._settings = settings

    async def resolve(self, db: AsyncSession, user_id: int, slugs: list[str]) -> ResolvedSkillContext:
        del user_id
        return await SkillService(db, self._storage, self._settings).resolve_enabled_skills(slugs)
