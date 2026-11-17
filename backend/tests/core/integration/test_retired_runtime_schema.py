"""Schema0039 removes retired settings while preserving counters and frozen execution facts."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.core.chat_spec_support import execution_spec
from tests.core.integration.test_builtin_role_migration import _version
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.chat.orm import Conversation
from zhiheng.modules.identity.public import IdentityService
from zhiheng.runtime.orm import Run


async def test_upgrade_removes_retired_fields_without_rewriting_usage_or_snapshot_facts(settings: Settings) -> None:
    await _version(settings, '0038_skill_receipt_payload')
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run_id, conversation_id = uuid4(), uuid4()
    frozen = execution_spec(input_budget=8192).model_dump(mode='json')
    try:
        async with sessions.begin() as session:
            owner = await IdentityService().create_user(
                session, username='retirement-owner', password='schema-test-password', is_admin=True
            )
            session.add(Conversation(id=conversation_id, owner_id=owner.id, title='Frozen context'))
            session.add(
                Run(
                    id=run_id,
                    owner_id=owner.id,
                    billing_owner_id=owner.id,
                    resource_type='chat',
                    resource_id=conversation_id,
                    client_request_id=uuid4(),
                    fingerprint='retirement',
                    payload={},
                    status='completed',
                    text='retained output',
                )
            )
            await session.flush()
            await session.execute(
                text(
                    'INSERT INTO developer_accounts(user_id,limit_tokens,quota_version,used_tokens,reserved_tokens) VALUES (:owner,1,9,2147483648,7)'
                ),
                {'owner': owner.id},
            )
            await session.execute(
                text("""INSERT INTO chat_context_snapshots
                (run_id,conversation_id,owner_id,summary_version,source_refs,file_refs,estimated_tokens,input_budget,dropped_turns,created_at,agent_context,agent_checkpoint_id)
                VALUES (:run,:conversation,:owner,3,CAST(:sources AS jsonb),CAST(:files AS jsonb),99,256,2,CURRENT_TIMESTAMP,CAST(:spec AS jsonb),'accepted-head')"""),
                {
                    'run': run_id,
                    'conversation': conversation_id,
                    'owner': owner.id,
                    'sources': json.dumps([{'id': 'question', 'hash': 'source'}]),
                    'files': json.dumps([{'file_id': str(uuid4()), 'hash': 'file'}]),
                    'spec': json.dumps(frozen),
                },
            )
            snapshot = await session.scalar(
                text(
                    "SELECT to_jsonb(s)-ARRAY['summary_version','estimated_tokens','input_budget','dropped_turns'] FROM chat_context_snapshots s WHERE run_id=:run"
                ),
                {'run': run_id},
            )
            run_before = await session.scalar(
                text('SELECT to_jsonb(r) FROM runtime_runs r WHERE id=:run'), {'run': run_id}
            )
        await _version(settings, '0039_retired_runtime_fields')
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync: {
                    table: {col['name'] for col in inspect(sync).get_columns(table)}
                    for table in ['developer_accounts', 'chat_context_snapshots', 'knowledge_graph_attempts']
                }
            )
            assert columns['developer_accounts'] == {'user_id', 'used_tokens', 'reserved_tokens'}
            assert (
                not {'summary_version', 'estimated_tokens', 'input_budget', 'dropped_turns'}
                & columns['chat_context_snapshots']
            )
            assert 'elapsed_seconds' not in columns['knowledge_graph_attempts']
            assert (
                await connection.scalar(
                    text('SELECT to_jsonb(s) FROM chat_context_snapshots s WHERE run_id=:run'), {'run': run_id}
                )
                == snapshot
            )
            assert (
                await connection.scalar(text('SELECT to_jsonb(r) FROM runtime_runs r WHERE id=:run'), {'run': run_id})
                == run_before
            )
            assert (
                await connection.execute(
                    text('SELECT used_tokens,reserved_tokens FROM developer_accounts WHERE user_id=:owner'),
                    {'owner': owner.id},
                )
            ).one() == (2147483648, 7)
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text('UPDATE developer_accounts SET reserved_tokens=-1 WHERE user_id=:owner'), {'owner': owner.id}
                )
        with pytest.raises(RuntimeError, match='restore a database backup'):
            await _version(settings, '0038_skill_receipt_payload', downgrade=True)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text('SELECT version_num FROM alembic_version'))
                == '0039_retired_runtime_fields'
            )
        await migrate(settings)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text('SELECT to_jsonb(r) FROM runtime_runs r WHERE id=:run'), {'run': run_id})
                == run_before
            )
    finally:
        await engine.dispose()
