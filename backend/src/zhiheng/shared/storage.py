from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class StorageEntry:
    key: str
    modified_at: datetime


class StoragePort(Protocol):
    async def put(self, content: bytes) -> str: ...
    async def read(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def entries(self, *, after: str = '', limit: int = 100) -> tuple[StorageEntry, ...]: ...
