from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from plugins.agent_extension.config import AgentSkillSettings
from plugins.agent_extension.domain.skill_errors import SkillServiceError
from plugins.agent_extension.domain.skill_package import SkillPackageLimits, SkillPackageParser
from plugins.agent_extension.entity.do.skill_do import AgentSkill
from plugins.agent_extension.entity.vo.skill_vo import SkillPageQueryModel, SkillStatusUpdateModel
from plugins.agent_extension.infrastructure.skill_storage import SkillStorage
from plugins.agent_extension.service.skill_service import SkillService
from tests.plugins.agent_extension.test_skill_package import VALID_SKILL


@pytest_asyncio.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(AgentSkill.__table__.create)
    try:
        yield session_maker
    finally:
        await engine.dispose()


def make_storage(tmp_path: Path) -> SkillStorage:
    return SkillStorage(
        tmp_path / 'skills',
        parser=SkillPackageParser(
            SkillPackageLimits(upload_bytes=4096, extracted_bytes=4096, file_count=10, skill_md_bytes=2048)
        ),
    )


def settings(tmp_path: Path, *, context_bytes: int = 4096) -> AgentSkillSettings:
    return AgentSkillSettings(
        data_dir=tmp_path / 'skills',
        upload_bytes=4096,
        extracted_bytes=4096,
        file_count=10,
        skill_md_bytes=2048,
        context_bytes=context_bytes,
        max_selected=5,
    )


@pytest.mark.asyncio
async def test_upload_confirm_list_status_and_delete_closed_loop(
    database: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = make_storage(tmp_path)
    async with database() as session:
        service = SkillService(session, storage, settings(tmp_path))
        draft = await service.prepare_upload(VALID_SKILL, filename='SKILL.md', operator='admin')
        installed = await service.confirm_install(draft.draft_id, operator='admin')

        page = await service.list_skills(SkillPageQueryModel(), is_page=True)
        assert page.total == 1
        assert page.rows[0]['skillSlug'] == 'deep-research'
        assert (await service.get_content(installed.skill_id)).content.startswith('---')

        with pytest.raises(SkillServiceError, match='先停用'):
            await service.delete_skill(installed.skill_id, operator='admin')

        disabled = await service.update_status(
            installed.skill_id,
            SkillStatusUpdateModel(status='disabled', expectedVersion=installed.lock_version),
            operator='admin',
        )
        assert disabled.status == 'disabled'
        available = await service.list_available_skills()
        assert available == []

        enabled = await service.update_status(
            installed.skill_id,
            SkillStatusUpdateModel(status='enabled', expectedVersion=disabled.lock_version),
            operator='admin',
        )
        assert enabled.status == 'enabled'
        disabled = await service.update_status(
            installed.skill_id,
            SkillStatusUpdateModel(status='disabled', expectedVersion=enabled.lock_version),
            operator='admin',
        )
        await service.delete_skill(installed.skill_id, operator='admin')
        assert not storage.skill_dir('deep-research').exists()
        assert (await service.list_skills(SkillPageQueryModel(), is_page=True)).total == 0


@pytest.mark.asyncio
async def test_builtin_skill_cannot_be_deleted(
    database: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    builtin_root = tmp_path / 'buildin'
    skill_root = builtin_root / 'document-summary'
    skill_root.mkdir(parents=True)
    skill_root.joinpath('SKILL.md').write_bytes(VALID_SKILL.replace(b'deep-research', b'document-summary'))
    storage = make_storage(tmp_path)
    async with database() as session:
        service = SkillService(session, storage, settings(tmp_path))
        await service.sync_builtin_skills(builtin_root)
        page = await service.list_skills(SkillPageQueryModel(), is_page=True)
        skill = page.rows[0]
        disabled = await service.update_status(
            skill['skillId'],
            SkillStatusUpdateModel(status='disabled', expectedVersion=skill['lockVersion']),
            operator='admin',
        )

        with pytest.raises(SkillServiceError, match='内置技能'):
            await service.delete_skill(disabled.skill_id, operator='admin')


@pytest.mark.asyncio
async def test_resolve_context_preserves_selection_order_and_limits_content(
    database: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = make_storage(tmp_path)
    async with database() as session:
        service = SkillService(session, storage, settings(tmp_path))
        draft = await service.prepare_upload(VALID_SKILL, filename='SKILL.md', operator='admin')
        await service.confirm_install(draft.draft_id, operator='admin')

        resolved = await service.resolve_enabled_skills(['deep-research'])
        assert '<selected_skills>' in resolved.system_prompt
        assert resolved.snapshots == ({'slug': 'deep-research', 'name': '深度研究'},)

        limited = SkillService(session, storage, settings(tmp_path, context_bytes=16))
        with pytest.raises(SkillServiceError, match='内容过大'):
            await limited.resolve_enabled_skills(['deep-research'])
        with pytest.raises(SkillServiceError, match='最多选择'):
            await service.resolve_enabled_skills(['deep-research'] * 6)


@pytest.mark.asyncio
async def test_resolve_rejects_missing_and_disabled_skill(
    database: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = make_storage(tmp_path)
    async with database() as session:
        service = SkillService(session, storage, settings(tmp_path))
        with pytest.raises(SkillServiceError, match='不存在'):
            await service.resolve_enabled_skills(['missing-skill'])
        draft = await service.prepare_upload(VALID_SKILL, filename='SKILL.md', operator='admin')
        installed = await service.confirm_install(draft.draft_id, operator='admin')
        await service.update_status(
            installed.skill_id,
            SkillStatusUpdateModel(status='disabled', expectedVersion=installed.lock_version),
            operator='admin',
        )
        with pytest.raises(SkillServiceError, match='已停用'):
            await service.resolve_enabled_skills(['deep-research'])

