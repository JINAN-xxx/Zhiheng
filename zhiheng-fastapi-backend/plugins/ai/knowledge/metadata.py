"""Atomic JSON metadata with advisory locks shared by processes and runtimes."""

import asyncio
import fcntl
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from plugins.ai.knowledge.models import KnowledgeError


class MetadataStore:
    def __init__(self, data_dir: Path) -> None:
        self.directory = data_dir
        self.path = data_dir / 'metadata.json'

    @asynccontextmanager
    async def lock(self, name: str = 'metadata') -> AsyncIterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.directory / f'.{name}.lock'
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            deadline = time.monotonic() + 120
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise KnowledgeError('KNOWLEDGE_BUSY', '知识库正在处理中，请稍后重试', 409) from None
                    await asyncio.sleep(0.02)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read(self) -> dict:
        if not self.path.exists():
            return {'version': 1, 'databases': {}, 'documents': {}, 'uploads': {}, 'folders': {}}
        try:
            if self.path.is_symlink():
                raise ValueError('invalid metadata path')
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('version') != 1 or any(
                not isinstance(value.get(key), dict) for key in ('databases', 'documents', 'uploads', 'folders')
            ):
                raise ValueError('invalid metadata')
            return value
        except (OSError, ValueError, TypeError):
            raise KnowledgeError.unavailable() from None

    def _write(self, value: dict) -> None:
        temporary = self.directory / f'.metadata-{uuid4().hex}.tmp'
        try:
            with temporary.open('x', encoding='utf-8') as output:
                os.chmod(temporary, 0o600)
                json.dump(value, output, ensure_ascii=False, separators=(',', ':'))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[dict]:
        async with self.lock():
            value = await asyncio.to_thread(self._read)
            yield value
            # Shield only the short atomic commit, never downstream model/storage I/O.
            task = asyncio.create_task(asyncio.to_thread(self._write, value))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def read(self) -> dict:
        async with self.lock():
            return await asyncio.to_thread(self._read)
