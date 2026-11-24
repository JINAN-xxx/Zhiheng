"""Application-owner diagnostics without credentials or request contents."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.keys import DeveloperIdentity
from zhiheng.modules.developer.orm import PublicRequestLog


async def record_public_request(
    session: AsyncSession,
    identity: DeveloperIdentity,
    request_id: UUID,
    method: str,
    route: str,
    status: int,
    error_code: str | None,
) -> None:
    session.add(
        PublicRequestLog(
            id=request_id,
            application_id=identity.application_id,
            key_id=identity.key_id,
            method=method,
            route=route,
            status=status,
            error_code=error_code,
        )
    )


async def list_public_requests(
    session: AsyncSession, application_id: UUID, page: int, page_size: int
) -> dict[str, object]:
    query = select(PublicRequestLog).where(PublicRequestLog.application_id == application_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(
        query.order_by(PublicRequestLog.created_at.desc(), PublicRequestLog.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        'items': [
            {
                'id': row.id,
                'method': row.method,
                'route': row.route,
                'status': row.status,
                'error_code': row.error_code,
                'created_at': row.created_at,
            }
            for row in rows
        ],
        'total': total or 0,
        'page': page,
        'page_size': page_size,
    }
