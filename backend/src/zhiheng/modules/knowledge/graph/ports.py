"""The application-facing graph runtime capability."""

from collections.abc import Awaitable, Callable
from typing import Protocol

from zhiheng.modules.knowledge.control import VectorIOControl
from zhiheng.modules.knowledge.graph.contracts import (
    GraphFrozen,
    GraphSDKCall,
    GraphSDKOutcome,
    GraphVectorManifest,
)


class GraphPort(Protocol):
    async def available(self) -> bool: ...

    async def execute(
        self,
        *,
        operation: str,
        frozen: GraphFrozen,
        manifest: GraphVectorManifest,
        request: dict[str, object],
        model: Callable[[GraphSDKCall], Awaitable[object]],
        control: VectorIOControl,
        fence: Callable[[], Awaitable[None]] | None = None,
        stopped: Callable[[], None] | None = None,
    ) -> GraphSDKOutcome: ...

    async def close(self) -> None: ...

    async def gc(self, manifest: GraphVectorManifest) -> None: ...
