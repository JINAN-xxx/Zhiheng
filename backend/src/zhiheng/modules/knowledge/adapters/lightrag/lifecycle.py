"""Per-attempt ownership survives partial construction and repeated cancellation."""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any

from zhiheng.modules.knowledge.adapters.lightrag.factory import shutdown_rag
from zhiheng.modules.knowledge.adapters.lightrag.owned_loop import _CURRENT_TASKS
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from collections.abc import Callable
    from tempfile import TemporaryDirectory
    from types import TracebackType

    from zhiheng.modules.knowledge.adapters.lightrag.postgres import PostgresContext
    from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC
    from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl

logger = logging.getLogger(__name__)


class AttemptResources:
    def __init__(self, control: VectorIOControl, stopped: Callable[[], None] | None) -> None:
        self.control, self.stopped = control, stopped
        self.tasks: set[asyncio.Task[Any]] = set()
        self.tokens = ExitStack()
        self.context: PostgresContext | None = None
        self.temporary: TemporaryDirectory[str] | None = None
        self.rpcs: list[GraphVectorRPC] = []
        self.rag: Any = None
        self.completed = False

    async def __aenter__(self) -> AttemptResources:
        try:
            self.tokens.callback(_CURRENT_TASKS.reset, _CURRENT_TASKS.set(self.tasks))
        except BaseException:
            self.tokens.close()
            raise
        return self

    async def __aexit__(
        self, _kind: type[BaseException] | None, failure: BaseException | None, _traceback: TracebackType | None
    ) -> None:
        await self.close(failure)

    async def _release(self, errors: list[BaseException]) -> None:
        try:
            await shutdown_rag(self.rag, self.tasks, on_error=self.control.cancel)
        except BaseException as error:
            errors.append(error)
            self.control.cancel()
        # This registry exists before create_rag returns, including its failure.
        for rpc in self.rpcs:
            try:
                await rpc.aclose()
            except BaseException as error:  # noqa: PERF203 - retain every independent close obligation
                errors.append(error)
        if self.context is not None:
            try:
                await self.context.close()
            except BaseException as error:
                errors.append(error)

    def _release_local(self, errors: list[BaseException]) -> bool:
        released = True
        try:
            self.tokens.close()
        except BaseException as error:
            errors.append(error)
            released = False
        if self.temporary is not None:
            try:
                self.temporary.cleanup()
            except BaseException as error:
                errors.append(error)
                released = False
        return released

    def _quiescent(self) -> bool:
        return (
            all(task.done() for task in self.tasks)
            and all(rpc.stopped for rpc in self.rpcs)
            and (self.context is None or (self.context.db.pool is None and not self.context.leases))
        )

    async def close(self, failure: BaseException | None) -> None:
        if not self.completed:
            self.control.cancel()
        errors: list[BaseException] = []
        cleanup = asyncio.create_task(self._release(errors), name='graph-attempt-cleanup')
        cancelled = False
        try:
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:  # noqa: PERF203 - actual drain keeps one owner through repeated cancel
                    cancelled = True
                    self.control.cancel()
            cleanup.result()
        finally:
            # Tokens must be reset here in the original task's Context, never
            # in the shielded task that inherited a different Context.
            local_released = self._release_local(errors)
        quiescent = local_released and self._quiescent()
        if quiescent and self.stopped is not None:
            self.stopped()
        for cleanup_error in errors[1:]:
            logger.warning('Additional graph cleanup failure error_type=%s', type(cleanup_error).__name__)
        if errors:
            if failure is not None:
                raise failure from errors[0]
            raise errors[0]
        if cancelled:
            raise asyncio.CancelledError
        if not quiescent:
            raise AppError('graph_writer_not_stopped', '图谱写入尚未停止', 409)
