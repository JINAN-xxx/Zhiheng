"""Private S3 objects behind the same opaque keys and limits as local storage."""

import asyncio
import hashlib
import io
import re
from datetime import datetime
from urllib.parse import urlsplit
from uuid import uuid4

from minio import Minio
from minio.error import MinioException, S3Error
from urllib3 import PoolManager, Retry, Timeout
from urllib3.exceptions import HTTPError

from zhiheng.shared.errors import AppError
from zhiheng.shared.storage import StorageEntry, StorageInspection, StorageStat

MAX_FILE_BYTES = 20 * 1024 * 1024
INSPECTION_CHUNK_BYTES = 64 * 1024
MAX_PAGE_SIZE = 1000

STORAGE_ERRORS = (MinioException, HTTPError, OSError, ValueError)
MISSING_OBJECT_CODES = frozenset({'NoSuchKey', 'NoSuchObject'})


class S3FileStorage:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        *,
        region: str = 'us-east-1',
        timeout: float = 30,
    ) -> None:
        address = urlsplit(endpoint)
        self._bucket = bucket
        self._client = Minio(
            address.netloc,
            access_key=access_key,
            secret_key=secret_key,
            secure=address.scheme == 'https',
            region=region,
            http_client=PoolManager(
                timeout=Timeout(connect=min(5, timeout), read=timeout),
                retries=Retry(total=2, backoff_factor=0.2, allowed_methods=frozenset({'GET', 'HEAD'})),
                maxsize=10,
            ),
        )

    @staticmethod
    def _validate(key: str) -> None:
        if not re.fullmatch(r'[a-f0-9]{32}', key):
            raise AppError('invalid_storage_key', '文件存储标识无效', 422)

    @staticmethod
    def _validate_content(content: bytes) -> None:
        if not content or len(content) > MAX_FILE_BYTES:
            raise AppError('invalid_file_size', '文件为空或超过20MiB', 422)

    @staticmethod
    def _content_error(error: Exception) -> AppError:
        if isinstance(error, ValueError) or (isinstance(error, S3Error) and error.code in MISSING_OBJECT_CODES):
            return AppError('file_bytes_unavailable', '文件内容不可用', 503)
        return AppError('file_storage_unavailable', '文件存储暂时不可用', 503)

    async def check(self) -> None:
        try:
            exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
        except STORAGE_ERRORS as error:
            raise AppError('file_storage_unavailable', '文件存储不可用', 503) from error
        if not exists:
            raise AppError('file_storage_unavailable', '文件存储桶尚未初始化', 503)

    async def put(self, content: bytes) -> str:
        self._validate_content(content)
        key = uuid4().hex
        try:
            await asyncio.to_thread(self._write, key, content)
        except STORAGE_ERRORS as error:
            raise AppError('file_storage_unavailable', '文件存储不可用', 503) from error
        return key

    def _write(self, key: str, content: bytes) -> None:
        self._client.put_object(
            self._bucket,
            key,
            io.BytesIO(content),
            len(content),
            content_type='application/octet-stream',
            metadata={'sha256': hashlib.sha256(content).hexdigest()},
            num_parallel_uploads=1,
        )

    async def read(self, key: str) -> bytes:
        self._validate(key)
        try:
            return await asyncio.to_thread(self._read, key)
        except STORAGE_ERRORS as error:
            raise self._content_error(error) from error

    def _read(self, key: str) -> bytes:
        response = self._client.get_object(self._bucket, key)
        try:
            content = response.read(MAX_FILE_BYTES + 1)
            if not content or len(content) > MAX_FILE_BYTES:
                raise ValueError('invalid stored size')
            if len(content) != int(response.headers.get('content-length', '-1')):
                raise ValueError('incomplete stored object')
            return content
        finally:
            response.close()
            response.release_conn()

    async def stat(self, key: str) -> StorageStat:
        self._validate(key)
        try:
            item = await asyncio.to_thread(self._client.stat_object, self._bucket, key)
            if item.size is None or not 0 < item.size <= MAX_FILE_BYTES or item.last_modified is None:
                raise ValueError('invalid stored object')
            return StorageStat(item.size, item.last_modified)
        except STORAGE_ERRORS as error:
            raise self._content_error(error) from error

    async def inspect(self, key: str) -> StorageInspection:
        self._validate(key)
        try:
            return await asyncio.to_thread(self._inspect, key)
        except STORAGE_ERRORS as error:
            raise self._content_error(error) from error

    def _inspect(self, key: str) -> StorageInspection:
        response = self._client.get_object(self._bucket, key)
        try:
            digest, size = hashlib.sha256(), 0
            while block := response.read(INSPECTION_CHUNK_BYTES):
                size += len(block)
                if size > MAX_FILE_BYTES:
                    raise ValueError('invalid stored size')
                digest.update(block)
            if not size or size != int(response.headers.get('content-length', '-1')):
                raise ValueError('invalid or incomplete stored object')
            return StorageInspection(size, digest.hexdigest())
        finally:
            response.close()
            response.release_conn()

    async def delete(self, key: str) -> None:
        self._validate(key)
        try:
            await asyncio.to_thread(self._client.remove_object, self._bucket, key)
        except STORAGE_ERRORS as error:
            raise AppError('file_storage_unavailable', '文件存储不可用', 503) from error

    async def entries(self, *, after: str = '', limit: int = 100) -> tuple[StorageEntry, ...]:
        if after:
            self._validate(after)
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError('Storage page limit must be between 1 and 1000')
        try:
            return await asyncio.to_thread(self._entries, after, limit)
        except STORAGE_ERRORS as error:
            raise AppError('file_storage_unavailable', '文件存储不可用', 503) from error

    def _entries(self, after: str, limit: int) -> tuple[StorageEntry, ...]:
        result = []
        for item in self._client.list_objects(self._bucket, recursive=True, start_after=after):
            if (
                isinstance(item.object_name, str)
                and re.fullmatch(r'[a-f0-9]{32}', item.object_name)
                and isinstance(item.last_modified, datetime)
            ):
                result.append(StorageEntry(item.object_name, item.last_modified))
                if len(result) == limit:
                    break
        return tuple(result)

    async def import_object(self, key: str, content: bytes) -> bool:
        """Offline migration only: preserve keys and refuse to replace different existing bytes."""
        self._validate(key)
        self._validate_content(content)
        try:
            return await asyncio.to_thread(self._import_object, key, content)
        except STORAGE_ERRORS as error:
            raise AppError('file_storage_unavailable', '文件存储不可用', 503) from error

    def _import_object(self, key: str, content: bytes) -> bool:
        try:
            existing = self._read(key)
        except S3Error as error:
            if error.code not in MISSING_OBJECT_CODES:
                raise
        else:
            if existing != content:
                raise AppError('file_migration_conflict', '目标存储已有不同内容，迁移已停止', 409)
            return False
        self._write(key, content)
        return True
