"""Folder ZIP uploads use the same skill tree and retain the original archive."""

from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_resource_files import auth, post
from tests.core.integration.test_resource_skills import setup
from tests.core.unit.test_skill_package import archive, markdown
from zhiheng.modules.skills.orm import SkillRevision


@pytest.mark.parametrize('protocol', ['console', 'resource'])
async def test_folder_archive_previews_installs_and_displays_original_markdown(
    admin: httpx.AsyncClient, app: FastAPI, protocol: str
) -> None:
    text = markdown('writing-assistant', 'Write clear and accurate prose.').replace(b'\n', b'\r\n')
    entries = [('SKILL.md', text), ('agents/openai.yaml', b'interface:\n  display_name: Writing Assistant\n')]
    payload = archive([('writing-assistant/' + name, data) for name, data in entries])
    if protocol == 'console':
        response = await admin.post(
            '/api/skills/import/prepare',
            params={'filename': 'writing-assistant.zip'},
            content=payload,
            headers={'Content-Type': 'application/octet-stream'},
        )
        assert response.status_code == 201, response.text
        draft = response.json()
        confirmed = await admin.post(f'/api/skills/import/{draft["draft_id"]}/confirm', json={})
        assert confirmed.status_code == 201, confirmed.text
        skill_id, revision_id = confirmed.json()['id'], confirmed.json()['revision_id']
    else:
        _owned, key = await setup(admin)
        uploaded = await post(
            admin,
            key,
            files={'file': ('writing-assistant.zip', payload, 'application/zip')},
            data={'purpose': 'skill'},
        )
        assert uploaded.status_code == 201, uploaded.text
        response = await admin.post('/api/v1/skill-imports', headers=auth(key), json={'file_id': uploaded.json()['id']})
        assert response.status_code == 201, response.text
        draft = response.json()
        confirmed = await admin.post(f'/api/v1/skill-imports/{draft["draft_id"]}/confirm', headers=auth(key))
        assert confirmed.status_code == 201, confirmed.text
        skill_id, revision_id = confirmed.json()['id'], confirmed.json()['current_revision_id']
    assert draft['slug'] == 'writing-assistant' and draft['file_count'] == 2
    assert (await admin.get(f'/api/skills/{skill_id}/content')).json()['content'] == text.decode()
    async with app.state.container.database.sessions() as session:
        stored = await session.get(SkillRevision, UUID(revision_id))
        assert stored is not None and stored.package_bytes == payload
    repeated = await admin.post(
        '/api/skills/import/prepare',
        params={'filename': 'flat.zip'},
        content=archive(entries),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert repeated.status_code == 201 and repeated.json()['action'] == 'unchanged', repeated.text


async def test_missing_main_file_returns_actionable_http_error(admin: httpx.AsyncClient) -> None:
    response = await admin.post(
        '/api/skills/import/prepare',
        params={'filename': 'missing.zip'},
        content=archive([('README.md', b'Missing')]),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'invalid_skill_package'
    assert 'SKILL.md' in response.json()['error']['message']
