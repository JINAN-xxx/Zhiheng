"""Construct an old schema with realistic fixture data in an exclusive test database.

Ownership cannot be downgraded safely. Migration tests instead reconstruct the
old schema and insert only its original columns, retaining every original value.
"""

from collections.abc import Callable

from sqlalchemy import MetaData, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection


async def reconstruct(
    connection: AsyncConnection, run_upgrade: Callable[[Connection], None], *, preserve_personal_skills: bool = False
) -> None:
    name = await connection.scalar(text('SELECT current_database()'))
    assert isinstance(name, str) and name.startswith('core_test_'), 'Exclusive fixture database required'
    current = MetaData()
    await connection.run_sync(current.reflect)
    rows = {
        table.name: [dict(row) for row in (await connection.execute(select(table))).mappings()]
        for table in current.sorted_tables
        if table.name != 'alembic_version'
    }
    if 'skills' in rows and not preserve_personal_skills:
        rows['skills'] = [row for row in rows['skills'] if row['owner_id'] is None or row['source'] == 'upload']
        retained = {row['id'] for row in rows['skills']}
        rows['skill_revisions'] = [row for row in rows['skill_revisions'] if row['skill_id'] in retained]
    await connection.execute(text('DROP SCHEMA IF EXISTS knowledge_graph_sdk CASCADE'))
    await connection.execute(text('DROP SCHEMA public CASCADE'))
    await connection.execute(text('CREATE SCHEMA public'))
    await connection.run_sync(run_upgrade)
    historical = MetaData()
    await connection.run_sync(historical.reflect)
    for table in historical.sorted_tables:
        original = rows.get(table.name, [])
        if original:
            if table.name == 'identity_roles':
                # Reuse the fixture's protected role identity before memberships.
                await connection.execute(table.delete())
            values = [{column.name: row[column.name] for column in table.columns} for row in original]
            statement = insert(table).values(values)
            if table.name == 'skills':
                statement = statement.on_conflict_do_update(
                    index_elements=['id'],
                    set_={
                        column.name: statement.excluded[column.name] for column in table.columns if column.name != 'id'
                    },
                )
            else:
                statement = statement.on_conflict_do_nothing()
            await connection.execute(statement)
