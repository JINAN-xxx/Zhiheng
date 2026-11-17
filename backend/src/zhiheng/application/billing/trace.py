"""Compose financial traceability without granting execution-content access."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from zhiheng.infrastructure.database import Database
from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.billing.public import BillingTrace, BillingTraceQueries, InvocationReference
from zhiheng.modules.chat.public import ContextMemory
from zhiheng.runtime.contracts import RunFacts
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

RELATED_LIMIT = 20


class RelatedInvocation(InvocationReference):
    relation: Literal['parent', 'summary']


class InvocationTrace(BaseModel):
    billing: BillingTrace
    execution: RunFacts | None
    related: list[RelatedInvocation]
    related_has_more: bool
    observed_at: datetime


class InvocationTraceService:
    def __init__(
        self,
        database: Database,
        billing: BillingTraceQueries,
        runs: RunStore,
        memory: ContextMemory,
        agents: AgentService,
    ) -> None:
        self._database, self._billing, self._runs, self._memory = database, billing, runs, memory
        self._agents = agents

    async def detail(self, actor: Principal, run_id: UUID, *, ledger_page: int = 1) -> InvocationTrace:
        async with self._database.sessions() as session:
            # All projections describe one committed snapshot. Reads cannot create or settle anything.
            await session.connection(
                execution_options={'isolation_level': 'REPEATABLE READ', 'postgresql_readonly': True}
            )
            billing = await self._billing.detail(session, actor, run_id, ledger_page=ledger_page)
            execution = await self._runs.facts(session, run_id, billing.invocation.user_id)
            ids: list[UUID] = []
            relation: Literal['parent', 'summary'] = 'summary'
            if execution is not None and execution.resource_type == 'chat':
                ids = await self._memory.summary_run_ids(session, run_id, execution.owner_id, limit=RELATED_LIMIT + 1)
            elif execution is not None and execution.resource_type == 'chat_summary':
                relation = 'parent'
                parent = await self._memory.summary_parent_id(session, run_id, execution.owner_id)
                if parent is not None:
                    ids = [parent]
            if execution is not None and execution.resource_type == 'agent_chat':
                ids = await self._agents.summary_run_ids(session, run_id, execution.owner_id, limit=RELATED_LIMIT + 1)
            elif execution is not None and execution.resource_type == 'agent_summary':
                relation = 'parent'
                parent = await self._agents.summary_parent_id(session, run_id, execution.owner_id)
                ids = [parent] if parent else []
            references = await self._billing.references(session, actor, billing.invocation.user_id, ids)
            return InvocationTrace(
                billing=billing,
                execution=execution,
                related=[
                    RelatedInvocation(**ref.model_dump(), relation=relation) for ref in references[:RELATED_LIMIT]
                ],
                related_has_more=len(references) > RELATED_LIMIT,
                observed_at=utcnow(),
            )
