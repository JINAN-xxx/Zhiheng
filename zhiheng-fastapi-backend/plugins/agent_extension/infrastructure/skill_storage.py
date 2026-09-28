from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from plugins.agent_extension.domain.skill_errors import SkillStorageError
from plugins.agent_extension.domain.skill_package import ParsedSkillPackage, SkillPackageParser, compute_tree_hash

_DRAFT_PATTERN = re.compile(r'^draft_[0-9a-f]{32}$')
_SLUG_PATTERN = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


@dataclass(frozen=True, slots=True)
class PreparedSkillDraft:
    draft_id: str
    package: ParsedSkillPackage


@dataclass(frozen=True, slots=True)
class StagedSkillDelete:
    slug: str
    original_path: Path
    trash_path: Path


class SkillStorage:
    def __init__(self, root: Path, *, parser: SkillPackageParser, draft_ttl_seconds: int = 3600) -> None:
        self.root = root.resolve()
        self.parser = parser
        self.draft_ttl_seconds = draft_ttl_seconds
        self.drafts_dir = self.root / '.drafts'
        self.trash_dir = self.root / '.trash'
        self.skills_dir = self.root / 'skills'
        self.ensure_layout()

    def ensure_layout(self) -> None:
        for path in (self.root, self.drafts_dir, self.trash_dir, self.skills_dir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def prepare(self, payload: bytes, *, filename: str, owner: str) -> PreparedSkillDraft:
        if not owner:
            raise SkillStorageError('技能草稿缺少上传人')
        draft_id = f'draft_{uuid4().hex}'
        draft_dir = self.drafts_dir / draft_id
        content_dir = draft_dir / 'content'
        draft_dir.mkdir(mode=0o700)
        try:
            package = self.parser.parse_bytes(payload, filename=filename, target_dir=content_dir)
            metadata = {
                'draft_id': draft_id,
                'owner': owner,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'package': {
                    key: value
                    for key, value in asdict(package).items()
                    if key not in {'root', 'metadata'}
                },
                'metadata': package.metadata,
            }
            self._write_json(draft_dir / 'draft.json', metadata)
            return PreparedSkillDraft(draft_id=draft_id, package=package)
        except BaseException:
            shutil.rmtree(draft_dir, ignore_errors=True)
            raise

    def load_draft(self, draft_id: str, *, owner: str) -> PreparedSkillDraft:
        draft_dir = self._draft_dir(draft_id)
        try:
            payload = json.loads((draft_dir / 'draft.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError) as exc:
            raise SkillStorageError('技能安装草稿不存在') from exc
        if payload.get('owner') != owner:
            raise SkillStorageError('无权访问该技能安装草稿')
        if self._draft_expired(payload.get('created_at')):
            shutil.rmtree(draft_dir, ignore_errors=True)
            raise SkillStorageError('技能安装草稿已过期')
        package_data = payload.get('package')
        metadata = payload.get('metadata')
        if not isinstance(package_data, dict) or not isinstance(metadata, dict):
            raise SkillStorageError('技能安装草稿已损坏')
        content_dir = draft_dir / 'content'
        if compute_tree_hash(content_dir) != package_data.get('content_hash'):
            raise SkillStorageError('技能安装草稿内容已变化')
        package = ParsedSkillPackage(
            slug=str(package_data['slug']),
            name=str(package_data['name']),
            description=str(package_data['description']),
            version=str(package_data['version']),
            content_hash=str(package_data['content_hash']),
            file_count=int(package_data['file_count']),
            total_bytes=int(package_data['total_bytes']),
            root=content_dir,
            metadata=dict(metadata),
        )
        return PreparedSkillDraft(draft_id=draft_id, package=package)

    def confirm(self, draft_id: str, *, owner: str) -> str:
        prepared = self.load_draft(draft_id, owner=owner)
        destination = self.skill_dir(prepared.package.slug)
        if destination.exists():
            raise SkillStorageError('技能标识已存在')
        draft_dir = self._draft_dir(draft_id)
        os.rename(prepared.package.root, destination)
        shutil.rmtree(draft_dir, ignore_errors=True)
        return destination.relative_to(self.root).as_posix()

    def discard(self, draft_id: str, *, owner: str) -> None:
        self.load_draft(draft_id, owner=owner)
        shutil.rmtree(self._draft_dir(draft_id))

    def skill_dir(self, slug: str) -> Path:
        if not _SLUG_PATTERN.fullmatch(slug):
            raise SkillStorageError('技能标识格式不正确')
        return self.skills_dir / slug

    def stage_delete(self, slug: str) -> StagedSkillDelete:
        original = self.skill_dir(slug)
        if not original.is_dir():
            raise SkillStorageError('技能文件不存在')
        trash = self.trash_dir / f'trash_{uuid4().hex}'
        os.rename(original, trash)
        return StagedSkillDelete(slug=slug, original_path=original, trash_path=trash)

    @staticmethod
    def restore_delete(staged: StagedSkillDelete) -> None:
        if staged.original_path.exists():
            raise SkillStorageError('技能目录已存在，无法恢复')
        os.rename(staged.trash_path, staged.original_path)

    @staticmethod
    def purge_delete(staged: StagedSkillDelete) -> None:
        shutil.rmtree(staged.trash_path, ignore_errors=True)

    def read_skill_md(self, storage_path: str) -> str:
        relative = Path(storage_path)
        if relative.is_absolute() or '..' in relative.parts:
            raise SkillStorageError('技能存储路径不安全')
        root = (self.root / relative).resolve()
        if root.parent != self.skills_dir or not root.is_dir():
            raise SkillStorageError('技能文件不存在')
        try:
            return (root / 'SKILL.md').read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            raise SkillStorageError('技能主文件不可读取') from exc

    def cleanup_stale(self, *, limit: int = 100) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - self.draft_ttl_seconds
        removed = 0
        candidates = sorted((*self.drafts_dir.iterdir(), *self.trash_dir.iterdir()), key=lambda path: path.name)
        for path in candidates:
            if removed >= limit:
                break
            if not path.is_dir():
                continue
            if path.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        return removed

    def _draft_dir(self, draft_id: str) -> Path:
        if not _DRAFT_PATTERN.fullmatch(draft_id):
            raise SkillStorageError('技能安装草稿标识无效')
        return self.drafts_dir / draft_id

    def _draft_expired(self, created_at: object) -> bool:
        if not isinstance(created_at, str):
            raise SkillStorageError('技能安装草稿已损坏')
        try:
            created = datetime.fromisoformat(created_at)
        except ValueError as exc:
            raise SkillStorageError('技能安装草稿已损坏') from exc
        if created.tzinfo is None:
            raise SkillStorageError('技能安装草稿已损坏')
        age = datetime.now(timezone.utc) - created.astimezone(timezone.utc)
        return age.total_seconds() > self.draft_ttl_seconds

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        os.replace(temporary, path)
