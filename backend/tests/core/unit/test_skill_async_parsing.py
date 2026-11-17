import asyncio
import threading

import pytest

from tests.core.unit.test_skill_package import archive, markdown
from zhiheng.modules.skills.package import UPLOAD_BYTES, ParsedSkillPackage, SkillPackageParser
from zhiheng.shared.errors import AppError


async def test_parser_uses_worker_thread_while_event_loop_can_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = threading.Event(), threading.Event()
    parser = SkillPackageParser()
    original = parser.parse
    calls = []
    main = threading.get_ident()

    def blocked(payload: bytes, *, filename: str) -> ParsedSkillPackage:
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return original(payload, filename=filename)

    monkeypatch.setattr(parser, 'parse', blocked)
    task = asyncio.create_task(parser.parse_async(markdown(), filename='SKILL.md'))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        assert not task.done() and calls[0] != main
    finally:
        release.set()
    assert (await task).slug == 'sample'


async def test_async_archive_preserves_exact_content_hash_and_safety_errors() -> None:
    parser = SkillPackageParser()
    raw = archive([('SKILL.md', markdown()), ('script.py', b'print(1)')])
    assert await parser.parse_async(raw, filename='skill.zip') == parser.parse(raw, filename='skill.zip')
    with pytest.raises(AppError) as invalid:
        await parser.parse_async(archive([('../SKILL.md', markdown())]), filename='skill.zip')
    assert invalid.value.code == 'invalid_skill_package'


@pytest.mark.parametrize('payload', [b'', b'x' * (UPLOAD_BYTES + 1)])
async def test_invalid_upload_size_is_rejected_before_thread_work(
    payload: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = SkillPackageParser()

    def unexpected(payload: bytes, *, filename: str) -> ParsedSkillPackage:
        raise AssertionError('Invalid upload should not be scheduled')

    monkeypatch.setattr(parser, 'parse', unexpected)
    with pytest.raises(AppError) as invalid:
        await parser.parse_async(payload, filename='SKILL.md')
    assert invalid.value.code == 'invalid_skill_package'
