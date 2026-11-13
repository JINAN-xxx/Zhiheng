import asyncio
import gc
import threading
from typing import Any

import pytest

from zhiheng.modules.knowledge.adapters.lightrag.sdk import _callbacks
from zhiheng.modules.knowledge.public import GraphSDKCall, VectorIOControl
from zhiheng.shared.errors import AppError


async def test_cancelled_bridge_observes_model_error_raised_during_cancellation() -> None:
    entered = threading.Event()
    finished = asyncio.Event()
    main_loop = asyncio.get_running_loop()
    unobserved: list[dict[str, Any]] = []
    previous = main_loop.get_exception_handler()
    main_loop.set_exception_handler(lambda _loop, context: unobserved.append(context))

    async def model(_call: GraphSDKCall) -> str:
        entered.set()
        try:
            await asyncio.Future()
        finally:
            finished.set()
            raise AppError('graph_cancelled', 'Late model cleanup failure', 409)

    llm, _ = _callbacks(model, main_loop, VectorIOControl(timeout_seconds=5), 2)

    async def sdk_program() -> None:
        task = asyncio.create_task(llm('cancel the actual bridge'))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        await asyncio.to_thread(lambda: asyncio.run(sdk_program()))
        await asyncio.wait_for(finished.wait(), 2)
        # The cancelled concurrent future must not leave its source Task's error unobserved.
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        assert unobserved == []
    finally:
        main_loop.set_exception_handler(previous)


@pytest.mark.parametrize('cancel_count', [1, 3])
async def test_cancelled_bridge_drains_the_actual_model_cleanup(cancel_count: int) -> None:
    entered = threading.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    callback_returned = threading.Event()
    main_loop = asyncio.get_running_loop()

    async def model(_call: GraphSDKCall) -> str:
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()

    llm, _ = _callbacks(model, main_loop, VectorIOControl(timeout_seconds=5), 2)

    async def sdk_program() -> None:
        task = asyncio.create_task(llm('cancel while model cleanup is pending'))
        assert await asyncio.to_thread(entered.wait, 2)
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0.01)
        with pytest.raises(asyncio.CancelledError):
            await task
        callback_returned.set()

    sdk = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(sdk_program())))
    try:
        await asyncio.wait_for(cleanup_started.wait(), 2)
        await asyncio.sleep(0.06)
        assert not callback_returned.is_set(), 'Bridge returned before the actual model cleanup finished'
    finally:
        release_cleanup.set()
        await sdk


@pytest.mark.parametrize('operation', ['llm', 'embedding'])
async def test_non_cancelled_bridge_still_propagates_model_errors(operation: str) -> None:
    failure = AppError('model_forbidden', 'Current model authorization denied', 403)

    async def model(_call: GraphSDKCall) -> str:
        raise failure

    llm, embedding = _callbacks(model, asyncio.get_running_loop(), VectorIOControl(timeout_seconds=5), 2)

    async def sdk_program() -> None:
        with pytest.raises(AppError) as caught:
            if operation == 'llm':
                await llm('normal model failure')
            else:
                await embedding(['normal model failure'])
        assert caught.value is failure

    await asyncio.to_thread(lambda: asyncio.run(sdk_program()))
