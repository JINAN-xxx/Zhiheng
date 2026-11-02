"""Record native cancellation intent without claiming an executing provider has stopped."""

from uuid import UUID

from zhiheng.application.access import CallerContext
from zhiheng.application.operations.contracts import OperationInfo
from zhiheng.application.operations.queries import OperationQueries
from zhiheng.shared.errors import AppError


class OperationCancellation:
    def __init__(self, queries: OperationQueries) -> None:
        self.queries = queries

    async def cancel(self, caller: CallerContext, request_id: UUID) -> OperationInfo:
        assert caller.identity is not None
        async with self.queries.database.sessions.begin() as session:
            request = await self.queries.access.requests.get(session, caller.identity.application_id, request_id)
            await self.queries.authorize(session, caller, request, cancel=True)
            if request.result_kind == 'knowledge_job' and request.result_id is not None:
                await self.queries.knowledge.cancel_job(
                    session, self.queries.access.personal(caller.principal), request.result_id
                )
            elif request.result_kind == 'knowledge_preparation' and request.result_id is not None:
                await self.queries.preparation.store.request_cancel(session, request.result_id)
            else:
                raise AppError('operation_not_found', '操作不存在', 404)
            return await self.queries.project(session, caller, request)
