"""0040 preserves accepted inputs and every financial row while retiring publication history."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text

from tests.core.legacy_schema import reconstruct
from zhiheng.application.execution.admission import AdmissionIdentity
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.modules.models.public import ModelMessage, ModelRequest
from zhiheng.runtime.contracts import RunUsage


@pytest.mark.parametrize('active', [False, True])
async def test_upgrade_preserves_exact_accepted_config_and_all_financial_facts(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, active: bool
) -> None:
    c = app.state.container
    actor = UUID((await admin.get('/api/auth/me')).json()['id'])
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def old_schema(connection: object) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, '0039_retired_runtime_fields')

    async with c.database.engine.begin() as connection:
        await reconstruct(connection, old_schema, preserve_personal_skills=True)
    aid, cid, vid = uuid4(), uuid4(), uuid4()
    original = {
        'name': 'Original accepted assistant',
        'description': '',
        'model_id': model_id,
        'system_prompt': 'ACCEPTED HISTORICAL INPUT',
        'welcome_message': '',
        'skill_slugs': [],
        'knowledge_base_ids': [],
        'application_id': None,
        'temperature': 0.7,
        'max_tokens': 32,
        'history_rounds': 7,
    }
    latest = {**original, 'name': 'Latest assistant', 'system_prompt': 'LATEST CONFIGURATION', 'history_rounds': 2}
    async with c.database.sessions.begin() as session:
        await session.execute(
            text("""INSERT INTO agents
            (id,owner_id,config,version,publication_count,published_version,created_at,updated_at)
            VALUES (:id,:owner,CAST(:config AS jsonb),4,1,1,now(),now())"""),
            {'id': aid, 'owner': actor, 'config': json.dumps(latest)},
        )
        await session.execute(
            text("""INSERT INTO agent_versions
            (id,agent_id,version,draft_version,config,skills,created_at)
            VALUES (:id,:agent,1,1,CAST(:config AS jsonb),'[]',now())"""),
            {'id': vid, 'agent': aid, 'config': json.dumps(original)},
        )
        await session.execute(
            text("""INSERT INTO assistant_conversations
            (id,owner_id,agent_id,create_request_id,published_version,config,skills,title,version,created_at,updated_at)
            VALUES (:id,:owner,:agent,:request,1,CAST(:config AS jsonb),'{"revisions":[]}','Old conversation',1,now(),now())"""),
            {'id': cid, 'owner': actor, 'agent': aid, 'request': uuid4(), 'config': json.dumps(original)},
        )
        binding = await c.models.bindings.resolve(session, UUID(model_id))
        run = await c.run_admission.reserve(
            session,
            identity=AdmissionIdentity(owner_id=actor, billing_owner_id=actor),
            resource_type='agent_chat',
            resource_id=cid,
            client_request_id=uuid4(),
            fingerprint='historical-fact',
            request=ModelRequest(
                binding=binding,
                messages=(
                    ModelMessage('system', original['system_prompt']),
                    ModelMessage('user', 'Historical question'),
                ),
            ),
        )
        await session.execute(
            text("""INSERT INTO assistant_turns
            (id,conversation_id,owner_id,request_id,sequence,content,run_id,published_version,agent_name,sources,created_at)
            VALUES (:id,:cid,:owner,:request,1,'Historical question',:run,1,'Original accepted assistant','[]',now())"""),
            {'id': uuid4(), 'cid': cid, 'owner': actor, 'request': uuid4(), 'run': run.id},
        )
    if active:
        with pytest.raises(RuntimeError, match='Finish or explicitly cancel'):
            await migrate(c.settings)
        async with c.database.sessions() as session:
            assert (
                await session.scalar(text('SELECT version_num FROM alembic_version')) == '0039_retired_runtime_fields'
            )
            assert await session.scalar(text('SELECT count(*) FROM agent_versions')) == 1
    async with c.database.sessions.begin() as session:
        if active:
            _, release = await c.runs.cancel(session, run.id, actor)
            assert release
            await c.usage.settle(session, run.id, rejected_before_execution=True)
        else:
            claim = await c.runs.claim(session, run.id, 5)
            await c.runs.start_provider(session, run.id, claim.attempt_id)
            await c.usage.settle(session, run.id, input_tokens=8, output_tokens=2)
            await c.runs.finish(
                session,
                run.id,
                claim.attempt_id,
                status='completed',
                content='Historical answer',
                reasoning='',
                usage=RunUsage(input_tokens=8, output_tokens=2, status='known'),
                error=None,
            )

    async def finances() -> dict:
        async with c.database.sessions() as session:
            tables = (
                await session.scalars(
                    text("""SELECT tablename FROM pg_tables WHERE schemaname='public'
                AND (tablename LIKE 'billing_%' OR tablename IN ('developer_accounts','developer_invocations'))
                ORDER BY tablename""")
                )
            ).all()
            return {
                table: (await session.scalars(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1'))).all()
                for table in tables
            }

    before = await finances()
    await migrate(c.settings)
    assert await finances() == before
    async with c.database.sessions() as session:
        assert await session.scalar(text("SELECT to_regclass('public.agent_versions') IS NULL"))
        audit = (
            await session.execute(
                text('SELECT config,skills,knowledge,execution FROM agent_run_audits WHERE run_id=:id'), {'id': run.id}
            )
        ).one()
        assert audit.config == original and audit.skills == audit.knowledge == []
        assert audit.execution is None
        current = (await session.execute(text('SELECT config,enabled FROM agents WHERE id=:id'), {'id': aid})).one()
        assert current.config == {k: v for k, v in latest.items() if k != 'history_rounds'} and current.enabled
        assert (
            await session.scalar(text('SELECT runtime_version FROM assistant_conversations WHERE id=:id'), {'id': cid})
            is None
        )
        assert await session.scalar(text('SELECT count(*) FROM assistant_checkpoints.checkpoint_migrations')) == 10
