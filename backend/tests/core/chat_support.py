"""Shared local chat execution support; no remote models or production resources."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select

from zhiheng.modules.chat.context_orm import ContextSnapshot, ContextState
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests.core.provider import ProviderServer


async def execute_with_summaries(app: FastAPI, run_id: str) -> None:
    """Pump the same native child Runs an independent short worker would execute."""
    container = app.state.container
    parent = asyncio.create_task(execute_run(container, UUID(run_id)))

    async def pump() -> None:
        while not parent.done():
            async with container.database.sessions() as session:
                children = list(
                    await session.scalars(
                        select(Run.id).where(Run.resource_type == 'chat_summary', Run.status == 'pending')
                    )
                )
            for child in children:
                await execute_run(container, child)
            await asyncio.sleep(0.01)
        await parent

    try:
        await asyncio.wait_for(pump(), timeout=10)
    finally:
        if not parent.done():
            parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)


async def conversation(admin: httpx.AsyncClient) -> str:
    response = await admin.post('/api/chat/conversations', json={'title': 'Agent integration'})
    assert response.status_code == 201, response.text
    return str(response.json()['id'])


async def admit(
    admin: httpx.AsyncClient, conversation_id: str, model_id: str, message: str, *, request_id: str | None = None
) -> dict[str, Any]:
    response = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={'model_id': model_id, 'message': message, 'client_request_id': request_id or str(uuid4())},
    )
    assert response.status_code == 202, response.text
    assert response.json()['resource_type'] == 'chat'
    return cast('dict[str, Any]', response.json())


async def completed(admin: httpx.AsyncClient, app: FastAPI, run_id: str) -> dict[str, Any]:
    await execute_run(app.state.container, UUID(run_id))
    response = await admin.get(f'/api/runs/{run_id}')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'completed', result
    assert result['text'] == '你好'
    return cast('dict[str, Any]', result)


def wire_messages(provider: ProviderServer, index: int = -1) -> list[tuple[str, str]]:
    messages = cast('list[dict[str, str]]', provider.requests[index]['messages'])
    return [(message['role'], message['content']) for message in messages if message['role'] != 'system']


async def memory_head(app: FastAPI, conversation_id: str) -> str | None:
    async with app.state.container.database.sessions() as session:
        return await session.scalar(
            select(ContextState.agent_checkpoint_id).where(ContextState.conversation_id == UUID(conversation_id))
        )


async def frozen_context(app: FastAPI, run_id: str) -> ContextSnapshot:
    async with app.state.container.database.sessions() as session:
        snapshot = await session.get(ContextSnapshot, UUID(run_id))
        assert snapshot is not None and snapshot.agent_context is not None
        return snapshot
