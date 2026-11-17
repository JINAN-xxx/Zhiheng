"""Either deployed schema joins the shared head without replaying its own branch."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from tests.core.integration.test_builtin_role_migration import _version
from tests.core.legacy_schema import insert_historical
from tests.core.schema_support import active_migration_context, assert_original_rows
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.database import SCHEMA_REVISION, Database
from zhiheng.modules.agents.orm import Agent, AgentRunAudit, AssistantConversation, AssistantTurn
from zhiheng.modules.billing.orm import CreditAccount, CreditLedger
from zhiheng.modules.files.orm import FileRecord, FileReference
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.work.orm import WorkArtifact, WorkInput, WorkMessage, WorkSandboxLease, WorkStep, WorkTask
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

STARTING_REVISIONS = ('empty', '0040_assistant_agents', '0041_work_agent', '0041_assistant_attachments')
SCHEMAS = ('public', 'knowledge_graph_sdk', 'chat_checkpoints', 'assistant_checkpoints', 'work_checkpoints')


async def snapshot(connection: AsyncConnection) -> dict[tuple[str, str], list[str]]:
    tables = await connection.execute(
        text(
            'SELECT schemaname,tablename FROM pg_tables WHERE schemaname=ANY(:schemas) '
            "AND tablename<>'alembic_version' ORDER BY schemaname,tablename"
        ),
        {'schemas': list(SCHEMAS)},
    )
    return {
        (schema, table): list(
            await connection.scalars(text(f'SELECT to_jsonb(t)::text FROM "{schema}"."{table}" t ORDER BY 1'))
        )
        for schema, table in tables
    }


def file_record(owner: UUID, name: str, purpose: str) -> FileRecord:
    content = ('Preserved ' + name).encode()
    return FileRecord(
        owner_id=owner,
        uploader_id=owner,
        storage_key=uuid4().hex,
        original_filename=name,
        display_name=name,
        purpose=purpose,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        mime='text/plain',
        storage_status='present',
    )


async def checkpoint(session: AsyncSession, schema: str, thread: UUID) -> None:
    await session.execute(
        text(f"""INSERT INTO {schema}.checkpoints
        (thread_id,checkpoint_id,type,checkpoint,metadata)
        VALUES (:thread,'preserved-checkpoint','json',CAST(:checkpoint AS jsonb),CAST(:metadata AS jsonb))"""),
        {
            'thread': str(thread),
            'checkpoint': json.dumps({'v': 4, 'channel_values': {'messages': ['保留原始记忆']}}),
            'metadata': json.dumps({'source': 'migration-preservation', 'step': 7}),
        },
    )
    await session.execute(
        text(f"""INSERT INTO {schema}.checkpoint_blobs (thread_id,channel,version,type,blob)
        VALUES (:thread,'messages','7','bytes',:content)"""),
        {'thread': str(thread), 'content': b'\x00original-checkpoint-blob\xff'},
    )
    await session.execute(
        text(f"""INSERT INTO {schema}.checkpoint_writes
        (thread_id,checkpoint_id,task_id,idx,channel,type,blob,task_path)
        VALUES (:thread,'preserved-checkpoint','original-task',0,'messages','bytes',:content,'original/path')"""),
        {'thread': str(thread), 'content': b'original-write'},
    )


async def seed_history(session: AsyncSession, revision: str) -> tuple[UUID, UUID]:
    owner = await IdentityService().create_user(session, username='merge-owner', password='platform-test-password')
    agent = Agent(owner_id=owner.id, config={'name': '保留原助手', 'instructions': '保留原配置'}, enabled=True)
    await insert_historical(session, agent)
    conversation = AssistantConversation(
        owner_id=owner.id,
        agent_id=agent.id,
        create_request_id=uuid4(),
        config=agent.config,
        skills={},
        title='合并前助手会话',
        checkpoint_id='preserved-checkpoint',
    )
    await insert_historical(session, conversation)
    native = Run(
        owner_id=owner.id,
        billing_owner_id=owner.id,
        resource_type='agent_chat',
        resource_id=conversation.id,
        client_request_id=uuid4(),
        fingerprint='a' * 64,
        payload={'original': 'accepted request'},
        status='completed',
        provider_started=True,
        text='合并前助手回复',
        input_tokens=11,
        output_tokens=5,
        usage_status='known',
    )
    await insert_historical(session, native)
    attached = file_record(owner.id, 'assistant.txt', 'chat_attachment')
    await insert_historical(session, attached)
    await insert_historical(
        session, FileReference(file_id=attached.id, resource_type='agent_chat', resource_id=conversation.id)
    )
    attachments = [{'file_id': str(attached.id), 'name': attached.original_filename, 'sha256': attached.sha256}]
    await insert_historical(
        session,
        AssistantTurn(
            conversation_id=conversation.id,
            owner_id=owner.id,
            request_id=native.client_request_id,
            sequence=1,
            content='原提问与附件信息',
            run_id=native.id,
            agent_name='保留原助手',
            attachments=attachments if revision == '0041_assistant_attachments' else [],
        ),
    )
    await insert_historical(
        session,
        AgentRunAudit(
            run_id=native.id, agent_id=agent.id, owner_id=owner.id, config=agent.config, skills=[], knowledge=[]
        ),
    )
    await insert_historical(
        session,
        CreditAccount(
            user_id=owner.id,
            allocated_points=Decimal('100.125'),
            used_points=Decimal('1.125'),
            reserved_points=Decimal('2.5'),
        ),
    )
    await insert_historical(
        session,
        CreditLedger(
            user_id=owner.id,
            kind='charge',
            amount=Decimal('1.125'),
            available_delta=Decimal('-1.125'),
            reserved_delta=Decimal('0'),
            available_after=Decimal('96.5'),
            reserved_after=Decimal('2.5'),
            reason='合并前财务账本保持原值',
        ),
    )
    await checkpoint(session, 'assistant_checkpoints', conversation.id)
    task = WorkTask(
        owner_id=owner.id,
        title='合并前工作任务',
        goal='保留全部执行事实',
        create_active_agent_id=agent.id,
        create_client_request_id=uuid4(),
        create_fingerprint='original-task',
        mode='agent' if revision == '0041_work_agent' else 'legacy',
        execution_state='completed',
        agent_checkpoint_id='preserved-checkpoint',
        version=9,
    )
    await insert_historical(session, task)
    work_run = Run(
        owner_id=owner.id,
        billing_owner_id=owner.id,
        resource_type='work_agent' if revision == '0041_work_agent' else 'work_turn',
        resource_id=task.id,
        client_request_id=uuid4(),
        fingerprint='b' * 64,
        payload={'original': 'workspace request'},
        status='completed',
        attempt_id=uuid4(),
        text='合并前工作成果',
        input_tokens=17,
        output_tokens=9,
        usage_status='known',
    )
    await insert_historical(session, work_run)
    await insert_historical(
        session,
        WorkMessage(
            task_id=task.id,
            client_request_id=work_run.client_request_id,
            sequence=1,
            role='assistant',
            content=work_run.text,
            run_id=work_run.id,
            usage={'input_tokens': 17, 'output_tokens': 9, 'status': 'known'},
        ),
    )
    if revision == '0041_work_agent':
        await seed_workspace(session, task, work_run)
    return conversation.id, task.id


async def seed_workspace(session: AsyncSession, task: WorkTask, run: Run) -> None:
    source = file_record(task.owner_id, 'source.txt', 'work_attachment')
    output = file_record(task.owner_id, 'report.txt', 'work_artifact')
    for row in (source, output):
        await insert_historical(session, row)
        await insert_historical(session, FileReference(file_id=row.id, resource_type='work', resource_id=task.id))
    await insert_historical(
        session,
        WorkInput(
            task_id=task.id,
            file_id=source.id,
            name=source.original_filename,
            version=2,
            size=source.size,
            mime=source.mime,
            sha256=source.sha256,
            file_version=source.version,
            acl_version=source.acl_version,
        ),
    )
    await insert_historical(
        session,
        WorkArtifact(
            task_id=task.id,
            run_id=run.id,
            file_id=output.id,
            logical_path='outputs/report.txt',
            name='report.txt',
            version=3,
            size=output.size,
            mime=output.mime,
            sha256=output.sha256,
            validation={'checked': True, 'original': 'publication fact'},
        ),
    )
    await insert_historical(
        session,
        WorkStep(
            task_id=task.id,
            run_id=run.id,
            attempt_id=run.attempt_id,
            action_id='original-publication',
            kind='tool',
            name='publish_outputs',
            status='succeeded',
            summary='原成果发布记录',
            details={'arguments': {'path': 'outputs/report.txt'}},
            result={'file_id': str(output.id), 'sha256': output.sha256},
        ),
    )
    await insert_historical(
        session,
        WorkSandboxLease(
            task_id=task.id,
            run_id=run.id,
            attempt_id=run.attempt_id,
            sandbox_id='original-closed-sandbox',
            image='preserved-runtime:1',
            status='closed',
            expires_at=utcnow() - timedelta(minutes=5),
        ),
    )
    await session.execute(
        text('UPDATE work_tasks SET workspace_files=CAST(:files AS jsonb) WHERE id=:id'),
        {
            'id': task.id,
            'files': json.dumps([{'path': 'outputs/report.txt', 'file_id': str(output.id), 'sha256': output.sha256}]),
        },
    )
    await checkpoint(session, 'work_checkpoints', task.id)


@pytest.mark.parametrize('starting_revision', STARTING_REVISIONS)
async def test_all_deployed_paths_join_one_head_without_rewriting_facts(
    settings: Settings, starting_revision: str
) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    before = {}
    try:
        if starting_revision != 'empty':
            await _version(settings, starting_revision)
            async with AsyncSession(engine) as session, session.begin():
                conversation_id, task_id = await seed_history(session, starting_revision)
            async with engine.connect() as connection:
                before = await snapshot(connection)
        await migrate(settings)
        async with engine.connect() as connection:
            assert tuple(await connection.scalars(text('SELECT version_num FROM alembic_version'))) == (
                SCHEMA_REVISION,
            )
            after = await snapshot(connection)
            for (schema, table), rows in before.items():
                assert_original_rows(rows, after[schema, table], table)
            assert (
                await connection.run_sync(
                    lambda sync: compare_metadata(active_migration_context(sync), schema_metadata())
                )
                == []
            )
            for schema in ('assistant_checkpoints', 'work_checkpoints'):
                assert await connection.scalar(text(f'SELECT count(*) FROM {schema}.checkpoint_migrations')) == 10
            if starting_revision != 'empty':
                attachments = await connection.scalar(
                    text('SELECT attachments FROM assistant_turns WHERE conversation_id=:id'), {'id': conversation_id}
                )
                assert bool(attachments) == (starting_revision == '0041_assistant_attachments')
                assert await connection.scalar(text('SELECT mode FROM work_tasks WHERE id=:id'), {'id': task_id}) == (
                    'agent' if starting_revision == '0041_work_agent' else 'legacy'
                )
        database = Database(settings.database_url.get_secret_value())
        try:
            await database.require_schema()
        finally:
            await database.close()
    finally:
        await engine.dispose()


async def test_api_rejects_two_unmerged_heads_until_merge_is_applied(settings: Settings) -> None:
    await _version(settings, '0041_work_agent')
    await _version(settings, '0041_assistant_attachments')
    database = Database(settings.database_url.get_secret_value())
    try:
        with pytest.raises(AppError, match='版本与应用不匹配'):
            await database.require_schema()
        await migrate(settings)
        await database.require_schema()
    finally:
        await database.close()
