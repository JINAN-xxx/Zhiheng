"""0014 audits provenance before modifying any legacy resource facts."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.core.integration.test_builtin_role_migration import _version
from tests.core.unit.test_skill_package import markdown
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.skills.orm import SkillDraft
from zhiheng.modules.skills.package import SkillPackageParser
from zhiheng.modules.skills.public import SkillRevisionValue, SkillSelection, SkillService, require_frozen_available
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


async def legacy_upload(session: AsyncSession, owner_id: UUID, *, confirmed: bool = True) -> tuple[UUID, UUID]:
    skill_id, revision_id = uuid4(), uuid4()
    parsed = SkillPackageParser().parse(markdown('legacy-owned'), filename='SKILL.md')
    await session.execute(
        text("""INSERT INTO skills
        (id,slug,status,source,version,revision_id,created_at,updated_at,deleted_at)
        VALUES (:id,'legacy-owned','disabled','upload',7,:revision,now(),now(),now())"""),
        {'id': skill_id, 'revision': revision_id},
    )
    await session.execute(
        text("""INSERT INTO skill_revisions
        (id,skill_id,name,description,package_version,content,content_hash,package_sha,package_bytes,
        filename,file_count,total_bytes) VALUES
        (:revision,:skill,:name,:description,:version,:content,:hash,'frozen-package-sha',:bytes,'SKILL.md',1,:size)"""),
        {
            'revision': revision_id,
            'skill': skill_id,
            'name': parsed.name,
            'description': parsed.description,
            'version': parsed.package_version,
            'content': parsed.content,
            'hash': parsed.content_hash,
            'bytes': markdown('legacy-owned'),
            'size': len(markdown('legacy-owned')),
        },
    )
    if confirmed:
        session.add(
            SkillDraft(
                owner_id=owner_id,
                expires_at=utcnow() + timedelta(hours=1),
                filename='SKILL.md',
                package_bytes=markdown('legacy-owned'),
                package_sha='frozen-package-sha',
                parsed_hash=parsed.parsed_hash,
                confirmed_skill_id=skill_id,
                confirmed_revision_id=revision_id,
                confirmed_info={'frozen': 'fact'},
                action='install',
            )
        )
        await session.flush()
    return skill_id, revision_id


@pytest.mark.parametrize('claim', ['missing', 'ambiguous', 'cross-owner', 'missing-active', 'ambiguous-active'])
async def test_uncertain_legacy_owner_or_cross_owner_reference_aborts_atomically(
    settings: Settings, claim: str
) -> None:
    await _version(settings, '0013_skill_revision_history')
    engine = create_async_engine(settings.database_url.get_secret_value())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            owner = await IdentityService().create_user(
                session, username='legacy-owner', password='platform-test-password'
            )
            other = await IdentityService().create_user(
                session, username='legacy-other', password='platform-test-password'
            )
            skill_id, revision_id = await legacy_upload(session, owner.id, confirmed=not claim.startswith('missing'))
            if claim.startswith('ambiguous'):
                session.add(
                    SkillDraft(
                        owner_id=other.id,
                        expires_at=utcnow() + timedelta(hours=1),
                        filename='SKILL.md',
                        package_bytes=b'x',
                        package_sha='x',
                        parsed_hash='x',
                        confirmed_skill_id=skill_id,
                        confirmed_revision_id=revision_id,
                        action='install',
                    )
                )
            if claim == 'cross-owner' or claim.endswith('-active'):
                await session.execute(
                    text("""INSERT INTO agents
                    (id,owner_id,config,version,publication_count,created_at,updated_at)
                    VALUES (:id,:owner,CAST(:config AS jsonb),1,0,now(),now())"""),
                    {'id': uuid4(), 'owner': other.id, 'config': json.dumps({'skill_slugs': ['legacy-owned']})},
                )
        if claim in {'missing', 'ambiguous'}:
            await migrate(settings)
            async with engine.connect() as connection:
                archived = (
                    await connection.execute(
                        text('SELECT owner_id,legacy_archive,ownership_audit FROM skills WHERE id=:id'),
                        {'id': skill_id},
                    )
                ).one()
                assert archived.owner_id is None and archived.legacy_archive
                assert archived.ownership_audit['decision'] == claim
                assert len(archived.ownership_audit['candidate_owner_ids']) == (0 if claim == 'missing' else 2)
                assert (
                    await connection.scalar(
                        text('SELECT content_hash FROM skill_revisions WHERE id=:id'), {'id': revision_id}
                    )
                    == SkillPackageParser().parse(markdown('legacy-owned'), filename='SKILL.md').content_hash
                )
            async with factory.begin() as session:
                assert all(row.id != skill_id for row in await SkillService().available(session, owner.id))
                with pytest.raises(AppError, match='技能不存在'):
                    await SkillService().get(session, owner.id, skill_id)
        else:
            with pytest.raises(RuntimeError, match='migration audit required'):
                await migrate(settings)
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(text('SELECT version_num FROM alembic_version'))
                    == '0013_skill_revision_history'
                )
                assert not await connection.scalar(
                    text("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
                    WHERE table_name='skills' AND column_name='owner_id')""")
                )
                assert (
                    await connection.scalar(
                        text('SELECT count(*) FROM skill_revisions WHERE skill_id=:id'), {'id': skill_id}
                    )
                    == 1
                )
    finally:
        await engine.dispose()


async def test_upgrade_preserves_uploaded_facts_and_binds_builtin_archives(settings: Settings) -> None:
    await _version(settings, '0013_skill_revision_history')
    engine = create_async_engine(settings.database_url.get_secret_value())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            owner = await IdentityService().create_user(
                session, username='legacy-owner', password='platform-test-password'
            )
            other = await IdentityService().create_user(
                session, username='legacy-other', password='platform-test-password'
            )
            skill_id, _revision_id = await legacy_upload(session, owner.id)
            originals = {
                table: (await session.execute(text(f'SELECT to_jsonb(t) FROM {table} t ORDER BY id'))).scalars().all()
                for table in ('skills', 'skill_revisions', 'skill_drafts')
            }
        await migrate(settings)
        async with factory.begin() as session:
            skill = (
                await session.execute(text('SELECT to_jsonb(t) FROM skills t WHERE id=:id'), {'id': skill_id})
            ).scalar_one()
            assert skill.pop('owner_id') == str(owner.id)
            assert skill.pop('legacy_archive') is False
            assert skill.pop('ownership_audit')['decision'] == 'assigned'
            assert skill == next(row for row in originals['skills'] if row['id'] == str(skill_id))
            assert (
                await session.execute(text('SELECT to_jsonb(t) FROM skill_drafts t ORDER BY id'))
            ).scalars().all() == originals['skill_drafts']
            for row in originals['skill_revisions']:
                assert (
                    await session.scalar(
                        text('SELECT to_jsonb(t) FROM skill_revisions t WHERE id=:id'), {'id': UUID(row['id'])}
                    )
                    == row
                )
            assert await session.scalar(text('SELECT count(*) FROM skills WHERE owner_id IS NULL')) == 2
            assert await session.scalar(text('SELECT count(*) FROM skill_legacy_bindings')) == 4
            legacy = (
                (
                    await session.execute(
                        text("""SELECT s.id,r.id AS revision_id,s.slug,r.name,r.content_hash,r.content
                FROM skills s JOIN skill_revisions r ON s.revision_id=r.id
                WHERE s.owner_id IS NULL ORDER BY s.slug LIMIT 1""")
                    )
                )
                .mappings()
                .one()
            )
            frozen = SkillSelection(
                (
                    SkillRevisionValue(
                        legacy['id'],
                        legacy['revision_id'],
                        legacy['slug'],
                        legacy['name'],
                        legacy['content_hash'],
                        legacy['content'],
                    ),
                )
            )
            await require_frozen_available(session, owner.id, frozen)
            personal = next(
                row for row in await SkillService().available(session, owner.id) if row.slug == legacy['slug']
            )
            await SkillService().set_status(session, owner, personal.id, status='disabled', expected_version=1)
            with pytest.raises(AppError, match='所选技能'):
                await require_frozen_available(session, owner.id, frozen)
            await require_frozen_available(session, other.id, frozen)
        with pytest.raises(RuntimeError, match='owner boundaries'):
            await _version(settings, '0013_skill_revision_history', downgrade=True)
    finally:
        await engine.dispose()


@pytest.mark.parametrize('stored_hint', [False, True])
async def test_work_preparation_uses_frozen_member_owner_instead_of_task_owner(
    settings: Settings, stored_hint: bool
) -> None:
    from zhiheng.modules.agents.orm import Agent
    from zhiheng.modules.knowledge.orm import RetrievalIntent
    from zhiheng.modules.work.orm import WorkMember, WorkTask

    await _version(settings, '0013_skill_revision_history')
    engine = create_async_engine(settings.database_url.get_secret_value())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            owner = await IdentityService().create_user(
                session, username='skill-owner', password='platform-test-password'
            )
            actor = await IdentityService().create_user(
                session, username='task-owner', password='platform-test-password'
            )
            skill_id, revision_id = await legacy_upload(session, owner.id)
            agent = Agent(owner_id=owner.id, config={'skill_slugs': ['legacy-owned']})
            session.add(agent)
            await session.flush()
            task = WorkTask(
                owner_id=actor.id,
                title='Delegated',
                goal='Goal',
                create_active_agent_id=agent.id,
                create_client_request_id=uuid4(),
                create_fingerprint='old',
            )
            session.add(task)
            await session.flush()
            session.add(
                WorkMember(
                    task_id=task.id,
                    agent_id=agent.id,
                    agent_owner_id=owner.id,
                    agent_name_snapshot='Owned agent',
                    published_version=1,
                    config=agent.config,
                    skills=[{'skill_id': str(skill_id), 'revision_id': str(revision_id), 'slug': 'legacy-owned'}],
                )
            )
            session.add(
                RetrievalIntent(
                    namespace_id=actor.id,
                    actor_id=actor.id,
                    owner_id=actor.id,
                    client_request_id=uuid4(),
                    resource_type='work_turn',
                    resource_id=task.id,
                    fingerprint='prep',
                    request={'body': {'agent_id': str(agent.id)}},
                    dependencies={
                        'member_config': agent.config,
                        **({'skill_owner_id': str(actor.id)} if stored_hint else {}),
                    },
                    deadline=utcnow() + timedelta(hours=1),
                )
            )
        await migrate(settings)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text('SELECT owner_id FROM skills WHERE id=:id'), {'id': skill_id}) == owner.id
            )
    finally:
        await engine.dispose()
