"""Uploaded immutable skill files reach the real isolated workspace runtime."""

import hashlib
import json
import os
from dataclasses import replace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_work_agent_execution import (
    SANDBOX_VARIABLES,
    consume,
    leases_for,
    turn,
    workspace_model,
)
from tests.core.integration.test_work_agent_execution import (
    workspace_provider as workspace_provider,  # noqa: PLC0414 - shared fixture
)
from tests.core.integration.test_work_agent_execution import (
    workspace_settings as workspace_settings,  # noqa: PLC0414 - shared fixture
)
from tests.core.integration.test_work_agent_resume import continue_task
from tests.core.unit.test_skill_package import archive, markdown
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.application.work.tools import WorkspaceTools
from zhiheng.modules.skills.orm import SkillRevision
from zhiheng.modules.skills.public import resolve_selection
from zhiheng.shared.errors import AppError

SLUG = 'package-report'
SCRIPT = b"""from pathlib import Path
root = Path(__file__).resolve().parents[1]
assert (root / 'assets/value.txt').read_text() == '42'
count = Path('/workspace/working/executions.txt')
count.write_text(str(int(count.read_text()) + 1) if count.exists() else '1')
Path('/workspace/outputs/report.txt').write_text('result=' + (root / 'assets/value.txt').read_text())
print('report created')
"""


def package(value: bytes = b'42') -> bytes:
    return archive(
        [
            ('bundle/SKILL.md', markdown(SLUG, 'Run scripts/report.py using assets/value.txt.')),
            ('bundle/scripts/report.py', SCRIPT),
            ('bundle/assets/value.txt', value),
        ]
    )


async def install_package(admin: httpx.AsyncClient, content: bytes | None = None) -> dict:
    prepared = await admin.post(
        '/api/skills/import/prepare?filename=report.zip',
        content=content if content is not None else package(),
        headers={'content-type': 'application/octet-stream'},
    )
    assert prepared.status_code == 201, prepared.text
    confirmed = await admin.post(f'/api/skills/import/{prepared.json()["draft_id"]}/confirm', json={})
    assert confirmed.status_code == 201, confirmed.text
    return confirmed.json()


async def test_frozen_package_keeps_revision_and_checks_owner_and_revocation(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    original = await install_package(admin)
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    container = app.state.container
    async with container.database.sessions() as session:
        value = (await resolve_selection(session, owner_id, [SLUG])).revisions[0]
    await install_package(admin, package(b'99'))
    async with container.database.sessions() as session:
        loaded = await container.skills.frozen_package(session, owner_id, value)
        assert dict(loaded.files)['assets/value.txt'] == b'42'
        current = (await resolve_selection(session, owner_id, [SLUG])).revisions[0]
        assert current.revision_id != value.revision_id
        assert (
            dict((await container.skills.frozen_package(session, owner_id, current)).files)['assets/value.txt'] == b'99'
        )
        for identifier, selected in [(uuid4(), value), (owner_id, replace(value, skill_id=uuid4()))]:
            with pytest.raises(AppError, match='所选技能不存在或已停用'):
                await container.skills.frozen_package(session, identifier, selected)
    disabled = await admin.patch(
        f'/api/skills/{original["id"]}/status', json={'status': 'disabled', 'expected_version': 2}
    )
    assert disabled.status_code == 200, disabled.text
    async with container.database.sessions() as session:
        with pytest.raises(AppError, match='所选技能不存在或已停用'):
            await container.skills.frozen_package(session, owner_id, value)


@pytest.mark.parametrize('tamper', ['bytes', 'tree', 'traversal'])
async def test_frozen_package_revalidates_archive_and_tree(admin: httpx.AsyncClient, app: FastAPI, tamper: str) -> None:
    await install_package(admin)
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    container = app.state.container
    async with container.database.sessions() as session:
        value = (await resolve_selection(session, owner_id, [SLUG])).revisions[0]
    content = (
        package(b'changed')
        if tamper != 'traversal'
        else archive(
            [
                ('SKILL.md', markdown(SLUG)),
                ('../escape.py', b'print(1)'),
            ]
        )
    )
    changes = {'package_bytes': content}
    if tamper != 'bytes':
        changes['package_sha'] = hashlib.sha256(content).hexdigest()
    async with container.database.sessions.begin() as session:
        original = await session.scalar(select(SkillRevision).where(SkillRevision.id == value.revision_id))
        assert original is not None
        # Revisions reject UPDATE at the database layer. Simulate damaged persisted
        # data with a distinct test revision, retaining the frozen tree identity.
        corrupted = SkillRevision(
            **{
                column.name: getattr(original, column.name)
                for column in SkillRevision.__table__.columns
                if column.name != 'id'
            }
        )
        corrupted.id = uuid4()
        for name, content in changes.items():
            setattr(corrupted, name, content)
        session.add(corrupted)
        value = replace(value, revision_id=corrupted.id)
    async with container.database.sessions() as session:
        with pytest.raises(AppError) as error:
            await container.skills.frozen_package(session, owner_id, value)
    assert error.value.code == ('invalid_skill_package' if tamper == 'traversal' else 'skill_package_integrity_error')


async def test_loading_package_returns_inventory_without_provisioning_or_executing(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await install_package(admin)

    def response(body: dict) -> dict:
        returned = [item for item in body['messages'] if item['role'] == 'tool']
        if body.get('tools') and not returned:
            return workspace_provider.tool('load_skill', {'name': SLUG}, 'load-package')
        if body.get('tools'):
            loaded = json.loads(returned[0]['content'])['data']
            assert loaded['workspace_path'] == f'inputs/skills/{SLUG}'
            assert {item['path'] for item in loaded['files']} == {'SKILL.md', 'scripts/report.py', 'assets/value.txt'}
        return {'role': 'assistant', 'content': '已查看方法，尚未运行脚本。'}

    monkeypatch.setattr(workspace_provider, 'response', response)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': '只查看技能',
            'skill_slugs': [SLUG],
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    first = await turn(admin, task, task['goal'], initial=True)
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{first["id"]}')).json()
    assert final['status'] == 'completed', final
    assert not await leases_for(app)


@pytest.mark.skipif(not all(os.getenv(key) for key in SANDBOX_VARIABLES), reason='Real OpenSandbox opt-in is required')
async def test_uploaded_script_and_resource_run_once_and_resume_after_delivery_failure(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await install_package(admin)
    deliveries = 0
    finalize = WorkspaceTools.finalize_outputs

    async def finish(self: WorkspaceTools) -> None:
        nonlocal deliveries
        await finalize(self)
        deliveries += 1
        if deliveries <= 2:
            raise AppError('work_artifact_invalid', 'test finalization acknowledgement failure', 422)

    def response(body: dict) -> dict:
        if not body.get('tools'):
            raise AssertionError('An extra delivery model must not run')
        returned = [
            item
            for item in body['messages']
            if item['role'] == 'tool' and 'loaded_toolsets' not in (json.loads(item['content']).get('data') or {})
        ]
        if not returned:
            return workspace_provider.tool('load_skill', {'name': SLUG}, 'load-package')
        if len(returned) == 1:
            if 'execute' not in {tool['function']['name'] for tool in body.get('tools', [])}:
                return workspace_provider.tool('load_tools', {'groups': ['shell']}, 'load-shell-tools')
            return workspace_provider.tool(
                'execute',
                {
                    'command': f'python inputs/skills/{SLUG}/scripts/report.py',
                },
                'run-package',
            )
        if len(returned) == 2:
            return workspace_provider.tool('publish_outputs', {}, 'publish-package')
        if deliveries and len(returned) == 3:
            if 'run_python' not in {tool['function']['name'] for tool in body.get('tools', [])}:
                return workspace_provider.tool('load_tools', {'groups': ['python']}, 'load-python-tools')
            # load_skill is already checkpointed: files must reappear in the new
            # sandbox without replaying either the load or business script.
            return workspace_provider.tool(
                'run_python',
                {
                    'code': f"""
from pathlib import Path
root = Path('inputs/skills/{SLUG}')
assert (root / 'scripts/report.py').is_file()
assert (root / 'assets/value.txt').read_text() == '42'
assert Path('working/executions.txt').read_text() == '1'
try:
    (root / 'assets/value.txt').write_text('tampered')
except OSError:
    pass
else:
    raise AssertionError('skill files must be readonly')
print('restored and readonly')
"""
                },
                'verify-restored',
            )
        for message in returned:
            assert json.loads(message['content'])['success'], message
        return {'role': 'assistant', 'content': '已核对脚本结果。'}

    monkeypatch.setattr(workspace_provider, 'response', response)
    monkeypatch.setattr(WorkspaceTools, 'finalize_outputs', finish)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': '使用技能制作报告',
            'skill_slugs': [SLUG],
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    first = await turn(admin, task, task['goal'], initial=True)
    await consume(app, seconds=150)
    failed = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{first["id"]}')).json()
    assert failed['status'] == 'failed', failed
    resumed = await continue_task(admin, task['id'])
    await consume(app, seconds=150)
    failed_again = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed["id"]}')).json()
    assert failed_again['status'] == 'failed', failed_again
    resumed_again = await continue_task(admin, task['id'])
    await consume(app, seconds=150)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed_again["id"]}')).json()
    assert final['status'] == 'completed', final
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert [item['content'] for item in detail['messages'] if item['role'] == 'user'] == [task['goal']]
    assert len([item for item in detail['steps'] if item['name'] == 'load_skill']) == 1
    assert len([item for item in detail['steps'] if item['name'] == 'execute']) == 1
    assert len(detail['artifacts']) == 1
    artifact = detail['artifacts'][0]
    assert artifact['version'] == 1
    downloaded = await admin.get(f'/api/work/tasks/{task["id"]}/artifacts/{artifact["id"]}/download')
    assert downloaded.status_code == 200 and downloaded.content == b'result=42'
    assert len(await leases_for(app)) == 3
    assert all(item.status == 'closed' for item in await leases_for(app))
