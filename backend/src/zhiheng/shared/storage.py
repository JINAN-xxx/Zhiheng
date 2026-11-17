from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class StorageEntry:
    key: str
    modified_at: datetime


@dataclass(frozen=True)
class StorageStat:
    size: int
    modified_at: datetime


@dataclass(frozen=True)
class StorageInspection:
    size: int
    sha256: str


class StoragePort(Protocol):
    async def put(self, content: bytes) -> str: ...
    async def read(self, key: str) -> bytes: ...
    async def stat(self, key: str) -> StorageStat: ...
    async def inspect(self, key: str) -> StorageInspection: ...
    async def delete(self, key: str) -> None: ...
    async def entries(self, *, after: str = '', limit: int = 100) -> tuple[StorageEntry, ...]: ...
