import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.skills.contracts import (
    DraftInfo,
    SkillInfo,
    SkillPage,
    SkillRevisionValue,
    SkillSelection,
    SkillStatus,
)
from zhiheng.modules.skills.orm import Skill, SkillDraft, SkillRevision
from zhiheng.modules.skills.package import SkillPackageParser
from zhiheng.modules.skills.validation import is_valid_slug, validate_search
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

MAX_FILENAME_LENGTH = 255
MAX_SELECTION = 5
MAX_SELECTION_BYTES = 32 * 1024


class SkillService:
    async def prepare(self, session: AsyncSession, principal: Principal, payload: bytes, *, filename: str) -> DraftInfo:
        principal.require_permission('ai:skills:write')
        if len(filename) > MAX_FILENAME_LENGTH or '\x00' in filename:
            raise AppError('invalid_skill_package', '技能包文件名无效', 422)
        parsed = SkillPackageParser().parse(payload, filename=filename)
        draft = SkillDraft(
            owner_id=principal.id,
            expires_at=utcnow() + timedelta(seconds=3600),
            filename=filename,
            package_bytes=payload,
            package_sha=hashlib.sha256(payload).hexdigest(),
            parsed_hash=parsed.parsed_hash,
        )
        session.add(draft)
        await session.flush()
        return DraftInfo(
            draft_id=draft.id,
            expires_at=draft.expires_at,
            slug=parsed.slug,
            name=parsed.name,
            description=parsed.description,
            package_version=parsed.package_version,
            content_hash=parsed.content_hash,
            file_count=parsed.file_count,
            total_bytes=parsed.total_bytes,
        )

    async def confirm(self, session: AsyncSession, principal: Principal, draft_id: UUID) -> SkillInfo:
        principal.require_permission('ai:skills:write')
        draft = await session.scalar(
            select(SkillDraft).where(SkillDraft.id == draft_id, SkillDraft.owner_id == principal.id).with_for_update()
        )
        if draft is None:
            raise AppError('skill_draft_not_found', '技能导入草稿不存在', 404)
        if draft.confirmed_skill_id is not None:
            return await self.get(session, draft.confirmed_skill_id, include_deleted=True)
        if draft.expires_at <= utcnow():
            raise AppError('skill_draft_expired', '技能导入草稿已过期', 409)
        if hashlib.sha256(draft.package_bytes).hexdigest() != draft.package_sha:
            raise AppError('skill_draft_changed', '技能导入草稿校验失败', 409)
        parsed = SkillPackageParser().parse(draft.package_bytes, filename=draft.filename)
        if parsed.parsed_hash != draft.parsed_hash:
            raise AppError('skill_draft_changed', '技能导入草稿校验失败', 409)
        revision_id, skill_id = uuid4(), uuid4()
        skill = Skill(id=skill_id, slug=parsed.slug, revision_id=revision_id)
        revision = SkillRevision(
            id=revision_id,
            skill_id=skill_id,
            name=parsed.name,
            description=parsed.description,
            package_version=parsed.package_version,
            content=parsed.content,
            content_hash=parsed.content_hash,
            package_sha=draft.package_sha,
            package_bytes=draft.package_bytes,
            filename=draft.filename,
            file_count=parsed.file_count,
            total_bytes=parsed.total_bytes,
        )
        try:
            async with session.begin_nested():
                session.add(skill)
                await session.flush()
                session.add(revision)
                draft.confirmed_skill_id = skill_id
                await session.flush()
        except IntegrityError as error:
            raise AppError('skill_slug_conflict', '技能标识已存在', 409) from error
        return self._info(skill, revision)

    async def list_skills(
        self,
        session: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str = '',
        status: SkillStatus | None = None,
    ) -> SkillPage:
        validate_search(search)
        query = (
            select(Skill, SkillRevision)
            .join(SkillRevision, Skill.revision_id == SkillRevision.id)
            .where(Skill.deleted_at.is_(None))
        )
        if status is not None:
            query = query.where(Skill.status == status)
        if search:
            pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            query = query.where(
                or_(
                    Skill.slug.ilike(pattern),
                    SkillRevision.name.ilike(pattern),
                    SkillRevision.description.ilike(pattern),
                )
            )
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = (await session.execute(query.order_by(Skill.slug).offset((page - 1) * page_size).limit(page_size))).all()
        return SkillPage(
            items=[self._info(skill, revision) for skill, revision in rows],
            total=total or 0,
            page=page,
            page_size=page_size,
        )

    async def available(self, session: AsyncSession) -> list[SkillInfo]:
        rows = (
            await session.execute(
                select(Skill, SkillRevision)
                .join(SkillRevision, Skill.revision_id == SkillRevision.id)
                .where(Skill.deleted_at.is_(None), Skill.status == 'enabled')
                .order_by(Skill.slug)
            )
        ).all()
        return [self._info(skill, revision) for skill, revision in rows]

    async def _row(
        self, session: AsyncSession, skill_id: UUID, *, lock: bool = False, include_deleted: bool = False
    ) -> tuple[Skill, SkillRevision]:
        query = (
            select(Skill, SkillRevision)
            .join(SkillRevision, Skill.revision_id == SkillRevision.id)
            .where(Skill.id == skill_id)
        )
        if not include_deleted:
            query = query.where(Skill.deleted_at.is_(None))
        if lock:
            query = query.with_for_update(of=Skill)
        row = (await session.execute(query)).first()
        if row is None:
            raise AppError('skill_not_found', '技能不存在', 404)
        return row[0], row[1]

    async def get(self, session: AsyncSession, skill_id: UUID, *, include_deleted: bool = False) -> SkillInfo:
        skill, revision = await self._row(session, skill_id, include_deleted=include_deleted)
        return self._info(skill, revision)

    async def content(self, session: AsyncSession, skill_id: UUID) -> str:
        _, revision = await self._row(session, skill_id)
        return revision.content

    async def set_status(
        self, session: AsyncSession, principal: Principal, skill_id: UUID, *, status: SkillStatus, expected_version: int
    ) -> SkillInfo:
        principal.require_permission('ai:skills:write')
        skill, revision = await self._row(session, skill_id, lock=True)
        if skill.version != expected_version:
            raise AppError('skill_version_conflict', '技能已更新，请刷新后重试', 409)
        skill.status, skill.version, skill.updated_at = status, skill.version + 1, utcnow()
        await session.flush()
        return self._info(skill, revision)

    async def remove(
        self,
        session: AsyncSession,
        principal: Principal,
        skill_id: UUID,
        *,
        reference_check: Callable[[AsyncSession, str], Awaitable[bool]],
    ) -> SkillInfo:
        principal.require_permission('ai:skills:write')
        skill, revision = await self._row(session, skill_id, lock=True)
        if skill.source == 'builtin' or skill.status != 'disabled':
            raise AppError('skill_delete_forbidden', '仅可删除已停用的上传技能', 409)
        if await reference_check(session, skill.slug):
            raise AppError('skill_referenced', '技能仍被智能体引用', 409)
        skill.deleted_at = skill.updated_at = utcnow()
        skill.version += 1
        await session.flush()
        return self._info(skill, revision)

    async def cleanup_expired(self, session: AsyncSession) -> int:
        ids = list(
            await session.scalars(
                select(SkillDraft.id)
                .where(SkillDraft.expires_at <= utcnow(), SkillDraft.confirmed_skill_id.is_(None))
                .order_by(SkillDraft.expires_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        if ids:
            await session.execute(delete(SkillDraft).where(SkillDraft.id.in_(ids)))
        return len(ids)

    @staticmethod
    def _info(skill: Skill, revision: SkillRevision) -> SkillInfo:
        return SkillInfo(
            id=skill.id,
            slug=skill.slug,
            name=revision.name,
            description=revision.description,
            package_version=revision.package_version,
            version=skill.version,
            status=cast('SkillStatus', skill.status),
            source=cast("Literal['builtin', 'upload']", skill.source),
            revision_id=revision.id,
            content_hash=revision.content_hash,
            file_count=revision.file_count,
            total_bytes=revision.total_bytes,
            created_at=skill.created_at,
            updated_at=skill.updated_at,
        )


def _validate_slugs(slugs: Sequence[str]) -> None:
    if len(slugs) > MAX_SELECTION or len(set(slugs)) != len(slugs):
        raise AppError('invalid_skill_selection', '最多选择五个不重复技能', 422)
    if any(not is_valid_slug(slug) for slug in slugs):
        raise AppError('invalid_skill_selection', '技能标识格式无效', 422)


async def require_available(session: AsyncSession, slugs: Sequence[str]) -> None:
    """Check current availability under the same locks as status/deletion writers.

    A publication owns its frozen content, so a current revision's text or size
    must not affect the published selection's context or reservation.
    """
    _validate_slugs(slugs)
    await require_available_set(session, slugs)


async def require_available_set(session: AsyncSession, slugs: Sequence[str]) -> None:
    """Lock a union of independently validated selections in global slug order.

    This checks availability only; count/content limits belong to each Agent's
    selection and must not apply to a union of multiple frozen/current configs.
    """
    unique = sorted(set(slugs))
    if any(not is_valid_slug(slug) for slug in unique):
        raise AppError('invalid_skill_selection', '技能标识格式无效', 422)
    if not unique:
        return
    ids = await session.scalars(
        select(Skill.id)
        .where(Skill.slug.in_(unique), Skill.deleted_at.is_(None), Skill.status == 'enabled')
        .order_by(Skill.slug)
        .with_for_update(read=True)
    )
    if len(ids.all()) != len(unique):
        raise AppError('skill_unavailable', '所选技能不存在或已停用', 422)


async def resolve_selection(session: AsyncSession, slugs: Sequence[str]) -> SkillSelection:
    _validate_slugs(slugs)
    if not slugs:
        return SkillSelection()
    rows = (
        await session.execute(
            select(Skill, SkillRevision)
            .join(SkillRevision, Skill.revision_id == SkillRevision.id)
            .where(Skill.slug.in_(slugs), Skill.deleted_at.is_(None), Skill.status == 'enabled')
            .order_by(Skill.slug)
            .with_for_update(read=True, of=Skill)
        )
    ).all()
    by_slug = {skill.slug: (skill, revision) for skill, revision in rows}
    if len(by_slug) != len(slugs):
        raise AppError('skill_unavailable', '所选技能不存在或已停用', 422)
    values = tuple(
        SkillRevisionValue(skill.id, revision.id, skill.slug, revision.name, revision.content_hash, revision.content)
        for skill, revision in (by_slug[slug] for slug in slugs)
    )
    if sum(len(value.content.encode()) for value in values) > MAX_SELECTION_BYTES:
        raise AppError('skill_context_too_long', '所选技能主文件合计超过32KiB', 422)
    return SkillSelection(values)
