"""Map inference DTOs into the existing durable admission use case."""

from uuid import UUID

from fastapi import Request

from zhiheng.api.common.auth import bearer
from zhiheng.api.inference.schemas import PublicBody
from zhiheng.bootstrap.container import Container
from zhiheng.modules.models.public import ModelMessage
from zhiheng.runtime.contracts import RunSnapshot


async def admit(
    container: Container,
    request: Request,
    operation: str,
    messages: tuple[ModelMessage, ...],
    body: PublicBody,
    options: dict[str, object],
) -> tuple[RunSnapshot, UUID, str]:
    token = bearer(request)
    await container.database.require_schema()
    async with container.database.sessions.begin() as session:
        run, identity = await container.public_runs.admit(
            session,
            token,
            model_id=body.model,
            operation=operation,
            messages=messages,
            options=options,
            token_limit=container.settings.token_limit,
        )
    return run, identity.owner_id, token
