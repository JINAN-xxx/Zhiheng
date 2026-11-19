import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from zhiheng.modules.files.adapters.native import NativeParser


async def test_real_native_parser_does_not_receive_platform_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ZHIHENG_CREDENTIAL_KEY', 'private-credential-sentinel')
    monkeypatch.setenv('ZHIHENG_DATABASE_URL', 'private-database-sentinel')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'private-cloud-sentinel')
    original = asyncio.create_subprocess_exec
    with patch(
        'zhiheng.modules.files.adapters.native.asyncio.create_subprocess_exec', AsyncMock(wraps=original)
    ) as launch:
        result = await NativeParser().parse(b'plain text', filename='example.txt')
    assert result.text == 'plain text'
    environment = launch.call_args.kwargs['env']
    assert not any('sentinel' in value for value in environment.values())
    assert 'ZHIHENG_CREDENTIAL_KEY' not in environment and 'AWS_SECRET_ACCESS_KEY' not in environment
