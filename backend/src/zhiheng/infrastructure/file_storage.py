"""Flat, opaque local object store. Business code never receives a filesystem path."""

import asyncio
import hashlib
import heapq
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from zhiheng.shared.errors import AppError
from zhiheng.shared.storage import StorageEntry, StorageInspection, StorageStat

MAX_FILE_BYTES = 20 * 1024 * 1024
INSPECTION_CHUNK_BYTES = 64 * 1024


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
            stream, _ = self._open_object(directory, key)
            with stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            if not content or len(content) > MAX_FILE_BYTES:
                raise OSError('invalid stored size')
            return content
        except OSError as error:
            raise AppError('file_bytes_unavailable', '文件内容不可用', 503) from error
        finally:
            os.close(directory)

    @staticmethod
    def _check_object(info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
            raise OSError('invalid stored object')

    def _open_object(self, directory: int, key: str) -> tuple[BinaryIO, os.stat_result]:
        descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            self._check_object(info)
            return os.fdopen(descriptor, 'rb'), info
        except BaseException:
            # fdopen can fail without taking ownership, notably for directory descriptors.
            os.close(descriptor)
            raise

    async def stat(self, key: str) -> StorageStat:
        self._validate(key)
        return await asyncio.to_thread(self._stat, key)

    def _stat(self, key: str) -> StorageStat:
        directory = self._directory()
        try:
            descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                self._check_object(info)
                return StorageStat(info.st_size, datetime.fromtimestamp(info.st_mtime, timezone.utc))
            finally:
                os.close(descriptor)
        except OSError as error:
            raise AppError('file_bytes_unavailable', '文件内容不可用', 503) from error
        finally:
            os.close(directory)

    async def inspect(self, key: str) -> StorageInspection:
        self._validate(key)
        return await asyncio.to_thread(self._inspect, key)

    def _inspect(self, key: str) -> StorageInspection:
        directory = self._directory()
        try:
            stream, before = self._open_object(directory, key)
            with stream:
                digest, size = hashlib.sha256(), 0
                while block := stream.read(INSPECTION_CHUNK_BYTES):
                    size += len(block)
                    if size > MAX_FILE_BYTES:
                        raise OSError('invalid stored size')
                    digest.update(block)
                after = os.fstat(stream.fileno())
                if size != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise OSError('stored object changed during inspection')
            return StorageInspection(size, digest.hexdigest())
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
            # Flat random keys still require a directory pass; only this page's keys stay in memory.
            with os.scandir(directory) as entries:
                keys = heapq.nsmallest(
                    limit,
                    (
                        entry.name
                        for entry in entries
                        if re.fullmatch(r'[a-f0-9]{32}', entry.name) and entry.name > after
                    ),
                )
            return tuple(entry for key in keys if (entry := self._entry(directory, key)) is not None)
        finally:
            os.close(directory)

    @staticmethod
    def _entry(directory: int, key: str) -> StorageEntry | None:
        try:
            info = os.stat(key, dir_fd=directory, follow_symlinks=False)
            return StorageEntry(key, datetime.fromtimestamp(info.st_mtime, timezone.utc))
        except FileNotFoundError:
            return None
