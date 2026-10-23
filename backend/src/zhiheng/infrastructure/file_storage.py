"""Flat, opaque local object store. Business code never receives a filesystem path."""

import asyncio
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from zhiheng.shared.errors import AppError
from zhiheng.shared.storage import StorageEntry

MAX_FILE_BYTES = 20 * 1024 * 1024


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.absolute()

    def _directory(self) -> int:
        try:
            # Never follow symlinks in configured ancestors, including a replaced root.
            for path in (*reversed(self._root.parents), self._root):
                if path.is_symlink():
                    raise OSError('symlink directory')
            self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
            return os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as error:
            raise AppError('file_storage_unavailable', '文件存储不可用', 503) from error

    @staticmethod
    def _validate(key: str) -> None:
        if not re.fullmatch(r'[a-f0-9]{32}', key):
            raise AppError('invalid_storage_key', '文件存储标识无效', 422)

    async def put(self, content: bytes) -> str:
        if not content or len(content) > MAX_FILE_BYTES:
            raise AppError('invalid_file_size', '文件为空或超过20MiB', 422)
        return await asyncio.to_thread(self._put, content)

    def _put(self, content: bytes) -> str:
        directory = self._directory()
        temporary, key = uuid4().hex, uuid4().hex
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, key, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
            return key
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)

    async def read(self, key: str) -> bytes:
        self._validate(key)
        return await asyncio.to_thread(self._read, key)

    def _read(self, key: str) -> bytes:
        directory = self._directory()
        try:
            descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
                    raise OSError('invalid stored object')
                content = stream.read(MAX_FILE_BYTES + 1)
            if not content or len(content) > MAX_FILE_BYTES:
                raise OSError('invalid stored size')
            return content
        except OSError as error:
            raise AppError('file_bytes_unavailable', '文件内容不可用', 503) from error
        finally:
            os.close(directory)

    async def delete(self, key: str) -> None:
        self._validate(key)
        await asyncio.to_thread(self._delete, key)

    def _delete(self, key: str) -> None:
        directory = self._directory()
        try:
            try:
                os.unlink(key, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
        finally:
            os.close(directory)

    async def entries(self, *, after: str = '', limit: int = 100) -> tuple[StorageEntry, ...]:
        return await asyncio.to_thread(self._entries, after, limit)

    def _entries(self, after: str, limit: int) -> tuple[StorageEntry, ...]:
        directory = self._directory()
        try:
            keys = sorted(key for key in os.listdir(directory) if re.fullmatch(r'[a-f0-9]{32}', key) and key > after)
            return tuple(entry for key in keys[:limit] if (entry := self._entry(directory, key)) is not None)
        finally:
            os.close(directory)

    @staticmethod
    def _entry(directory: int, key: str) -> StorageEntry | None:
        try:
            info = os.stat(key, dir_fd=directory, follow_symlinks=False)
            return StorageEntry(key, datetime.fromtimestamp(info.st_mtime, timezone.utc))
        except FileNotFoundError:
            return None
