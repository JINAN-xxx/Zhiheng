import json
import os
from pathlib import Path

import pytest

from plugins.agent_extension.domain.skill_errors import SkillStorageError
from plugins.agent_extension.domain.skill_package import SkillPackageLimits, SkillPackageParser
from plugins.agent_extension.infrastructure.skill_storage import SkillStorage
from tests.plugins.agent_extension.test_skill_package import VALID_SKILL


def test_storage_uses_python_310_compatible_utc_timezone() -> None:
    source = Path('plugins/agent_extension/infrastructure/skill_storage.py').read_text(encoding='utf-8')

    assert 'from datetime import UTC' not in source
    assert 'timezone.utc' in source


def storage(tmp_path: Path, *, draft_ttl_seconds: int = 3600) -> SkillStorage:
    parser = SkillPackageParser(
        SkillPackageLimits(upload_bytes=4096, extracted_bytes=4096, file_count=10, skill_md_bytes=2048)
    )
    return SkillStorage(tmp_path / 'skills-data', parser=parser, draft_ttl_seconds=draft_ttl_seconds)


def test_prepares_and_confirms_owned_draft(tmp_path: Path) -> None:
    target = storage(tmp_path)
    prepared = target.prepare(VALID_SKILL, filename='SKILL.md', owner='admin')

    assert prepared.draft_id.startswith('draft_')
    assert target.load_draft(prepared.draft_id, owner='admin').package.slug == 'deep-research'
    relative_path = target.confirm(prepared.draft_id, owner='admin')

    assert relative_path == 'skills/deep-research'
    assert (target.root / relative_path / 'SKILL.md').is_file()
    assert not (target.drafts_dir / prepared.draft_id).exists()


def test_draft_owner_and_duplicate_install_are_enforced(tmp_path: Path) -> None:
    target = storage(tmp_path)
    first = target.prepare(VALID_SKILL, filename='SKILL.md', owner='admin')

    with pytest.raises(SkillStorageError, match='无权'):
        target.load_draft(first.draft_id, owner='other')
    target.confirm(first.draft_id, owner='admin')
    duplicate = target.prepare(VALID_SKILL, filename='SKILL.md', owner='admin')
    with pytest.raises(SkillStorageError, match='已存在'):
        target.confirm(duplicate.draft_id, owner='admin')


def test_staged_delete_can_be_restored_or_purged(tmp_path: Path) -> None:
    target = storage(tmp_path)
    first = target.prepare(VALID_SKILL, filename='SKILL.md', owner='admin')
    target.confirm(first.draft_id, owner='admin')

    staged = target.stage_delete('deep-research')
    assert not target.skill_dir('deep-research').exists()
    target.restore_delete(staged)
    assert target.skill_dir('deep-research').exists()

    staged = target.stage_delete('deep-research')
    target.purge_delete(staged)
    assert not staged.trash_path.exists()


def test_load_draft_rejects_tampered_content(tmp_path: Path) -> None:
    target = storage(tmp_path)
    prepared = target.prepare(VALID_SKILL, filename='SKILL.md', owner='admin')
    (target.drafts_dir / prepared.draft_id / 'content' / 'SKILL.md').write_text('tampered')

    with pytest.raises(SkillStorageError, match='已变化'):
        target.load_draft(prepared.draft_id, owner='admin')


def test_expired_draft_is_rejected_and_removed(tmp_path: Path) -> None:
    target = storage(tmp_path, draft_ttl_seconds=1)
    prepared = target.prepare(VALID_SKILL, filename='SKILL.md', owner='admin')
    metadata_path = target.drafts_dir / prepared.draft_id / 'draft.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    metadata['created_at'] = '2000-01-01T00:00:00+00:00'
    metadata_path.write_text(json.dumps(metadata), encoding='utf-8')

    with pytest.raises(SkillStorageError, match='已过期'):
        target.load_draft(prepared.draft_id, owner='admin')

    assert not (target.drafts_dir / prepared.draft_id).exists()


def test_cleanup_stale_removes_old_trash_with_limit(tmp_path: Path) -> None:
    target = storage(tmp_path, draft_ttl_seconds=1)
    prepared = target.prepare(VALID_SKILL, filename='SKILL.md', owner='admin')
    target.confirm(prepared.draft_id, owner='admin')
    staged = target.stage_delete('deep-research')
    os.utime(staged.trash_path, (1, 1))

    removed = target.cleanup_stale(limit=1)

    assert removed == 1
    assert not staged.trash_path.exists()
