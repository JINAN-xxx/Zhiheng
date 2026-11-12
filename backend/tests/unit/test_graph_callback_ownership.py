import asyncio

import pytest

from zhiheng.modules.knowledge.adapters.lightrag.sdk import _callbacks
from zhiheng.modules.knowledge.public import GraphSDKCall, VectorIOControl
from zhiheng.shared.errors import AppError


async def test_cancelled_control_cannot_submit_a_new_actual_model_bridge() -> None:
    calls = []

    async def model(call: GraphSDKCall) -> str:
        calls.append(call)
        return 'already submitted'

    control = VectorIOControl(timeout_seconds=5)
    llm, _ = _callbacks(model, asyncio.get_running_loop(), control, 2)
    control.cancel()
    with pytest.raises(AppError, match='已取消'):
        await asyncio.to_thread(lambda: asyncio.run(llm('Cancelled before bridge admission')))
    # Let the already posted cross-loop callback complete, if one escaped admission.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls == [], 'Cancellation was checked only after scheduling new model work'
