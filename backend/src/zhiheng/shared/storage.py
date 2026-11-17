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


class StorageSourcePort(Protocol):
    async def check(self) -> None: ...
    async def read(self, key: str) -> bytes: ...


class StoragePort(StorageSourcePort, Protocol):
    async def put(self, content: bytes) -> str: ...
    async def stat(self, key: str) -> StorageStat: ...
    async def inspect(self, key: str) -> StorageInspection: ...
    async def delete(self, key: str) -> None: ...
    async def entries(self, *, after: str = '', limit: int = 100) -> tuple[StorageEntry, ...]: ...


class ImportStoragePort(StoragePort, Protocol):
    async def import_object(self, key: str, content: bytes) -> bool: ...
