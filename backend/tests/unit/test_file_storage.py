from pathlib import Path

import pytest


async def test_local_storage_atomic_and_symlink(tmp_path: Path) -> None:
    from zhiheng.infrastructure.file_storage import LocalFileStorage
    from zhiheng.shared.errors import AppError

    storage = LocalFileStorage(tmp_path / 'files')
    key = await storage.put(b'hello')
    assert await storage.read(key) == b'hello'
    with pytest.raises(AppError):
        await storage.read('../outside')
    outside = tmp_path / 'outside'
    outside.write_bytes(b'secret')
    (tmp_path / 'files' / key).unlink()
    (tmp_path / 'files' / key).symlink_to(outside)
    with pytest.raises(AppError):
        await storage.read(key)
    assert outside.read_bytes() == b'secret'


async def test_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    import os

    from zhiheng.infrastructure.file_storage import LocalFileStorage
    from zhiheng.shared.errors import AppError

    store = LocalFileStorage(tmp_path / 'files')
    key = await store.put(b'x')
    (tmp_path / 'files' / key).unlink()
    os.mkfifo(tmp_path / 'files' / key)
    with pytest.raises(AppError):
        await store.read(key)


async def test_storage_inspection_streams_and_stat_does_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import io
    from typing import BinaryIO

    from zhiheng.infrastructure import file_storage

    storage = file_storage.LocalFileStorage(tmp_path / 'files')
    content = b'inspection' * (file_storage.INSPECTION_CHUNK_BYTES // 3)
    key = await storage.put(content)
    sizes = []
    fdopen = file_storage.os.fdopen

    class BoundedReader(io.BufferedReader):
        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= file_storage.INSPECTION_CHUNK_BYTES
            sizes.append(size)
            return super().read(size)

    def bounded(fd: int, mode: str) -> BinaryIO:
        return BoundedReader(fdopen(fd, mode))

    monkeypatch.setattr(file_storage.os, 'fdopen', bounded)
    info = await storage.stat(key)
    assert info.size == len(content) and not sizes
    inspected = await storage.inspect(key)
    assert inspected.size == len(content) and inspected.sha256 == hashlib.sha256(content).hexdigest()
    assert len(sizes) > 2


@pytest.mark.parametrize('operation', ['read', 'stat', 'inspect'])
@pytest.mark.parametrize('kind', ['empty', 'oversized', 'symlink', 'fifo', 'directory'])
async def test_inspection_and_stat_reject_unsafe_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, kind: str
) -> None:
    import asyncio
    import os

    from zhiheng.infrastructure.file_storage import MAX_FILE_BYTES, LocalFileStorage
    from zhiheng.shared.errors import AppError

    storage = LocalFileStorage(tmp_path / 'files')
    key = await storage.put(b'original')
    path = tmp_path / 'files' / key
    path.unlink()
    if kind in {'empty', 'oversized'}:
        with path.open('wb') as stream:
            stream.truncate(MAX_FILE_BYTES + 1 if kind == 'oversized' else 0)
    elif kind == 'symlink':
        outside = tmp_path / 'outside'
        outside.write_bytes(b'outside')
        path.symlink_to(outside)
    elif kind == 'fifo':
        os.mkfifo(path)
    else:
        path.mkdir()
    opened = []
    open_object = os.open

    def observed_open(path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        descriptor = open_object(path, flags, mode, dir_fd=dir_fd)
        if path == key:
            opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, 'open', observed_open)
    with pytest.raises(AppError) as error:
        await asyncio.wait_for(getattr(storage, operation)(key), timeout=2)
    assert error.value.code == 'file_bytes_unavailable'
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    with pytest.raises(AppError) as error:
        await getattr(storage, operation)('../outside')
    assert error.value.code == 'invalid_storage_key'


async def test_inspection_accepts_maximum_object_size(tmp_path: Path) -> None:
    import hashlib

    from zhiheng.infrastructure.file_storage import INSPECTION_CHUNK_BYTES, MAX_FILE_BYTES, LocalFileStorage

    storage = LocalFileStorage(tmp_path / 'files')
    key = await storage.put(b'x')
    with (tmp_path / 'files' / key).open('wb') as stream:
        stream.truncate(MAX_FILE_BYTES)
    expected = hashlib.sha256()
    for _ in range(MAX_FILE_BYTES // INSPECTION_CHUNK_BYTES):
        expected.update(bytes(INSPECTION_CHUNK_BYTES))
    assert (await storage.stat(key)).size == MAX_FILE_BYTES
    result = await storage.inspect(key)
    assert result.size == MAX_FILE_BYTES and result.sha256 == expected.hexdigest()


async def test_storage_pages_use_bounded_directory_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from zhiheng.infrastructure import file_storage

    storage = file_storage.LocalFileStorage(tmp_path / 'files')
    keys = {await storage.put(str(index).encode()) for index in range(11)}
    (tmp_path / 'files' / 'ignored.partial').write_bytes(b'unfinished')

    def forbid_listdir(_directory: int) -> list[str]:
        raise AssertionError('Directory pages must not allocate the complete object listing')

    monkeypatch.setattr(file_storage.os, 'listdir', forbid_listdir)
    observed = []
    cursor = ''
    while True:
        page = await storage.entries(after=cursor, limit=3)
        assert len(page) <= 3
        observed.extend(item.key for item in page)
        if len(page) < 3:
            break
        cursor = page[-1].key
    assert observed == sorted(keys)
