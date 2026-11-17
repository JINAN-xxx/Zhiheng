import asyncio
import hashlib
import io
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import HTTPError

from zhiheng.cli.legacy_files import LegacyFileSource
from zhiheng.infrastructure.file_storage import INSPECTION_CHUNK_BYTES, MAX_FILE_BYTES, S3FileStorage
from zhiheng.shared.errors import AppError

KEY = 'a' * 32


class Response(io.BytesIO):
    def __init__(self, content: bytes, *, length: int | None = None) -> None:
        super().__init__(content)
        self.headers = {'content-length': str(len(content) if length is None else length)}
        self.released = False
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)

    def release_conn(self) -> None:
        self.released = True


@pytest.fixture
def storage() -> S3FileStorage:
    result = S3FileStorage('http://objects:9000', 'application-key', 'application-secret', 'private-files')
    result._client = MagicMock(spec=Minio)
    return result


@pytest.mark.parametrize('operation', ['read', 'stat', 'inspect', 'delete', 'import_object'])
@pytest.mark.parametrize('key', ['../secret', 'A' * 32, 'a' * 31, 'folder/' + KEY])
async def test_opaque_key_validation_precedes_network(storage: S3FileStorage, operation: str, key: str) -> None:
    with pytest.raises(AppError) as error:
        await getattr(storage, operation)(key, b'hello') if operation == 'import_object' else await getattr(
            storage, operation
        )(key)
    assert error.value.code == 'invalid_storage_key'
    assert not storage._client.mock_calls


@pytest.mark.parametrize('operation', ['read', 'inspect'])
@pytest.mark.parametrize(
    'content,length',
    [(b'', 0), (b'x' * (MAX_FILE_BYTES + 1), None), (b'partial', 100)],
    ids=['empty', 'oversized', 'truncated'],
)
async def test_invalid_or_incomplete_objects_fail_closed_and_release_connection(
    storage: S3FileStorage, operation: str, content: bytes, length: int | None
) -> None:
    response = Response(content, length=length)
    storage._client.get_object.return_value = response
    with pytest.raises(AppError) as error:
        await getattr(storage, operation)(KEY)
    assert error.value.code == 'file_bytes_unavailable'
    assert response.closed and response.released


async def test_read_integrity_inspection_is_streamed_and_stat_avoids_content(storage: S3FileStorage) -> None:
    content = b'inspection' * (INSPECTION_CHUNK_BYTES // 3)
    response = Response(content)
    storage._client.get_object.return_value = response
    modified = datetime.now(timezone.utc)
    storage._client.stat_object.return_value = SimpleNamespace(size=len(content), last_modified=modified)
    assert (await storage.stat(KEY)).modified_at == modified
    storage._client.get_object.assert_not_called()
    result = await storage.inspect(KEY)
    assert result.size == len(content) and result.sha256 == hashlib.sha256(content).hexdigest()
    assert len(response.read_sizes) > 2 and set(response.read_sizes) == {INSPECTION_CHUNK_BYTES}
    assert response.closed and response.released


async def test_upload_failure_is_reported_without_local_fallback(storage: S3FileStorage, tmp_path: Path) -> None:
    storage._client.put_object.side_effect = HTTPError('offline')
    with pytest.raises(AppError) as error:
        await storage.put(b'hello')
    assert error.value.code == 'file_storage_unavailable'
    assert not await asyncio.to_thread(lambda: list(tmp_path.iterdir()))


@pytest.mark.parametrize('content', [b'', b'x' * (MAX_FILE_BYTES + 1)], ids=['empty', 'oversized'])
async def test_upload_limits_are_unchanged(storage: S3FileStorage, content: bytes) -> None:
    with pytest.raises(AppError) as error:
        await storage.put(content)
    assert error.value.code == 'invalid_file_size'
    storage._client.put_object.assert_not_called()


async def test_object_listing_uses_cursor_and_stops_at_requested_page(storage: S3FileStorage) -> None:
    now = datetime.now(timezone.utc)
    visited = []

    def objects() -> Iterator[SimpleNamespace]:
        for key in ['ignored.json', 'b' * 32, 'c' * 32, 'd' * 32]:
            visited.append(key)
            yield SimpleNamespace(object_name=key, last_modified=now)

    storage._client.list_objects.return_value = objects()
    result = await storage.entries(after=KEY, limit=2)
    assert [item.key for item in result] == ['b' * 32, 'c' * 32]
    assert visited == ['ignored.json', 'b' * 32, 'c' * 32]
    storage._client.list_objects.assert_called_once_with('private-files', recursive=True, start_after=KEY)


async def test_bucket_must_be_provisioned_before_readiness(storage: S3FileStorage) -> None:
    storage._client.bucket_exists.return_value = False
    with pytest.raises(AppError) as error:
        await storage.check()
    assert error.value.code == 'file_storage_unavailable'
    storage._client.make_bucket.assert_not_called()


@pytest.mark.parametrize('operation', ['read', 'stat', 'inspect'])
@pytest.mark.parametrize(
    'failure,expected', [('network', 'file_storage_unavailable'), ('missing', 'file_bytes_unavailable')]
)
async def test_outage_is_distinct_from_a_missing_object(
    storage: S3FileStorage, operation: str, failure: str, expected: str
) -> None:
    error = (
        HTTPError('temporarily offline')
        if failure == 'network'
        else S3Error(response=None, code='NoSuchKey', message='missing', resource='', request_id='', host_id='')
    )
    call = storage._client.stat_object if operation == 'stat' else storage._client.get_object
    call.side_effect = error
    with pytest.raises(AppError) as caught:
        await getattr(storage, operation)(KEY)
    assert caught.value.code == expected


async def test_migration_reuses_equal_objects_and_refuses_conflicting_bytes(storage: S3FileStorage) -> None:
    storage._client.get_object.side_effect = [Response(b'original'), Response(b'different')]
    assert not await storage.import_object(KEY, b'original')
    with pytest.raises(AppError) as error:
        await storage.import_object(KEY, b'original')
    assert error.value.code == 'file_migration_conflict'
    storage._client.put_object.assert_not_called()


async def test_migration_only_writes_when_target_is_proven_missing(storage: S3FileStorage) -> None:
    storage._client.get_object.side_effect = S3Error(
        response=None, code='AccessDenied', message='denied', resource='', request_id='', host_id=''
    )
    with pytest.raises(AppError):
        await storage.import_object(KEY, b'original')
    storage._client.put_object.assert_not_called()
    storage._client.get_object.side_effect = S3Error(
        response=None, code='NoSuchKey', message='missing', resource='', request_id='', host_id=''
    )
    assert await storage.import_object(KEY, b'original')
    assert storage._client.put_object.call_args.args[1] == KEY


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'empty', 'oversized', 'directory'])
async def test_one_time_legacy_reader_rejects_unsafe_sources(tmp_path: Path, kind: str) -> None:
    import asyncio
    import os

    path = tmp_path / KEY
    if kind == 'symlink':
        outside = tmp_path / 'outside'
        outside.write_bytes(b'secret')
        path.symlink_to(outside)
    elif kind == 'fifo':
        os.mkfifo(path)
    elif kind == 'directory':
        path.mkdir()
    else:
        with path.open('wb') as stream:
            stream.truncate(MAX_FILE_BYTES + 1 if kind == 'oversized' else 0)
    source = LegacyFileSource(tmp_path)
    with pytest.raises(AppError):
        await asyncio.wait_for(source.read(KEY), timeout=2)
    with pytest.raises(AppError):
        await source.read('../outside')
