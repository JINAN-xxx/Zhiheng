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
