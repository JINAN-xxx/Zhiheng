"""Isolated real MinIO buckets and storage fault/age injection for integration tests."""

import asyncio
import io
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

from minio import Minio

from tests.core.resources import resource

if TYPE_CHECKING:
    import pytest

    from zhiheng.bootstrap.container import Container
    from zhiheng.infrastructure.file_storage import S3FileStorage
    from zhiheng.shared.storage import StorageEntry, StorageStat


@contextmanager
def isolated_bucket() -> Iterator[dict[str, str]]:
    endpoint = os.environ['ZHIHENG_TEST_S3_ENDPOINT']
    access = os.environ['ZHIHENG_TEST_S3_ACCESS_KEY']
    secret = os.environ['ZHIHENG_TEST_S3_SECRET_KEY']
    address = urlsplit(endpoint)
    client = Minio(address.netloc, access_key=access, secret_key=secret, secure=address.scheme == 'https')
    bucket = f'zhiheng-test-{uuid4().hex}'
    resource('intent', 'minio_bucket', bucket, endpoint=endpoint)
    client.make_bucket(bucket)
    resource('created', 'minio_bucket', bucket, endpoint=endpoint)
    try:
        yield {
            'files_s3_endpoint': endpoint,
            'files_s3_access_key': access,
            'files_s3_secret_key': secret,
            'files_s3_bucket': bucket,
        }
    finally:
        for item in client.list_objects(bucket, recursive=True):
            assert item.object_name is not None
            client.remove_object(bucket, item.object_name)
        client.remove_bucket(bucket)
        resource('deleted', 'minio_bucket', bucket, endpoint=endpoint)


async def overwrite_object(container: 'Container', key: str, content: bytes) -> None:
    settings = container.settings
    address = urlsplit(settings.files_s3_endpoint)
    client = Minio(
        address.netloc,
        access_key=settings.files_s3_access_key.get_secret_value(),
        secret_key=settings.files_s3_secret_key.get_secret_value(),
        secure=address.scheme == 'https',
    )
    await asyncio.to_thread(client.put_object, settings.files_s3_bucket, key, io.BytesIO(content), len(content))


def age_objects(store: 'S3FileStorage', keys: set[str], monkeypatch: 'pytest.MonkeyPatch') -> None:
    entries, stat = store.entries, store.stat
    aged = datetime(2000, 1, 1, tzinfo=timezone.utc)

    async def old_entries(*, after: str = '', limit: int = 100) -> tuple['StorageEntry', ...]:
        return tuple(
            replace(item, modified_at=aged) if item.key in keys else item
            for item in await entries(after=after, limit=limit)
        )

    async def old_stat(key: str) -> 'StorageStat':
        result = await stat(key)
        return replace(result, modified_at=aged) if key in keys else result

    monkeypatch.setattr(store, 'entries', old_entries)
    monkeypatch.setattr(store, 'stat', old_stat)
