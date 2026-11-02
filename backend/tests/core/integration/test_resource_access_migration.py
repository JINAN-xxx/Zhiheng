"""Upgrade populated schema 14 rows and preserve every original column and money fact."""

from uuid import uuid4

import httpx
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.legacy_schema import reconstruct
from zhiheng.bootstrap.migrations import migrate, migration_directory


async def test_0014_upgrade_preserves_legacy_key_financial_and_model_facts(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI
) -> None:
    owner_app = await application(admin)
    old = await issue(
        admin, owner_app['id'], model_ids=[model_id], monthly_points_limit='12.345', monthly_budget='45.67'
    )
    rate = await admin.post(
        f'/api/models/{model_id}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.00123', 'output_price': '0.00456'},
    )
    assert rate.status_code == 201, rate.text
    c = app.state.container
    cfg = Config()
    cfg.set_main_option('script_location', str(migration_directory()))

    def original_schema(connection: Connection) -> None:
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, '0014_personal_skill_ownership')

    async with c.database.engine.begin() as connection:
        await reconstruct(connection, original_schema, preserve_personal_skills=True)
        await connection.execute(
            text(
                'INSERT INTO billing_accounts(user_id,allocated_points,used_points,reserved_points) VALUES (:owner,123.456,12.3,4.5) ON CONFLICT(user_id) DO UPDATE SET allocated_points=123.456,used_points=12.3,reserved_points=4.5'
            ),
            {'owner': owner_app['owner_id']},
        )
        await connection.execute(text('UPDATE billing_control SET enabled=true,version=3'))
        # Insert a real old-schema key without any scope/RPM columns.
        legacy_id = uuid4()
        await connection.execute(
            text("""INSERT INTO developer_keys
            (id,application_id,name,prefix,digest,status,model_ids,rpm_limit,tpm_limit,concurrency_limit,
             monthly_budget,monthly_points_limit,currency,created_at,expires_at,retire_at,last_used_at)
            SELECT :id,application_id,'pre-0015','sk-legacy',:digest,status,model_ids,rpm_limit,tpm_limit,
                   concurrency_limit,monthly_budget,monthly_points_limit,currency,created_at,expires_at,retire_at,last_used_at
            FROM developer_keys WHERE id=:original"""),
            {'id': legacy_id, 'digest': 'f' * 64, 'original': old['id']},
        )
        names = list(
            await connection.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename"
                )
            )
        )
        columns = {
            name: list(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:name ORDER BY ordinal_position"
                    ),
                    {'name': name},
                )
            )
            for name in names
        }
        before = {
            name: list(await connection.scalars(text(f'SELECT to_jsonb(t)::text FROM "{name}" t ORDER BY 1')))
            for name in names
        }
        assert (
            await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0014_personal_skill_ownership'
        )
    await migrate(c.settings)
    async with c.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0015_resource_api_access'
        for name in names:
            projection = ','.join('"' + column + '"' for column in columns[name])
            after = list(
                await connection.scalars(
                    text(f'SELECT to_jsonb(t)::text FROM (SELECT {projection} FROM "{name}") t ORDER BY 1')
                )
            )
            assert after == before[name], name
        rows = (await connection.execute(text('SELECT scopes,resource_rpm_limit FROM developer_keys'))).all()
        assert len(rows) == 2 and all(
            set(row.scopes) == {'models:invoke', 'agents:invoke'} and row.resource_rpm_limit == 120 for row in rows
        )
        for name in ('developer_resource_grants', 'developer_source_enrollments', 'developer_api_requests'):
            assert await connection.scalar(text(f'SELECT count(*) FROM {name}')) == 0
