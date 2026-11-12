"""Closing platform-wide sharing preserves documents, members, and private invitations."""

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.integration.test_knowledge_materials_v2 import reader
from tests.core.provider import ProviderServer
from tests.core.schema_support import active_migration_context, assert_original_rows
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata


async def test_link_only_upgrade_closes_global_access_without_changing_members_or_materials(  # noqa: PLR0915 - keep downgrade, upgrade and preservation checks in one fixture lifecycle
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    created = await admin.post(
        '/api/knowledge/bases', json={'name': 'Keep private', 'embed_model_id': base['embed_model_id']}
    )
    assert created.status_code == 201
    private = created.json()
    container = app.state.container
    member = await reader(app)
    outsider = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
    try:
        async with container.database.sessions.begin() as session:
            await container.accounts.create_user(
                session, username='not-invited', password='platform-test-password', is_admin=False
            )
        assert (
            await outsider.post(
                '/api/auth/login', json={'username': 'not-invited', 'password': 'platform-test-password'}
            )
        ).status_code == 200

        async def issue(target: dict[str, Any], version: int) -> str:
            response = await admin.post(
                '/api/knowledge/bases/' + target['id'] + '/invitations',
                json={'expected_acl_version': version, 'client_request_id': str(uuid4())},
            )
            assert response.status_code == 201, response.text
            return response.json()['token']

        accepted = await issue(base, 1)
        assert (await member.post('/api/knowledge/invitations/accept', json={'token': accepted})).status_code == 200
        pending = await issue(base, 2)
        private_token = await issue(private, 1)
        config = Config()
        config.set_main_option('script_location', str(migration_directory()))

        def previous(connection: Connection) -> None:
            config.attributes['connection'] = connection
            command.downgrade(config, '0022_knowledge_invitations')

        async with container.database.engine.begin() as connection:
            await connection.run_sync(previous)
            await connection.execute(
                text("UPDATE knowledge_bases SET access='public' WHERE id=:id"), {'id': UUID(base['id'])}
            )
            before_bases = {
                str(row.id): row._asdict()
                for row in await connection.execute(
                    text('SELECT id,access,version,acl_version,invite_epoch,updated_at FROM knowledge_bases')
                )
            }
            tables = list(
                await connection.scalars(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename!='alembic_version' ORDER BY tablename"
                    )
                )
            )
            before = {}
            for table in tables:
                projection = (
                    "to_jsonb(t)-'access'-'acl_version'-'invite_epoch'-'updated_at'"
                    if table == 'knowledge_bases'
                    else 'to_jsonb(t)'
                )
                before[table] = list(
                    await connection.scalars(
                        text(
                            'SELECT ('
                            + projection
                            + ')::text FROM "'
                            + table
                            + '" t ORDER BY ('
                            + projection
                            + ')::text'
                        )
                    )
                )
        await migrate(container.settings)
        async with container.database.engine.connect() as connection:
            for table in tables:
                projection = (
                    "to_jsonb(t)-'access'-'acl_version'-'invite_epoch'-'updated_at'"
                    if table == 'knowledge_bases'
                    else 'to_jsonb(t)'
                )
                after = list(
                    await connection.scalars(
                        text(
                            'SELECT ('
                            + projection
                            + ')::text FROM "'
                            + table
                            + '" t ORDER BY ('
                            + projection
                            + ')::text'
                        )
                    )
                )
                assert_original_rows(before[table], after, table)
            for row in await connection.execute(
                text('SELECT id,access,version,acl_version,invite_epoch,updated_at FROM knowledge_bases')
            ):
                old = before_bases[str(row.id)]
                changed = old['access'] == 'public'
                assert row.access == 'private' and row.version == old['version']
                assert row.acl_version == old['acl_version'] + changed
                assert row.invite_epoch == old['invite_epoch'] + changed
                assert row.updated_at >= old['updated_at'] if changed else row.updated_at == old['updated_at']

            def differences(sync: Connection) -> list[Any]:
                return compare_metadata(active_migration_context(sync), schema_metadata())

            assert await connection.run_sync(differences) == []
        for operation in ('preview', 'accept'):
            assert (
                await outsider.post('/api/knowledge/invitations/' + operation, json={'token': pending})
            ).status_code == 404
        assert (await outsider.get('/api/knowledge/bases/' + base['id'])).status_code == 404
        assert base['id'] not in json.dumps((await outsider.get('/api/knowledge/bases')).json())
        assert (await member.get('/api/knowledge/bases/' + base['id'] + '/documents')).json()['total'] == 1
        assert (
            await member.post('/api/knowledge/invitations/preview', json={'token': private_token})
        ).status_code == 200
        with pytest.raises(IntegrityError):
            async with container.database.sessions.begin() as session:
                await session.execute(
                    text("UPDATE knowledge_bases SET access='public' WHERE id=:id"), {'id': UUID(base['id'])}
                )
    finally:
        await member.aclose()
        await outsider.aclose()
