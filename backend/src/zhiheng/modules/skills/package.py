"""In-memory skill package validation; rules adapted from the MIT legacy parser."""

import hashlib
import io
import json
import lzma
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass
from typing import cast

import yaml  # type: ignore[import-untyped]  # PyYAML is bounded and narrowed to object below.

from zhiheng.modules.skills.validation import is_valid_slug
from zhiheng.shared.errors import AppError

UPLOAD_BYTES = 10 * 1024 * 1024
EXTRACTED_BYTES = 30 * 1024 * 1024
MAIN_BYTES = 128 * 1024
MAX_FILES = 200
MAX_YAML_DEPTH = 64
MAX_YAML_EVENTS = 20000
_FRONT = re.compile(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', re.DOTALL)
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


def invalid() -> AppError:
    return AppError('invalid_skill_package', '技能包格式不正确或超过安全限制', 422)


@dataclass(frozen=True)
class ParsedSkillPackage:
    slug: str
    name: str
    description: str
    package_version: str
    content: str
    content_hash: str
    files: tuple[tuple[str, bytes], ...]

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(len(data) for _, data in self.files)

    @property
    def parsed_hash(self) -> str:
        value = [
            self.slug,
            self.name,
            self.description,
            self.package_version,
            self.content_hash,
            self.file_count,
            self.total_bytes,
        ]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class SkillPackageParser:
    def parse(self, payload: bytes, *, filename: str) -> ParsedSkillPackage:
        if not payload or len(payload) > UPLOAD_BYTES:
            raise invalid()
        try:
            files: tuple[tuple[str, bytes], ...]
            if filename == 'SKILL.md':
                files = (('SKILL.md', payload),)
            elif filename.lower().endswith('.zip'):
                files = self._zip(payload)
            else:
                raise invalid()
            main = dict(files).get('SKILL.md')
            if main is None or len(main) > MAIN_BYTES:
                raise invalid()
            content = main.decode('utf-8')
            if '\x00' in content:
                raise invalid()
            metadata = self._metadata(content)
            digest = hashlib.sha256()
            for path, data in files:
                encoded = path.encode()
                digest.update(len(encoded).to_bytes(4, 'big'))
                digest.update(encoded)
                digest.update(len(data).to_bytes(8, 'big'))
                digest.update(data)
            return ParsedSkillPackage(*metadata, content, digest.hexdigest(), files)
        except (
            zlib.error,
            lzma.LZMAError,
            OSError,
            UnicodeError,
            yaml.YAMLError,
            RecursionError,
            zipfile.BadZipFile,
            RuntimeError,
            NotImplementedError,
            ValueError,
            EOFError,
        ) as error:
            raise invalid() from error

    @staticmethod
    def _metadata(content: str) -> tuple[str, str, str, str]:
        match = _FRONT.match(content)
        if match is None:
            raise invalid()
        depth = 0
        for count, event in enumerate(yaml.parse(match.group(1))):
            if isinstance(event, yaml.events.AliasEvent) or count >= MAX_YAML_EVENTS:
                raise invalid()
            if isinstance(event, (yaml.events.SequenceStartEvent, yaml.events.MappingStartEvent)):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise invalid()
            elif isinstance(event, (yaml.events.SequenceEndEvent, yaml.events.MappingEndEvent)):
                depth -= 1
        loaded: object = yaml.safe_load(match.group(1))
        if not isinstance(loaded, dict):
            raise invalid()
        data = cast('dict[object, object]', loaded)
        slug = data.get('name')
        if not isinstance(slug, str) or not is_valid_slug(slug):
            raise invalid()
        fields: list[str] = []
        for key, limit, default in [('display_name', 100, None), ('description', 500, None), ('version', 64, '1.0.0')]:
            value = data.get(key, default)
            if not isinstance(value, str) or not value.strip() or len(value) > limit or _CONTROL.search(value):
                raise invalid()
            fields.append(value.strip())
        return slug, fields[0], fields[1], fields[2]

    @staticmethod
    def _zip(payload: bytes) -> tuple[tuple[str, bytes], ...]:
        files: dict[str, bytes] = {}
        seen: set[str] = set()
        regular: set[str] = set()
        directories: set[str] = set()
        casing: dict[str, str] = {}
        total = 0
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                name = info.orig_filename
                path = name[:-1] if info.is_dir() else name
                parts = path.split('/')
                mode = stat.S_IFMT(info.external_attr >> 16)
                if (
                    not path
                    or '\\' in name
                    or _CONTROL.search(name)
                    or any(part in {'', '.', '..'} or ':' in part for part in parts)
                    or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or info.flag_bits & 1
                    or (mode == stat.S_IFDIR and not info.is_dir())
                    or (mode == stat.S_IFREG and info.is_dir())
                ):
                    raise invalid()
                for index in range(1, len(parts) + 1):
                    prefix = '/'.join(parts[:index])
                    folded = prefix.casefold()
                    if folded in casing and casing[folded] != prefix:
                        raise invalid()
                    casing[folded] = prefix
                canonical = path.casefold()
                parents = {'/'.join(parts[:index]).casefold() for index in range(1, len(parts))}
                if canonical in seen or parents & regular or (not info.is_dir() and canonical in directories):
                    raise invalid()
                seen.add(canonical)
                directories.update(parents)
                if info.is_dir():
                    if info.file_size != 0:
                        raise invalid()
                    directories.add(canonical)
                    continue
                regular.add(canonical)
                if (
                    len(files) >= MAX_FILES
                    or info.file_size > EXTRACTED_BYTES - total
                    or (path == 'SKILL.md' and info.file_size > MAIN_BYTES)
                ):
                    raise invalid()
                data = bytearray()
                with archive.open(info) as member:
                    while chunk := member.read(65536):
                        data.extend(chunk)
                        if len(data) + total > EXTRACTED_BYTES or (path == 'SKILL.md' and len(data) > MAIN_BYTES):
                            raise invalid()
                if len(data) != info.file_size:
                    raise invalid()
                total += len(data)
                files[path] = bytes(data)
        return tuple(sorted(files.items()))
