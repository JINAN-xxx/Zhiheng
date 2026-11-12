"""Interpret business references and console links above the opaque Run store."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.runtime.contracts import ActiveRunReference
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.references import ResourceReference


class RunReferences:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    async def connection_references(self, session: AsyncSession, connection_id: UUID) -> list[ResourceReference]:
        rows = await self.runs.active_references(
            session, payload_path=('binding', 'connection_id'), payload_value=str(connection_id)
        )
        return [self.project(row) for row in rows]

    async def references_connection(self, session: AsyncSession, connection_id: UUID) -> bool:
        return bool(await self.connection_references(session, connection_id))

    async def model_references(self, session: AsyncSession, model_id: UUID) -> list[ResourceReference]:
        rows = await self.runs.active_references(
            session, payload_path=('binding', 'model_id'), payload_value=str(model_id)
        )
        return [self.project(row) for row in rows]

    async def agent_references(self, session: AsyncSession, agent_id: UUID) -> list[ResourceReference]:
        rows = await self.runs.active_references(
            session, resource_types=('agent_preview', 'agent_invocation'), resource_id=agent_id
        )
        return [self.project(row) for row in rows]

    @staticmethod
    def project(row: ActiveRunReference) -> ResourceReference:
        path = None
        if row.resource_type in {'agent_preview', 'agent_invocation'}:
            path = f'/agents/{row.resource_id}'
        elif row.resource_type == 'work_turn':
            path = f'/work?taskId={row.resource_id}'
        elif row.resource_type == 'connection_test':
            path = '/models'
        return ResourceReference('run', row.id, '进行中的运行', row.status, row.owner_id, path)
