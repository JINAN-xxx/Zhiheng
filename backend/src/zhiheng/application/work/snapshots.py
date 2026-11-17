"""Stable accepted task values and request identities, without SDK clients or secrets."""

import hashlib
import json
from dataclasses import asdict
from uuid import UUID

from zhiheng.modules.skills.public import SkillRevisionValue, SkillSelection
from zhiheng.shared.errors import AppError


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def frozen_skills(selection: SkillSelection) -> list[dict[str, object]]:
    return [
        {**asdict(item), 'skill_id': str(item.skill_id), 'revision_id': str(item.revision_id)}
        for item in selection.revisions
    ]


def skill_selection(payload: dict[str, object]) -> SkillSelection:
    values = payload.get('skills', [])
    if not isinstance(values, list):
        raise AppError('work_snapshot_invalid', '任务执行配置无效', 409)
    revisions = []
    for item in values:
        if not isinstance(item, dict):
            raise AppError('work_snapshot_invalid', '任务技能配置无效', 409)
        revisions.append(
            SkillRevisionValue(
                skill_id=UUID(str(item['skill_id'])),
                revision_id=UUID(str(item['revision_id'])),
                slug=str(item['slug']),
                name=str(item['name']),
                content_hash=str(item['content_hash']),
                content=str(item['content']),
            )
        )
    return SkillSelection(tuple(revisions))
