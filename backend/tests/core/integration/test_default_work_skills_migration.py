from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.core.integration.test_builtin_role_migration import _version
from tests.core.unit.test_skill_package import markdown
from zhiheng.application.identity.accounts import AccountService
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.skills.orm import Skill, SkillRevision
from zhiheng.modules.skills.package import SkillPackageParser
from zhiheng.modules.skills.public import SkillService
from zhiheng.shared.time import utcnow

NEW_SKILLS = {'meeting-minutes', 'requirements-clarification', 'writing-polish'}


async def test_upgrade_adds_personal_copies_and_keeps_existing_skill_state(settings: Settings) -> None:
    await _version(settings, '0026_model_output_range')
    engine = create_async_engine(settings.database_url.get_secret_value())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    skills = SkillService()
    accounts = AccountService(IdentityService(), skills)
    existing = {}
    try:
        async with factory.begin() as session:
            owner = await accounts.create_user(session, username='before-upgrade', password='platform-test-password')
            for slug, deleted in [('meeting-minutes', False), ('writing-polish', True)]:
                payload = markdown(slug, 'Existing personal instructions')
                draft = await skills.prepare(session, owner, payload, filename='SKILL.md')
                installed = await skills.confirm(session, owner, draft.draft_id)
                row = await session.get(Skill, installed.id)
                assert row is not None
                row.status = 'disabled'
                row.deleted_at = utcnow() if deleted else None
                existing[slug] = (row.id, row.revision_id, row.status, row.deleted_at, payload)
        await migrate(settings)
        async with factory.begin() as session:
            newcomer = await accounts.create_user(session, username='after-upgrade', password='platform-test-password')
            rows = (
                await session.execute(
                    select(Skill, SkillRevision)
                    .join(SkillRevision, SkillRevision.id == Skill.revision_id)
                    .where(Skill.slug.in_(NEW_SKILLS))
                )
            ).all()
            by_owner = {
                owner_id: {skill.slug: (skill, rev) for skill, rev in rows if skill.owner_id == owner_id}
                for owner_id in (owner.id, newcomer.id, None)
            }
            for owner_id, copies in by_owner.items():
                assert set(copies) == NEW_SKILLS
                for slug, (skill, rev) in copies.items():
                    if owner_id == owner.id and slug in existing:
                        assert (skill.id, skill.revision_id, skill.status, skill.deleted_at, rev.package_bytes) == (
                            existing[slug]
                        )
                        continue
                    assert skill.source == 'builtin' and skill.status == 'enabled' and skill.deleted_at is None
                    assert skill.legacy_archive == (owner_id is None)
                    parsed = SkillPackageParser().parse(rev.package_bytes, filename='SKILL.md')
                    assert (parsed.slug, parsed.name, parsed.content_hash) == (slug, rev.name, rev.content_hash)
            assert len({skill.id for skill, _ in rows}) == 9
            assert len({rev.id for _, rev in rows}) == 9
    finally:
        await engine.dispose()
