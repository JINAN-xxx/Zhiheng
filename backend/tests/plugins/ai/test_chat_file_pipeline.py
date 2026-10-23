"""Exercise protected upload, real metadata persistence and authorized chat reads."""

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import UploadFile
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.env import UploadConfig
from module_admin.entity.do.file_do import SysFileInfo
from module_admin.service.common_service import CommonService
from plugins.ai.infrastructure.chat_file_adapter import ChatFileAdapter
from plugins.ai.service.platform_chat_service import PlatformChatError


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['image', 'document'])
async def test_protected_upload_is_readable_only_by_its_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(SysFileInfo.__table__.create)
    monkeypatch.setattr(UploadConfig, 'PRIVATE_UPLOAD_PATH', str(tmp_path / 'private'))
    monkeypatch.setattr(UploadConfig, 'UPLOAD_PATH', str(tmp_path / 'public'))
    monkeypatch.setattr(CommonService, '_enqueue_file_access_log', AsyncMock())
    request = SimpleNamespace(base_url='http://platform.test/docker-api/', headers={}, client=None)
    user = SimpleNamespace(user=SimpleNamespace(user_id=42, user_name='file-test', dept_id=1))
    if kind == 'image':
        output = io.BytesIO()
        Image.new('RGB', (4, 4), 'blue').save(output, format='WEBP')
        content, filename = output.getvalue(), '测试图片.webp'
    else:
        content, filename = '季度,营收\n一季度,12345\n'.encode('utf-8-sig'), '季度报表.csv'
    try:
        async with sessions() as db:
            uploaded = await CommonService.upload_service(
                request, db, user, UploadFile(file=io.BytesIO(content), filename=filename), 'private'
            )
        assert uploaded.result.access_type == 'private'
        reference = UUID(uploaded.result.file_id)
        adapter = ChatFileAdapter(sessions)
        resolve = adapter.resolve if kind == 'image' else adapter.resolve_attachment
        result = await resolve(42, reference, max_size_bytes=20 * 1024 * 1024)
        assert result.file_id == reference
        assert result.file_name == filename
        assert result.size_bytes == len(content)
        if kind == 'image':
            assert result.mime_type == 'image/webp'
            assert result.data_url.startswith('data:image/webp;base64,')
        else:
            assert '季度' in result.text and '12345' in result.text
        with pytest.raises(PlatformChatError):
            await resolve(43, reference, max_size_bytes=20 * 1024 * 1024)
    finally:
        await engine.dispose()
