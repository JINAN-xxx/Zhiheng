import io
import stat
import zipfile
from pathlib import Path

import pytest

from plugins.agent_extension.domain.skill_errors import SkillPackageError
from plugins.agent_extension.domain.skill_package import SkillPackageLimits, SkillPackageParser

VALID_SKILL = b"""---
name: deep-research
display_name: \xe6\xb7\xb1\xe5\xba\xa6\xe7\xa0\x94\xe7\xa9\xb6
description: \xe6\x8c\x89\xe8\xae\xa1\xe5\x88\x92\xe5\xae\x8c\xe6\x88\x90\xe5\xa4\x8d\xe6\x9d\x82\xe4\xb8\xbb\xe9\xa2\x98\xe7\xa0\x94\xe7\xa9\xb6\xe3\x80\x82
version: 1.0.0
---

# \xe5\xb7\xa5\xe4\xbd\x9c\xe6\x96\xb9\xe5\xbc\x8f

\xe5\x85\x88\xe5\x88\x86\xe8\xa7\xa3\xe9\x97\xae\xe9\xa2\x98\xef\xbc\x8c\xe5\x86\x8d\xe6\x80\xbb\xe7\xbb\x93\xe7\xbb\x93\xe8\xae\xba\xe3\x80\x82
"""
SHA256_HEX_LENGTH = 64
PACKAGE_FILE_COUNT = 2


def build_zip(entries: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, 'SKILL.md')
    return output.getvalue()


def parser(**overrides: int) -> SkillPackageParser:
    values = {'upload_bytes': 4096, 'extracted_bytes': 4096, 'file_count': 10, 'skill_md_bytes': 2048}
    values.update(overrides)
    return SkillPackageParser(SkillPackageLimits(**values))


def test_parses_single_skill_markdown(tmp_path: Path) -> None:
    result = parser().parse_bytes(VALID_SKILL, filename='SKILL.md', target_dir=tmp_path / 'parsed')

    assert result.slug == 'deep-research'
    assert result.name == '深度研究'
    assert result.description.endswith('研究。')
    assert result.version == '1.0.0'
    assert result.file_count == 1
    assert len(result.content_hash) == SHA256_HEX_LENGTH
    assert (result.root / 'SKILL.md').read_bytes() == VALID_SKILL


def test_parses_zip_with_regular_support_files(tmp_path: Path) -> None:
    payload = build_zip({'SKILL.md': VALID_SKILL, 'references/guide.md': b'guide'})

    result = parser().parse_bytes(payload, filename='research.zip', target_dir=tmp_path / 'parsed')

    assert result.file_count == PACKAGE_FILE_COUNT
    assert (result.root / 'references/guide.md').read_text() == 'guide'


@pytest.mark.parametrize(
    'entries',
    (
        {'../escape.txt': b'unsafe', 'SKILL.md': VALID_SKILL},
        {'/absolute.txt': b'unsafe', 'SKILL.md': VALID_SKILL},
        {'folder\\escape.txt': b'unsafe', 'SKILL.md': VALID_SKILL},
        {'SKILL.md': VALID_SKILL, 'skill.md': VALID_SKILL},
    ),
)
def test_rejects_unsafe_or_duplicated_zip_paths(tmp_path: Path, entries: dict[str, bytes]) -> None:
    with pytest.raises(SkillPackageError, match=r'不安全|重复'):
        parser().parse_bytes(build_zip(entries), filename='unsafe.zip', target_dir=tmp_path / 'parsed')


def test_rejects_zip_symlink(tmp_path: Path) -> None:
    with pytest.raises(SkillPackageError, match='链接'):
        parser().parse_bytes(
            build_zip({'SKILL.md': VALID_SKILL}, symlink='references/current'),
            filename='unsafe.zip',
            target_dir=tmp_path / 'parsed',
        )


@pytest.mark.parametrize(
    ('payload', 'limits', 'message'),
    (
        (build_zip({'readme.md': b'missing'}), {}, 'SKILL.md'),
        (build_zip({'SKILL.md': VALID_SKILL, 'a': b'1', 'b': b'2'}), {'file_count': 2}, '文件数量'),
        (build_zip({'SKILL.md': VALID_SKILL}), {'extracted_bytes': 32}, '解压'),
        (VALID_SKILL, {'skill_md_bytes': 32}, '主文件'),
    ),
)
def test_rejects_missing_or_oversized_content(
    tmp_path: Path,
    payload: bytes,
    limits: dict[str, int],
    message: str,
) -> None:
    filename = 'SKILL.md' if payload == VALID_SKILL else 'skill.zip'
    with pytest.raises(SkillPackageError, match=message):
        parser(**limits).parse_bytes(payload, filename=filename, target_dir=tmp_path / 'parsed')


def test_rejects_invalid_front_matter_and_cleans_target(tmp_path: Path) -> None:
    target = tmp_path / 'parsed'

    with pytest.raises(SkillPackageError, match='元数据'):
        parser().parse_bytes(b'# no metadata', filename='SKILL.md', target_dir=target)

    assert not target.exists()
