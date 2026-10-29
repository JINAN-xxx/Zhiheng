"""Billing mode preparation checks the full published model inventory."""

from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import select

from zhiheng.modules.models.orm import ModelConnection, ModelDefinition


async def test_billing_model_inventory_has_no_catalog_page_limit_and_keeps_visibility_rules(
    app: FastAPI,
    model_id: str,
) -> None:
    container = app.state.container
    async with container.database.sessions.begin() as session:
        original = await session.get(ModelDefinition, UUID(model_id))
        fields = {'connection_id': original.connection_id, 'upstream_model': 'local', 'max_output_tokens': 64}
        session.add_all(ModelDefinition(name=f'Billing model {index}', **fields) for index in range(1000))
        session.add(ModelDefinition(name='Unpublished model', published=False, **fields))
        session.add(ModelDefinition(name='Disabled model', enabled=False, **fields))
        await session.flush()
        assert len(await container.models.list_models(session)) == 1000
        inventory = await container.models.billing_models(session)
        assert len(inventory) == 1001
        assert all(row.enabled and row.published for row in inventory)
        connection = await session.scalar(select(ModelConnection).where(ModelConnection.id == original.connection_id))
        connection.enabled = False
        await session.flush()
        assert await container.models.billing_models(session) == []
