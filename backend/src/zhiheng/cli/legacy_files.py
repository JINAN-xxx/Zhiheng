"""Read-only input for one-time migration. Never used by API or Worker."""

import asyncio
import os
import re
import stat
from pathlib import Path

from zhiheng.infrastructure.file_storage import MAX_FILE_BYTES
from zhiheng.shared.errors import AppError


class LegacyFileSource:
    def __init__(self, root: Path) -> None:
        self._root = root.absolute()

    def _directory(self) -> int:
        for path in (*reversed(self._root.parents), self._root):
            if path.is_symlink():
                raise OSError('symlink directory')
        return os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    async def check(self) -> None:
        try:
            await asyncio.to_thread(self._check)
        except OSError as error:
            raise AppError('file_storage_unavailable', '旧文件迁移目录不可读', 503) from error

    def _check(self) -> None:
        os.close(self._directory())

    async def read(self, key: str) -> bytes:
        if not re.fullmatch(r'[a-f0-9]{32}', key):
            raise AppError('invalid_storage_key', '文件存储标识无效', 422)
        try:
            return await asyncio.to_thread(self._read, key)
        except OSError as error:
            raise AppError('file_bytes_unavailable', '旧文件内容不可读，迁移已停止', 503) from error

    def _read(self, key: str) -> bytes:
        directory = self._directory()
        try:
            descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_FILE_BYTES:
                    raise OSError('invalid legacy object')
                with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                    content = stream.read(MAX_FILE_BYTES + 1)
                after = os.fstat(descriptor)
                if len(content) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise OSError('legacy object changed during migration')
                return content
            finally:
                os.close(descriptor)
        finally:
            os.close(directory)
