from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.infrastructure.database import SCHEMA_REVISION
from zhiheng.modules.chat.orm import Conversation
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def snapshot(connection: AsyncConnection, columns: dict[tuple[str, str], list[str]]) -> dict[str, list[str]]:
    result = {}
    for (schema, table), names in columns.items():
        projection = ','.join(quoted(name) for name in names)
        qualified = quoted(schema) + '.' + quoted(table)
        result[schema + '.' + table] = list(
            await connection.scalars(
                text(f'SELECT to_jsonb(t)::text FROM (SELECT {projection} FROM {qualified}) t ORDER BY 1')
            )
        )
    return result


async def test_message_actions_upgrade_preserves_all_original_columns_and_rows(settings: object) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def original_schema(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, '0008_graph')

    try:
        async with engine.begin() as connection:
            await connection.run_sync(original_schema)
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            # Freeze the original user columns: the current ORM also writes
            # admin-console fields that do not exist in the 0008 fixture.
            owner_id = uuid4()
            await session.execute(
                text("""INSERT INTO identity_users (id,username,password_hash,is_admin,is_active)
                    VALUES (:id,'upgrade-owner','preserve-this-test-hash',true,true)"""),
                {'id': owner_id},
            )
            conversation = Conversation(owner_id=owner_id, title='升级前的真实字段')
            session.add(conversation)
            await session.flush()
            run = Run(
                owner_id=owner_id,
                billing_owner_id=owner_id,
                resource_type='chat',
                resource_id=conversation.id,
                client_request_id=uuid4(),
                fingerprint='f' * 64,
                payload={},
                status='completed',
                text='原回复完整保留',
                usage_status='known',
            )
            session.add(run)
            await session.flush()
            for role, content in [('user', '原提问完整保留'), ('assistant', '原回复完整保留')]:
                await session.execute(
                    text(
                        'INSERT INTO chat_messages (id,conversation_id,run_id,role,content,is_complete,created_at) VALUES (:id,:conversation,:run,:role,:content,true,:created)'
                    ),
                    {
                        'id': uuid4(),
                        'conversation': conversation.id,
                        'run': run.id,
                        'role': role,
                        'content': content,
                        'created': utcnow(),
                    },
                )
        columns: dict[tuple[str, str], list[str]] = {}
        async with engine.connect() as connection:
            for schema, table, column in (
                await connection.execute(
                    text(
                        "SELECT table_schema,table_name,column_name FROM information_schema.columns WHERE table_schema IN ('public','knowledge_graph_sdk') AND table_name<>'alembic_version' ORDER BY table_schema,table_name,ordinal_position"
                    )
                )
            ).all():
                columns.setdefault((schema, table), []).append(column)
            before = await snapshot(connection, columns)
        await migrate(settings)
        async with engine.connect() as connection:
            assert await snapshot(connection, columns) == before
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
            assert (
                await connection.execute(
                    text('SELECT reply_to_message_id,generation_index,request_context FROM chat_messages')
                )
            ).all() == [(None, 1, {}), (None, 1, {})]
            assert await connection.scalar(text('SELECT count(*) FROM chat_message_feedback')) == 0
            assert await connection.scalar(text('SELECT count(*) FROM chat_message_shares')) == 0

        def restore_original_schema(connection: Connection) -> None:
            config.attributes['connection'] = connection
            command.downgrade(config, '0008_graph')

        async with engine.begin() as connection:
            await connection.run_sync(restore_original_schema)
        async with engine.connect() as connection:
            assert await snapshot(connection, columns) == before
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0008_graph'
        await migrate(settings)
        async with engine.connect() as connection:
            assert await snapshot(connection, columns) == before
    finally:
        await engine.dispose()
