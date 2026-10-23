from __future__ import annotations

import hashlib
import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from plugins.agent_extension.domain.skill_errors import SkillPackageError

_FRONT_MATTER_PATTERN = re.compile(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', re.DOTALL)
_SLUG_PATTERN = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_CONTROL_PATTERN = re.compile(r'[\x00-\x1f\x7f]')
_HASH_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class SkillPackageLimits:
    upload_bytes: int
    extracted_bytes: int
    file_count: int
    skill_md_bytes: int


@dataclass(frozen=True, slots=True)
class ParsedSkillPackage:
    slug: str
    name: str
    description: str
    version: str
    content_hash: str
    file_count: int
    total_bytes: int
    root: Path
    metadata: dict[str, object]


def compute_tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (path for path in root.rglob('*') if path.is_file()), key=lambda path: path.relative_to(root).as_posix()
    )
    for path in files:
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, 'big'))
        digest.update(relative)
        with path.open('rb') as handle:
            while chunk := handle.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
    return digest.hexdigest()


class SkillPackageParser:
    def __init__(self, limits: SkillPackageLimits) -> None:
        self._limits = limits

    def parse_bytes(self, payload: bytes, *, filename: str, target_dir: Path) -> ParsedSkillPackage:
        if not payload or len(payload) > self._limits.upload_bytes:
            raise SkillPackageError('技能包超过上传大小限制')
        if target_dir.exists():
            raise SkillPackageError('技能解析目标已存在')
        target_dir.mkdir(parents=True, mode=0o700)
        try:
            if filename == 'SKILL.md':
                self._write_markdown(payload, target_dir)
            elif filename.lower().endswith('.zip'):
                self._extract_zip(payload, target_dir)
            else:
                raise SkillPackageError('仅支持 SKILL.md 或 ZIP 技能包')
            return self._describe(target_dir)
        except BaseException:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

    def _write_markdown(self, payload: bytes, target_dir: Path) -> None:
        if len(payload) > self._limits.skill_md_bytes:
            raise SkillPackageError('技能主文件超过大小限制')
        (target_dir / 'SKILL.md').write_bytes(payload)

    def _extract_zip(self, payload: bytes, target_dir: Path) -> None:
        upload_path = target_dir.parent / f'.{target_dir.name}.zip'
        upload_path.write_bytes(payload)
        try:
            with zipfile.ZipFile(upload_path) as archive_handle:
                self._extract_members(archive_handle, target_dir)
        except zipfile.BadZipFile as exc:
            raise SkillPackageError('ZIP 技能包格式不正确') from exc
        finally:
            upload_path.unlink(missing_ok=True)

    def _extract_members(self, archive: zipfile.ZipFile, target_dir: Path) -> None:
        seen: set[str] = set()
        total_bytes = 0
        file_count = 0
        for info in archive.infolist():
            relative = self._safe_member_path(info)
            canonical = relative.as_posix().casefold()
            if canonical in seen:
                raise SkillPackageError('ZIP 技能包包含重复路径')
            seen.add(canonical)
            if info.is_dir():
                continue
            file_count += 1
            if file_count > self._limits.file_count:
                raise SkillPackageError('技能包文件数量超过限制')
            total_bytes += info.file_size
            if total_bytes > self._limits.extracted_bytes:
                raise SkillPackageError('技能包解压大小超过限制')
            if relative.as_posix() == 'SKILL.md' and info.file_size > self._limits.skill_md_bytes:
                raise SkillPackageError('技能主文件超过大小限制')
            destination = target_dir.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._copy_member(archive, info, destination, total_before=total_bytes - info.file_size)
        if not (target_dir / 'SKILL.md').is_file():
            raise SkillPackageError('技能包根目录缺少 SKILL.md')

    def _copy_member(
        self, archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path, *, total_before: int
    ) -> None:
        written = 0
        with archive.open(info) as source, destination.open('xb') as output:
            while chunk := source.read(_HASH_CHUNK_SIZE):
                written += len(chunk)
                if total_before + written > self._limits.extracted_bytes:
                    raise SkillPackageError('技能包解压大小超过限制')
                if destination.name == 'SKILL.md' and written > self._limits.skill_md_bytes:
                    raise SkillPackageError('技能主文件超过大小限制')
                output.write(chunk)

    @staticmethod
    def _safe_member_path(info: zipfile.ZipInfo) -> PurePosixPath:
        name = info.filename
        if not name or '\\' in name or name.startswith('/'):
            raise SkillPackageError('ZIP 技能包包含不安全路径')
        relative = PurePosixPath(name)
        if relative.is_absolute() or any(part in {'', '.', '..'} or ':' in part for part in relative.parts):
            raise SkillPackageError('ZIP 技能包包含不安全路径')
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK:
            raise SkillPackageError('ZIP 技能包不能包含链接')
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise SkillPackageError('ZIP 技能包只能包含普通文件和目录')
        if info.flag_bits & 0x1:
            raise SkillPackageError('ZIP 技能包不能包含加密文件')
        return relative

    def _describe(self, target_dir: Path) -> ParsedSkillPackage:
        skill_path = target_dir / 'SKILL.md'
        raw = skill_path.read_bytes()
        if len(raw) > self._limits.skill_md_bytes:
            raise SkillPackageError('技能主文件超过大小限制')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise SkillPackageError('技能主文件必须使用 UTF-8 编码') from exc
        metadata = self._parse_metadata(text)
        files = [path for path in target_dir.rglob('*') if path.is_file()]
        total_bytes = sum(path.stat().st_size for path in files)
        return ParsedSkillPackage(
            slug=str(metadata['name']),
            name=str(metadata['display_name']),
            description=str(metadata['description']),
            version=str(metadata['version']),
            content_hash=compute_tree_hash(target_dir),
            file_count=len(files),
            total_bytes=total_bytes,
            root=target_dir,
            metadata=metadata,
        )

    @staticmethod
    def _parse_metadata(text: str) -> dict[str, object]:
        match = _FRONT_MATTER_PATTERN.match(text)
        if match is None:
            raise SkillPackageError('技能主文件缺少有效元数据')
        try:
            metadata: Any = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            raise SkillPackageError('技能主文件元数据格式不正确') from exc
        if not isinstance(metadata, dict):
            raise SkillPackageError('技能主文件元数据格式不正确')
        values = {
            'name': metadata.get('name'),
            'display_name': metadata.get('display_name'),
            'description': metadata.get('description'),
            'version': metadata.get('version', '1.0.0'),
        }
        if not isinstance(values['name'], str) or not _SLUG_PATTERN.fullmatch(values['name']):
            raise SkillPackageError('技能主文件元数据中的标识格式不正确')
        for key, limit in (('display_name', 100), ('description', 500), ('version', 64)):
            value = values[key]
            if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL_PATTERN.search(value):
                raise SkillPackageError('技能主文件元数据字段不正确')
            values[key] = value.strip()
        return values
