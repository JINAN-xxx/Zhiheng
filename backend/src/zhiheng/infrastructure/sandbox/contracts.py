"""Execution capabilities shared by task orchestration and isolated environment adapters."""

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class SandboxInput:
    path: str
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class SandboxFile:
    path: str
    content: bytes = field(repr=False)
    sha256: str
    mime: str
    validation: dict[str, object] = field(default_factory=dict)
    previews: tuple[SandboxInput, ...] = ()


@dataclass(frozen=True)
class SandboxCommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False
    environment: tuple[str, ...] = ()


class SandboxSession(Protocol):
    @property
    def id(self) -> str: ...

    async def execute(
        self,
        code: str,
        *,
        kind: Literal['python', 'shell'] = 'python',
        timeout: int = 45,
        packages: tuple[str, ...] = (),
    ) -> SandboxCommandResult: ...

    async def read_file(self, path: str, *, max_bytes: int = 65536) -> str: ...
    async def read_binary(self, path: str, *, max_bytes: int = 20 * 1024 * 1024) -> bytes: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def add_input(self, item: SandboxInput) -> None: ...
    async def list_files(self, path: str = '.') -> tuple[dict[str, object], ...]: ...
    async def collect_outputs(self) -> tuple[SandboxFile, ...]: ...
    async def snapshot_files(self) -> tuple[SandboxInput, ...]: ...
    async def close(self) -> None: ...


class SandboxFactory(Protocol):
    @property
    def available(self) -> bool: ...

    async def check(self) -> bool: ...

    async def create(
        self,
        run_id: UUID,
        attempt_id: UUID,
        *,
        inputs: tuple[SandboxInput, ...] = (),
        initial_files: tuple[SandboxInput, ...] = (),
    ) -> SandboxSession: ...

    async def kill(self, sandbox_id: str) -> None: ...
    async def close(self) -> None: ...
