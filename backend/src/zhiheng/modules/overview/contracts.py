"""Read-only workbench projections; resource records never contain private configuration."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

ResourceKind = Literal['model', 'agent', 'knowledge', 'skill', 'application']


class WorkbenchResource(BaseModel):
    kind: ResourceKind
    id: UUID
    name: str
    occurred_at: datetime
    status: str
    action: Literal['used', 'created', 'published', 'indexed', 'uploaded', 'failed'] = 'used'


class WorkbenchAlert(BaseModel):
    id: str
    level: Literal['info', 'warning', 'error']
    title: str
    description: str
    kind: ResourceKind | None = None
    resource_id: UUID | None = None
    occurred_at: datetime | None = None


class DailyCalls(BaseModel):
    day: date
    calls: int


class WorkbenchMetrics(BaseModel):
    today_calls: int
    yesterday_calls: int
    successful_calls: int
    failed_calls: int
    cancelled_calls: int
    today_known_tokens: int
    today_unconfirmed_calls: int
    trend: list[DailyCalls]


class WorkbenchCredits(BaseModel):
    enabled: bool
    monthly_points: str
    unconfirmed_calls: int


class WorkbenchOverview(BaseModel):
    scope: Literal['mine', 'platform']
    can_view_platform: bool
    period_days: Literal[7, 30]
    timezone: str = 'Asia/Shanghai'
    observed_at: datetime
    metrics: WorkbenchMetrics | None
    credits: WorkbenchCredits | None
    recent: list[WorkbenchResource]
    activities: list[WorkbenchResource]
    alerts: list[WorkbenchAlert]
