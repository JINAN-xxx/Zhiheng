from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.provider import ProviderServer
from tests.core.unit.test_skill_package import markdown
from zhiheng.modules.files.public import IssueAction, PolicyRequest, RetentionPolicyRequest
from zhiheng.modules.models.public import RateCreate
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


@asynccontextmanager
async def account(
    admin: httpx.AsyncClient, app: FastAPI, *permissions: str
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, Any], dict[str, Any]]]:
    suffix = uuid4().hex[:12]
    role = await admin.post('/api/admin/roles', json={'code': suffix, 'name': suffix, 'permissions': list(permissions)})
    assert role.status_code == 201, role.text
    user = await admin.post(
        '/api/admin/users',
        json={'username': suffix, 'password': 'platform-test-password', 'role_ids': [role.json()['id']]},
    )
    assert user.status_code == 201, user.text
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'content-type': 'application/json'}
    ) as client:
        login = await client.post('/api/auth/login', json={'username': suffix, 'password': 'platform-test-password'})
        assert login.status_code == 200, login.text
        assert login.json()['user']['is_admin'] is False
        assert set(login.json()['user']['permissions']) == set(permissions)
        yield client, role.json(), user.json()


async def test_model_management_read_write_and_basic_usage_are_independent(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    async with account(admin, app) as (member, _, _):
        assert (await member.get('/api/models')).status_code == 200
        assert (await member.get('/api/models?include_disabled=true')).status_code == 403
        assert (await member.get('/api/models/connections')).status_code == 403
        conversation = await member.post('/api/chat/conversations', json={})
        assert conversation.status_code == 201, conversation.text
        run = await member.post(
            f'/api/chat/conversations/{conversation.json()["id"]}/runs',
            json={'model_id': model_id, 'message': 'hello', 'client_request_id': str(uuid4())},
        )
        assert run.status_code == 202, run.text
    async with account(admin, app, 'ai:models:read') as (reader, _, _):
        connections = await reader.get('/api/models/connections')
        assert connections.status_code == 200, connections.text
        assert 'local-test-key' not in connections.text and 'encrypted_api_key' not in connections.text
        assert (await reader.get('/api/models?include_disabled=true')).status_code == 200
        connection_id = connections.json()[0]['id']
        for response in [
            await reader.post(
                '/api/models/connections', json={'name': 'deny', 'base_url': 'http://localhost/v1', 'api_key': 'secret'}
            ),
            await reader.patch(f'/api/models/connections/{connection_id}', json={'name': 'deny'}),
            await reader.post(f'/api/models/connections/{connection_id}/disable', json={}),
            await reader.delete(f'/api/models/connections/{connection_id}'),
            await reader.post(
                '/api/models', json={'name': 'deny', 'connection_id': connection_id, 'upstream_model': 'test'}
            ),
            await reader.patch(f'/api/models/{model_id}', json={'name': 'deny'}),
            await reader.post(f'/api/models/{model_id}/disable', json={}),
            await reader.delete(f'/api/models/{model_id}'),
            await reader.post(f'/api/models/{model_id}/test', json={'client_request_id': str(uuid4())}),
            await reader.get(f'/api/models/{model_id}/rate-cards'),
        ]:
            assert response.status_code == 403, response.text
    async with account(admin, app, 'ai:models:write') as (writer, _, _):
        assert (await writer.get('/api/models/connections')).status_code == 200
        assert (await writer.get('/api/models?include_disabled=true')).status_code == 200
        connection = await writer.post(
            '/api/models/connections',
            json={
                'name': 'delegated',
                'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
                'api_key': 'private-key',
            },
        )
        assert connection.status_code == 201 and 'private-key' not in connection.text, connection.text
        connection_id = connection.json()['id']
        assert (
            await writer.patch(f'/api/models/connections/{connection_id}', json={'api_key': 'rotated-private-key'})
        ).status_code == 200
        for action in ['disable', 'enable']:
            assert (await writer.post(f'/api/models/connections/{connection_id}/{action}', json={})).status_code == 200
        model = await writer.post(
            '/api/models', json={'name': 'delegated', 'connection_id': connection_id, 'upstream_model': 'local'}
        )
        assert model.status_code == 201, model.text
        identifier = model.json()['id']
        assert (await writer.patch(f'/api/models/{identifier}', json={'name': 'updated'})).status_code == 200
        for action in ['disable', 'enable']:
            assert (await writer.post(f'/api/models/{identifier}/{action}', json={})).status_code == 200
        assert (
            await writer.post(f'/api/models/{model_id}/test', json={'client_request_id': str(uuid4())})
        ).status_code == 403
        assert (await writer.get(f'/api/models/{model_id}/rate-cards')).status_code == 403
        assert (await writer.delete(f'/api/models/{identifier}')).status_code == 200
        spare = await writer.post(
            '/api/models/connections', json={'name': 'unused', 'base_url': 'http://localhost/v1', 'api_key': 'secret'}
        )
        assert (await writer.delete(f'/api/models/connections/{spare.json()["id"]}')).status_code == 200
        assert (await writer.get('/api/admin/users')).status_code == 403


async def test_model_testing_uses_existing_quota_ledger_idempotency_and_current_role(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    from tests.core.billing_support import enable_free_billing

    await enable_free_billing(admin)
    from zhiheng.worker.execution import execute_run

    async with account(admin, app, 'ai:models:test') as (tester, role, _):
        path = f'/api/models/{model_id}/test'
        body = {'client_request_id': str(uuid4())}
        result = await tester.post(path, json=body)
        assert result.status_code == 202, result.text
        retry = await tester.post(path, json=body)
        assert retry.status_code == 202 and retry.json()['id'] == result.json()['id']
        usage = (await tester.get('/api/usage')).json()
        assert usage['reserved_tokens'] > 0 and usage['used_tokens'] == 0
        await execute_run(app.state.container, UUID(result.json()['id']))
        usage = (await tester.get('/api/usage')).json()
        assert usage['reserved_tokens'] == 0 and usage['used_tokens'] > 0
        assert len(usage['records']) == 1 and len(provider.requests) == 1
        assert (await tester.get('/api/models/connections')).status_code == 403
        assert (await tester.get(f'/api/models/{model_id}/rate-cards')).status_code == 403
        revoked = await admin.patch(
            f'/api/admin/roles/{role["id"]}', json={'permissions': [], 'expected_version': role['version']}
        )
        assert revoked.status_code == 200, revoked.text
        assert (await tester.post(path, json=body)).status_code == 403
        assert (await tester.post(path, json={'client_request_id': str(uuid4())})).status_code == 403
        assert len(provider.requests) == 1


async def test_rate_read_and_write_do_not_grant_model_configuration_or_testing(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    path = f'/api/models/{model_id}/rate-cards'
    body = {'billing_mode': 'token', 'input_price': '0.01'}
    async with account(admin, app, 'ai:rates:read') as (reader, _, _):
        assert (await reader.get(path)).status_code == 200
        assert (await reader.post(path, json=body)).status_code == 403
        assert (await reader.get('/api/models/connections')).status_code == 403
        assert (
            await reader.post(f'/api/models/{model_id}/test', json={'client_request_id': str(uuid4())})
        ).status_code == 403
    async with account(admin, app, 'ai:rates:write') as (writer, _, _):
        assert (await writer.get(path)).status_code == 200
        created = await writer.post(path, json=body)
        assert created.status_code == 201 and created.json()['version'] == 1, created.text
        assert (await writer.get('/api/models?include_disabled=true')).status_code == 403
        assert (await writer.patch(f'/api/models/{model_id}', json={'name': 'deny'})).status_code == 403
        assert (
            await writer.post(f'/api/models/{model_id}/test', json={'client_request_id': str(uuid4())})
        ).status_code == 403


async def test_shared_skills_require_write_and_drafts_remain_bound_to_creator(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    # Keep this historical node ID while enforcing the personal-resource contract.
    async with account(admin, app) as (writer, _, _):
        draft = await writer.post(
            '/api/skills/import/prepare?filename=SKILL.md',
            content=markdown('delegated'),
            headers={'content-type': 'application/octet-stream'},
        )
        assert draft.status_code == 201, draft.text
        confirm_path = f'/api/skills/import/{draft.json()["draft_id"]}/confirm'
        async with account(admin, app, 'ai:skills:write') as (other, _, _):
            assert (await other.post(confirm_path, json={})).status_code == 404
        async with account(admin, app) as (member, _, _):
            assert (await member.get('/api/skills/available')).status_code == 200
            assert (
                await member.post(
                    '/api/skills/import/prepare?filename=SKILL.md',
                    content=markdown(),
                    headers={'content-type': 'application/octet-stream'},
                )
            ).status_code == 201
            assert (await member.post(confirm_path, json={})).status_code == 404
        installed = await writer.post(confirm_path, json={})
        assert installed.status_code == 201, installed.text
        identifier = installed.json()['id']
        async with account(admin, app) as (member, _, _):
            assert (
                await member.patch(
                    f'/api/skills/{identifier}/status', json={'status': 'disabled', 'expected_version': 1}
                )
            ).status_code == 404
            assert (await member.delete(f'/api/skills/{identifier}')).status_code == 404
        disabled = await writer.patch(
            f'/api/skills/{identifier}/status', json={'status': 'disabled', 'expected_version': 1}
        )
        assert disabled.status_code == 200, disabled.text
        assert (await writer.delete(f'/api/skills/{identifier}')).status_code == 200


async def test_storage_governance_read_write_preserve_private_file_acl(admin: httpx.AsyncClient, app: FastAPI) -> None:
    uploaded = await admin.post(
        '/api/files?filename=private.txt',
        content=b'private-source',
        headers={'content-type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    identifier = uploaded.json()['id']
    reads = [
        '/api/files/policy',
        '/api/files/policies',
        '/api/files/jobs',
        '/api/files/reconcile/issues',
        '/api/files/reconcile/stats',
        '/api/files/users',
    ]
    async with account(admin, app) as (member, _, _):
        for path in reads:
            assert (await member.get(path)).status_code == 403, path
        own_file = await member.post(
            '/api/files?filename=own.txt',
            content=b'own-source',
            headers={'content-type': 'application/octet-stream'},
        )
        assert own_file.status_code == 201, own_file.text
        own_id = own_file.json()['id']
        removed = await member.request('DELETE', f'/api/files/{own_id}', json={'expected_version': 1})
        assert removed.status_code == 200, removed.text
        restored = await member.post(
            f'/api/files/{own_id}/restore', json={'expected_version': removed.json()['version']}
        )
        assert restored.status_code == 200, restored.text
    for permission in ['ai:storage:read', 'ai:storage:write']:
        async with account(admin, app, permission) as (operator, _, user):
            for path in reads:
                response = await operator.get(path)
                assert response.status_code == (403 if path == '/api/files/users' else 200), f'{path}: {response.text}'
            assert (await operator.get('/api/files')).json()['total'] == 0
            assert (await operator.get('/api/files/stats')).json()['total_count'] == 0
            for suffix in ['', '/download', '/acl', '/references', '/audits']:
                assert (await operator.get(f'/api/files/{identifier}{suffix}')).status_code == 404
            assert (await operator.post(f'/api/files/{identifier}/parse', json={})).status_code == 404
            assert (
                await operator.patch(f'/api/files/{identifier}', json={'display_name': 'deny', 'expected_version': 1})
            ).status_code == 404
            assert (
                await operator.patch(
                    f'/api/files/{identifier}/acl', json={'access': 'public', 'entries': [], 'expected_version': 1}
                )
            ).status_code == 404
            assert (
                await operator.post(
                    f'/api/files/{identifier}/transfer',
                    json={'owner_id': user['id'], 'expected_version': 1, 'uploader_access_enabled': False},
                )
            ).status_code == 404
            assert (await operator.get('/api/admin/users')).status_code == 403
            expected = 403 if permission.endswith(':read') else 200
            changed = await operator.patch(
                '/api/files/policy', json={'retention_days': 31, 'reminder_days': 7, 'expected_version': 1}
            )
            assert changed.status_code == expected, changed.text
            created = await operator.post('/api/files/policies', json={'resource_type': 'chat', 'retention_days': 30})
            assert created.status_code == (403 if expected == 403 else 201), created.text
            run = await operator.post('/api/files/jobs/reconcile', json={})
            assert run.status_code == expected, run.text
            changed_policy = await operator.patch(
                '/api/files/policies/chat', json={'retention_days': 31, 'expected_version': 1}
            )
            assert changed_policy.status_code == expected, changed_policy.text
            removed_policy = await operator.request('DELETE', '/api/files/policies/chat', json={'expected_version': 2})
            assert removed_policy.status_code == (403 if expected == 403 else 204), removed_policy.text
            issue = await operator.post(
                f'/api/files/reconcile/issues/{uuid4()}/actions', json={'action': 'ignore', 'expected_version': 1}
            )
            assert issue.status_code == (403 if expected == 403 else 404), issue.text
            principal = Principal(UUID(user['id']), user['username'], False, frozenset({permission}))
            async with app.state.container.database.sessions.begin() as session:
                with pytest.raises(AppError) as denied:
                    await app.state.container.files.create_owned(
                        session,
                        principal,
                        owner_id=UUID(uploaded.json()['owner_id']),
                        filename='deny.txt',
                        content=b'deny',
                        storage_key=uuid4().hex,
                    )
                assert denied.value.status_code == 403


async def test_storage_jobs_keep_owner_checks_for_purge_and_file_repairs(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    from datetime import timedelta

    from zhiheng.modules.files.orm import FileJob, FileReconcileIssue, FileRecord, FileReference
    from zhiheng.shared.errors import AppError
    from zhiheng.shared.time import utcnow

    container = app.state.container
    records = []
    for name in ['expired', 'retained', 'referenced', 'repairable']:
        uploaded = await admin.post(
            '/api/files?filename=' + name + '.txt',
            content=b'private-source',
            headers={'content-type': 'application/octet-stream'},
        )
        assert uploaded.status_code == 201
        if name != 'repairable':
            deleted = await admin.request('DELETE', '/api/files/' + uploaded.json()['id'], json={'expected_version': 1})
            assert deleted.status_code == 200
        records.append(uploaded.json())
    async with container.database.sessions.begin() as session:
        for index, info in enumerate(records):
            row = await session.get(FileRecord, UUID(info['id']))
            if index < 3:
                row.retention_until = utcnow() + timedelta(days=1 if index == 1 else -1)
            info['storage_key'] = row.storage_key
        session.add(FileReference(file_id=UUID(records[2]['id']), resource_type='chat', resource_id=uuid4()))
        job = FileJob(kind='reconcile', status='succeeded')
        session.add(job)
        await session.flush()
        issue = FileReconcileIssue(
            job_id=job.id,
            file_id=UUID(records[3]['id']),
            identity_key='owned-test:' + records[3]['id'],
            storage_key=records[3]['storage_key'],
            kind='size_or_hash',
        )
        session.add(issue)
        await session.flush()
        issue_id = issue.id
    async with account(admin, app, 'ai:storage:write') as (operator, _, _):
        purged = await operator.post('/api/files/jobs/purge', json={})
        assert purged.status_code == 200 and purged.json()['changed'] == 1, purged.text
        for info in records:
            assert (await operator.get('/api/files/' + info['id'] + '/download')).status_code == 404
            assert (
                await operator.post('/api/files/' + info['id'] + '/purge', json={'expected_version': 2})
            ).status_code == 404
        path = f'/api/files/reconcile/issues/{issue_id}/actions'
        quarantined = await operator.post(path, json={'action': 'quarantine', 'expected_version': 1})
        assert quarantined.status_code == 200 and quarantined.json()['status'] == 'quarantined'
        repaired = await operator.post(
            path, json={'action': 'repair', 'expected_version': quarantined.json()['version']}
        )
        assert repaired.status_code == 200 and repaired.json()['status'] == 'resolved'
        assert (await operator.get('/api/files/' + records[3]['id'] + '/download')).status_code == 404
    with pytest.raises(AppError):
        await container.files.storage.read(records[0]['storage_key'])
    for info in records[1:]:
        assert await container.files.storage.read(info['storage_key']) == b'private-source'
    for info in records[1:3]:
        assert (await admin.get('/api/files/' + info['id'])).json()['status'] == 'deleted'
    assert (await admin.get('/api/files/' + records[3]['id'])).json()['owner_id'] == records[3]['owner_id']


async def test_services_enforce_ai_permissions_without_http_routes(admin: httpx.AsyncClient, app: FastAPI) -> None:
    container = app.state.container
    principal = Principal(uuid4(), 'no-permissions', False)
    identifier = uuid4()

    async def unreferenced(*args: object) -> bool:
        return False

    async with container.database.sessions() as session:
        reads = [
            lambda: container.models.list_connections(session, principal),
            lambda: container.models.list_models(session, principal=principal, include_disabled=True),
            lambda: container.models.rates(session, principal, identifier),
            lambda: container.files.policy(session, principal),
            lambda: container.files.users(session, principal),
            lambda: container.files.retention_policies(session, principal),
            lambda: container.files.jobs(session, principal),
            lambda: container.files.issues(session, principal),
            lambda: container.files.issue_stats(session, principal),
        ]
        writes = [
            lambda: container.models.resolve_for_test(session, principal, identifier),
            lambda: container.models.list_models(session, principal=principal, for_test=True),
            lambda: container.models.create_connection(
                session, principal, name='deny', base_url='http://localhost', api_key='secret'
            ),
            lambda: container.models.update_connection(session, principal, identifier, {}),
            lambda: container.models.delete_connection(session, principal, identifier, reference_check=unreferenced),
            lambda: container.models.create_model(
                session, principal, name='deny', connection_id=identifier, upstream_model='deny', max_output_tokens=1
            ),
            lambda: container.models.update_model(session, principal, identifier, {}),
            lambda: container.models.delete_model(session, principal, identifier, reference_check=unreferenced),
            lambda: container.models.add_rate(session, principal, identifier, RateCreate(billing_mode='token')),
            lambda: container.admission.admit_test(
                session, principal, model_id=identifier, client_request_id=uuid4(), token_limit=1000
            ),
            lambda: container.files.set_policy(
                session, principal, PolicyRequest(retention_days=1, reminder_days=1, expected_version=1)
            ),
            lambda: container.files.set_retention(
                session, principal, RetentionPolicyRequest(resource_type='chat', retention_days=1)
            ),
            lambda: container.file_jobs.run(principal, 'reconcile'),
            lambda: container.file_jobs.issue_action(
                principal, identifier, IssueAction(action='ignore', expected_version=1)
            ),
        ]
        for operation in [*reads, *writes]:
            with pytest.raises(AppError) as denied:
                await operation()
            assert denied.value.status_code == 403
        with pytest.raises(AppError) as missing_principal:
            await container.models.list_models(session, include_disabled=True)
        assert missing_principal.value.status_code == 403
        assert await container.models.list_models(session) == []
        principal = Principal(
            principal.id,
            'read-only',
            False,
            frozenset({'ai:models:read', 'ai:rates:read', 'ai:storage:read', 'system:users:read'}),
        )
        for operation in reads:
            await operation()
        for operation in writes:
            with pytest.raises(AppError) as denied:
                await operation()
            assert denied.value.status_code == 403

    async with account(admin, app) as (_, _, user):
        owner = Principal(UUID(user['id']), user['username'], False)
    async with account(admin, app, 'ai:skills:write') as (_, _, user):
        other = Principal(UUID(user['id']), user['username'], False, frozenset({'ai:skills:write'}))
    async with container.database.sessions.begin() as session:
        draft = await container.skills.prepare(session, owner, markdown(), filename='SKILL.md')
        with pytest.raises(AppError) as foreign_draft:
            await container.skills.confirm(session, other, draft.draft_id)
        assert foreign_draft.value.status_code == 404
        installed = await container.skills.confirm(session, owner, draft.draft_id)
        assert (await container.skills.get(session, owner.id, installed.id)).id == installed.id
        for operation in [
            lambda: container.skills.get(session, other.id, installed.id),
            lambda: container.skills.set_status(
                session, other, installed.id, status='disabled', expected_version=installed.version
            ),
            lambda: container.skills.remove(session, other, installed.id, reference_check=unreferenced),
        ]:
            with pytest.raises(AppError) as foreign_skill:
                await operation()
            assert foreign_skill.value.status_code == 404
        disabled = await container.skills.set_status(
            session, owner, installed.id, status='disabled', expected_version=installed.version
        )
        assert disabled.status == 'disabled' and disabled.version == installed.version + 1
        removed = await container.skills.remove(session, owner, installed.id, reference_check=unreferenced)
        assert removed.version == disabled.version + 1
        with pytest.raises(AppError) as deleted_skill:
            await container.skills.get(session, owner.id, installed.id)
        assert deleted_skill.value.status_code == 404
