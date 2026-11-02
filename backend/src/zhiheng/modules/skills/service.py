import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import DateTime, delete, func, literal, or_, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Load

from zhiheng.modules.skills.contracts import (
    DraftInfo,
    ResourceSkillInfo,
    RevisionContent,
    RevisionInfo,
    SkillAccess,
    SkillConfirmation,
    SkillImportAction,
    SkillInfo,
    SkillPage,
    SkillRevisionValue,
    SkillSelection,
    SkillStatus,
)
from zhiheng.modules.skills.orm import Skill, SkillDraft, SkillLegacyBinding, SkillRevision
from zhiheng.modules.skills.package import ParsedSkillPackage, SkillPackageParser
from zhiheng.modules.skills.validation import is_valid_slug, validate_search
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

MAX_FILENAME_LENGTH = 255
MAX_SELECTION = 5
MAX_SELECTION_BYTES = 32 * 1024


def revision_columns(*, content: bool = False) -> Load:
    columns = (
        SkillRevision.id,
        SkillRevision.skill_id,
        SkillRevision.name,
        SkillRevision.description,
        SkillRevision.package_version,
        SkillRevision.content_hash,
        SkillRevision.package_sha,
        SkillRevision.filename,
        SkillRevision.file_count,
        SkillRevision.total_bytes,
    )
    return Load(SkillRevision).load_only(*columns, *([SkillRevision.content] if content else []), raiseload=True)


class SkillService:
    async def initialize_owner(self, session: AsyncSession, owner_id: UUID) -> None:
        """Copy immutable builtin templates during account creation, in its transaction."""
        templates = (
            await session.execute(
                select(Skill, SkillRevision)
                .join(SkillRevision, Skill.revision_id == SkillRevision.id)
                .where(Skill.owner_id.is_(None), Skill.legacy_archive.is_(True), Skill.source == 'builtin')
                .order_by(Skill.slug)
            )
        ).all()
        for template, revision in templates:
            await self._lock_slug(session, owner_id, template.slug)
            if await session.scalar(select(Skill.id).where(Skill.owner_id == owner_id, Skill.slug == template.slug)):
                continue
            personal = Skill(owner_id=owner_id, slug=template.slug, source='builtin', revision_id=uuid4())
            session.add(personal)
            await session.flush()
            session.add(
                SkillRevision(
                    id=personal.revision_id,
                    skill_id=personal.id,
                    **{
                        column: getattr(revision, column)
                        for column in (
                            'name',
                            'description',
                            'package_version',
                            'content',
                            'content_hash',
                            'package_sha',
                            'package_bytes',
                            'filename',
                            'file_count',
                            'total_bytes',
                        )
                    },
                )
            )
            session.add(
                SkillLegacyBinding(
                    owner_id=owner_id,
                    legacy_skill_id=template.id,
                    personal_skill_id=personal.id,
                )
            )
        await session.flush()

    async def reference_ids(self, session: AsyncSession, owner_id: UUID, skill_id: UUID) -> tuple[UUID, ...]:
        await self._row(session, owner_id, skill_id)
        legacy = await session.scalars(
            select(SkillLegacyBinding.legacy_skill_id).where(
                SkillLegacyBinding.owner_id == owner_id,
                SkillLegacyBinding.personal_skill_id == skill_id,
            )
        )
        return (skill_id, *legacy)

    async def prepare(
        self,
        session: AsyncSession,
        principal: Principal,
        payload: bytes,
        *,
        filename: str,
        parsed: ParsedSkillPackage | None = None,
        target_check: Callable[[AsyncSession, UUID | None], Awaitable[None]] | None = None,
    ) -> DraftInfo:
        if len(filename) > MAX_FILENAME_LENGTH or '\x00' in filename:
            raise AppError('invalid_skill_package', '技能包文件名无效', 422)
        parsed = parsed or SkillPackageParser().parse(payload, filename=filename)
        await self._lock_slug(session, principal.id, parsed.slug)
        skill = await session.scalar(
            select(Skill).where(Skill.owner_id == principal.id, Skill.slug == parsed.slug).with_for_update(read=True)
        )
        if target_check is not None:
            await target_check(session, skill.id if skill is not None else None)
        current = (
            await session.get(SkillRevision, skill.revision_id, options=(revision_columns(),))
            if skill is not None
            else None
        )
        action = self._import_action(skill, current, parsed)
        draft = SkillDraft(
            owner_id=principal.id,
            expires_at=utcnow() + timedelta(seconds=3600),
            filename=filename,
            package_bytes=payload,
            package_sha=hashlib.sha256(payload).hexdigest(),
            parsed_hash=parsed.parsed_hash,
            target_skill_id=skill.id if skill else None,
            target_version=skill.version if skill else None,
            action=action,
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
            action=action,
            target_skill_id=draft.target_skill_id,
            expected_version=draft.target_version,
            current_package_version=current.package_version if current else None,
        )

    async def draft_package(self, session: AsyncSession, owner_id: UUID, draft_id: UUID) -> tuple[bytes, str] | None:
        draft = await session.scalar(
            select(SkillDraft).where(SkillDraft.id == draft_id, SkillDraft.owner_id == owner_id)
        )
        if draft is None:
            raise AppError('skill_draft_not_found', '技能导入草稿不存在', 404)
        if draft.confirmed_info is not None:
            return None
        if draft.expires_at <= utcnow():
            raise AppError('skill_draft_expired', '技能导入草稿已过期', 409)
        if hashlib.sha256(draft.package_bytes).hexdigest() != draft.package_sha:
            raise AppError('skill_draft_changed', '技能导入草稿校验失败', 409)
        return draft.package_bytes, draft.filename

    async def confirm(
        self,
        session: AsyncSession,
        principal: Principal,
        draft_id: UUID,
        *,
        parsed: ParsedSkillPackage | None = None,
        target_check: Callable[[AsyncSession, UUID | None], Awaitable[None]] | None = None,
    ) -> SkillInfo:
        return (
            await self.confirm_with_outcome(session, principal, draft_id, parsed=parsed, target_check=target_check)
        ).info

    async def confirm_with_outcome(
        self,
        session: AsyncSession,
        principal: Principal,
        draft_id: UUID,
        *,
        parsed: ParsedSkillPackage | None = None,
        target_check: Callable[[AsyncSession, UUID | None], Awaitable[None]] | None = None,
    ) -> SkillConfirmation:
        if parsed is None:
            package = await self.draft_package(session, principal.id, draft_id)
            parsed = SkillPackageParser().parse(package[0], filename=package[1]) if package is not None else None
        draft = await session.scalar(
            select(SkillDraft)
            .where(SkillDraft.id == draft_id, SkillDraft.owner_id == principal.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if draft is None:
            raise AppError('skill_draft_not_found', '技能导入草稿不存在', 404)
        if draft.confirmed_info is not None:
            return SkillConfirmation(SkillInfo.model_validate(draft.confirmed_info), False)
        if draft.expires_at <= utcnow():
            raise AppError('skill_draft_expired', '技能导入草稿已过期', 409)
        if hashlib.sha256(draft.package_bytes).hexdigest() != draft.package_sha:
            raise AppError('skill_draft_changed', '技能导入草稿校验失败', 409)
        assert parsed is not None
        if parsed.parsed_hash != draft.parsed_hash:
            raise AppError('skill_draft_changed', '技能导入草稿校验失败', 409)
        await self._lock_slug(session, principal.id, parsed.slug)
        skill = await session.scalar(
            select(Skill).where(Skill.owner_id == principal.id, Skill.slug == parsed.slug).with_for_update()
        )
        if target_check is not None:
            await target_check(session, skill.id if skill is not None else None)
        current = (
            await session.get(SkillRevision, skill.revision_id, options=(revision_columns(),))
            if skill is not None
            else None
        )
        if (
            (skill.id if skill else None) != draft.target_skill_id
            or (skill.version if skill else None) != draft.target_version
            or self._import_action(skill, current, parsed) != draft.action
        ):
            raise AppError('skill_version_conflict', '技能在预检后已变化，请重新导入确认', 409)
        if draft.action == 'unchanged' and skill is not None and current is not None:
            return SkillConfirmation(await self._confirm_result(session, draft, skill, current), False)
        created = skill is None
        revision_id = uuid4()
        if skill is None:
            skill = Skill(id=uuid4(), owner_id=principal.id, slug=parsed.slug, revision_id=revision_id)
            session.add(skill)
            await session.flush()
        else:
            skill.revision_id = revision_id
            skill.version += 1
            skill.updated_at = utcnow()
            if draft.action == 'restore':
                skill.deleted_at = None
                skill.status = 'enabled'
        revision = SkillRevision(
            id=revision_id,
            skill_id=skill.id,
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
        session.add(revision)
        return SkillConfirmation(await self._confirm_result(session, draft, skill, revision), created)

    @staticmethod
    async def _lock_slug(session: AsyncSession, owner_id: UUID, slug: str) -> None:
        # Row locks cover existing identities; this also serializes first installs.
        await session.execute(
            text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': f'skill-import:{owner_id}:{slug}'}
        )

    @staticmethod
    def _import_action(
        skill: Skill | None, current: SkillRevision | None, parsed: ParsedSkillPackage
    ) -> SkillImportAction:
        if skill is None:
            return 'install'
        if skill.deleted_at is not None:
            return 'restore'
        if current is not None and current.content_hash == parsed.content_hash:
            return 'unchanged'
        return 'update'

    async def _confirm_result(
        self, session: AsyncSession, draft: SkillDraft, skill: Skill, revision: SkillRevision
    ) -> SkillInfo:
        await session.flush()
        info = self._info(skill, revision)
        draft.confirmed_skill_id = skill.id
        draft.confirmed_revision_id = revision.id
        draft.confirmed_info = info.model_dump(mode='json')
        await session.flush()
        return info

    async def list_skills(
        self,
        session: AsyncSession,
        owner_id: UUID,
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
            .where(Skill.owner_id == owner_id, Skill.deleted_at.is_(None))
            .options(revision_columns())
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
        total = await session.scalar(select(func.count()).select_from(query.with_only_columns(Skill.id).subquery()))
        rows = (await session.execute(query.order_by(Skill.slug).offset((page - 1) * page_size).limit(page_size))).all()
        return SkillPage(
            items=[self._info(skill, revision) for skill, revision in rows],
            total=total or 0,
            page=page,
            page_size=page_size,
        )

    async def available(self, session: AsyncSession, owner_id: UUID) -> list[SkillInfo]:
        rows = (
            await session.execute(
                select(Skill, SkillRevision)
                .join(SkillRevision, Skill.revision_id == SkillRevision.id)
                .options(revision_columns())
                .where(Skill.owner_id == owner_id, Skill.deleted_at.is_(None), Skill.status == 'enabled')
                .order_by(Skill.slug)
            )
        ).all()
        return [self._info(skill, revision) for skill, revision in rows]

    async def _row(
        self,
        session: AsyncSession,
        owner_id: UUID,
        skill_id: UUID,
        *,
        lock: bool = False,
        include_deleted: bool = False,
        content: bool = False,
    ) -> tuple[Skill, SkillRevision]:
        query = select(Skill).where(Skill.id == skill_id, Skill.owner_id == owner_id)
        if not include_deleted:
            query = query.where(Skill.owner_id == owner_id, Skill.deleted_at.is_(None))
        if lock:
            query = query.with_for_update(of=Skill).execution_options(populate_existing=True)
        skill = await session.scalar(query)
        if skill is None:
            raise AppError('skill_not_found', '技能不存在', 404)
        revision = await session.get(SkillRevision, skill.revision_id, options=(revision_columns(content=content),))
        if revision is None:
            raise AppError('skill_not_found', '技能不存在', 404)
        return skill, revision

    async def access(
        self,
        session: AsyncSession,
        owner_id: UUID,
        skill_id: UUID,
        *,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> SkillAccess:
        skill, _ = await self._row(session, owner_id, skill_id, lock=lock, include_deleted=include_deleted)
        assert skill.owner_id is not None
        return SkillAccess(skill.id, skill.owner_id, skill.version, skill.status)

    async def target(self, session: AsyncSession, owner_id: UUID, slug: str) -> UUID | None:
        return cast(
            'UUID | None', await session.scalar(select(Skill.id).where(Skill.owner_id == owner_id, Skill.slug == slug))
        )

    async def resource_info(
        self,
        session: AsyncSession,
        owner_id: UUID,
        skill_id: UUID,
        *,
        include_deleted: bool = False,
        receipt: SkillInfo | None = None,
    ) -> ResourceSkillInfo:
        skill, revision = await self._row(session, owner_id, skill_id, include_deleted=include_deleted)
        info = receipt or self._info(skill, revision)
        return ResourceSkillInfo(
            id=info.id,
            owner_id=owner_id,
            slug=info.slug,
            name=info.name,
            description=info.description,
            status='deleted' if skill.deleted_at is not None and receipt is None else info.status,
            resource_version=info.version,
            current_revision_id=info.revision_id,
            package_version=info.package_version,
            content_hash=info.content_hash,
            created_at=info.created_at,
            updated_at=info.updated_at,
        )

    async def resource_items(
        self, session: AsyncSession, owner_id: UUID, ids: list[UUID], *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[ResourceSkillInfo]:
        query = select(Skill.id).where(Skill.owner_id == owner_id, Skill.id.in_(ids), Skill.deleted_at.is_(None))
        if after is not None:
            query = query.where(
                (Skill.created_at < after[0]) | ((Skill.created_at == after[0]) & (Skill.id < after[1]))
            )
        found = await session.scalars(query.order_by(Skill.created_at.desc(), Skill.id.desc()).limit(limit))
        return [await self.resource_info(session, owner_id, identifier) for identifier in found]

    async def revisions(
        self,
        session: AsyncSession,
        owner_id: UUID,
        skill_id: UUID,
        *,
        after: tuple[datetime, UUID] | None = None,
        limit: int = 101,
        revision_id: UUID | None = None,
    ) -> list[RevisionInfo]:
        skill, _ = await self._row(session, owner_id, skill_id)
        # Native immutable receipts carry release time. Personal builtin copies use their creation time.
        times = (
            select(
                SkillDraft.confirmed_revision_id.label('revision_id'),
                func.min(sql_cast(SkillDraft.confirmed_info['updated_at'].astext, DateTime(timezone=True))).label(
                    'released_at'
                ),
            )
            .where(
                SkillDraft.owner_id == owner_id,
                SkillDraft.confirmed_skill_id == skill_id,
                SkillDraft.confirmed_info.is_not(None),
            )
            .group_by(SkillDraft.confirmed_revision_id)
            .subquery()
        )
        released = func.coalesce(times.c.released_at, literal(skill.created_at, type_=DateTime(timezone=True)))
        query = (
            select(SkillRevision, released.label('released_at'))
            .outerjoin(times, times.c.revision_id == SkillRevision.id)
            .where(SkillRevision.skill_id == skill_id)
            .options(revision_columns())
        )
        if revision_id is not None:
            query = query.where(SkillRevision.id == revision_id)
        if after is not None:
            query = query.where((released < after[0]) | ((released == after[0]) & (SkillRevision.id < after[1])))
        rows = await session.execute(query.order_by(released.desc(), SkillRevision.id.desc()).limit(limit))
        return [self.revision_info(row, created_at) for row, created_at in rows]

    async def revision(self, session: AsyncSession, owner_id: UUID, skill_id: UUID, revision_id: UUID) -> RevisionInfo:
        found = await self.revisions(session, owner_id, skill_id, revision_id=revision_id, limit=1)
        if not found:
            raise AppError('skill_revision_not_found', '技能修订不存在', 404)
        return found[0]

    async def revision_content(
        self, session: AsyncSession, owner_id: UUID, skill_id: UUID, revision_id: UUID
    ) -> RevisionContent:
        info = await self.revision(session, owner_id, skill_id, revision_id)
        content = await session.scalar(
            select(SkillRevision.package_bytes).where(
                SkillRevision.skill_id == skill_id, SkillRevision.id == revision_id
            )
        )
        if content is None:
            raise AppError('skill_revision_not_found', '技能修订不存在', 404)
        return RevisionContent(info, content)

    @staticmethod
    def revision_info(row: SkillRevision, created_at: datetime) -> RevisionInfo:
        return RevisionInfo(
            id=row.id,
            skill_id=row.skill_id,
            name=row.name,
            description=row.description,
            package_version=row.package_version,
            content_hash=row.content_hash,
            package_sha=row.package_sha,
            filename=row.filename,
            file_count=row.file_count,
            total_bytes=row.total_bytes,
            created_at=created_at,
        )

    async def get(
        self, session: AsyncSession, owner_id: UUID, skill_id: UUID, *, include_deleted: bool = False
    ) -> SkillInfo:
        skill, revision = await self._row(session, owner_id, skill_id, include_deleted=include_deleted)
        return self._info(skill, revision)

    async def content(self, session: AsyncSession, owner_id: UUID, skill_id: UUID) -> str:
        _, revision = await self._row(session, owner_id, skill_id, content=True)
        return revision.content

    async def set_status(
        self, session: AsyncSession, principal: Principal, skill_id: UUID, *, status: SkillStatus, expected_version: int
    ) -> SkillInfo:
        skill, revision = await self._row(session, principal.id, skill_id, lock=True)
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
        skill, revision = await self._row(session, principal.id, skill_id, lock=True)
        if await reference_check(session, skill.slug):
            raise AppError('skill_referenced', '技能仍有有效引用或进行中的调用', 409)
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


async def require_available(session: AsyncSession, owner_id: UUID, slugs: Sequence[str]) -> None:
    """Check current availability under the same locks as status/deletion writers.

    A publication owns its frozen content, so a current revision's text or size
    must not affect the published selection's context or reservation.
    """
    _validate_slugs(slugs)
    await require_available_set(session, owner_id, slugs)


async def require_available_set(session: AsyncSession, owner_id: UUID, slugs: Sequence[str]) -> None:
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
        .where(
            Skill.owner_id == owner_id, Skill.slug.in_(unique), Skill.deleted_at.is_(None), Skill.status == 'enabled'
        )
        .order_by(Skill.slug)
        .with_for_update(read=True)
    )
    if len(ids.all()) != len(unique):
        raise AppError('skill_unavailable', '所选技能不存在或已停用', 422)


async def require_available_groups(session: AsyncSession, groups: dict[UUID, set[str]]) -> None:
    """Lock multiple owners in a stable order; a Work union never changes ownership."""
    for owner_id in sorted(groups, key=str):
        await require_available_set(session, owner_id, sorted(groups[owner_id]))


async def resolve_selection(session: AsyncSession, owner_id: UUID, slugs: Sequence[str]) -> SkillSelection:
    _validate_slugs(slugs)
    if not slugs:
        return SkillSelection()
    rows = (
        await session.execute(
            select(Skill, SkillRevision)
            .join(SkillRevision, Skill.revision_id == SkillRevision.id)
            .options(revision_columns(content=True))
            .where(
                Skill.owner_id == owner_id, Skill.slug.in_(slugs), Skill.deleted_at.is_(None), Skill.status == 'enabled'
            )
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


async def require_frozen_available(session: AsyncSession, owner_id: UUID, selection: SkillSelection) -> None:
    """Check the actual frozen identity/revision, including explicitly bound legacy archives."""
    _validate_slugs([value.slug for value in selection.revisions])
    for value in sorted(selection.revisions, key=lambda item: item.slug):
        legacy = await session.get(SkillLegacyBinding, (owner_id, value.skill_id))
        personal_id = legacy.personal_skill_id if legacy else value.skill_id
        skill = await session.scalar(
            select(Skill)
            .where(
                Skill.id == personal_id,
                Skill.owner_id == owner_id,
                Skill.slug == value.slug,
                Skill.deleted_at.is_(None),
                Skill.status == 'enabled',
            )
            .with_for_update(read=True)
        )
        revision = await session.get(SkillRevision, value.revision_id, options=(revision_columns(content=True),))
        if (
            skill is None
            or revision is None
            or revision.skill_id != value.skill_id
            or revision.content_hash != value.content_hash
            or revision.content != value.content
        ):
            raise AppError('skill_unavailable', '所选技能不存在或已停用', 422)
