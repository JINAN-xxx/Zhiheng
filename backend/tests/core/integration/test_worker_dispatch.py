from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from fastapi import FastAPI


async def test_outbox_delivery_projects_only_the_requested_opaque_hint(app: 'FastAPI') -> None:
    container = app.state.container
    async with container.database.sessions.begin() as session:
        run = await container.runs.create(
            session,
            owner_id=uuid4(),
            resource_type='connection_test',
            resource_id=uuid4(),
            request_id=uuid4(),
            fingerprint='worker-dispatch-projection',
            payload={'binding': {'model_type': 'embedding'}, 'unrelated': 'x' * 1_000_000},
        )
    async with container.database.sessions() as session:
        deliveries = await container.outbox.pending(session, payload_path=('binding', 'model_type'))
    assert len(deliveries) == 1
    assert deliveries[0].run_id == run.id
    assert deliveries[0].hint == 'embedding'
