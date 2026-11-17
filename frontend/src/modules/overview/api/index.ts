import { http } from '../../../shared/http.ts'
import { boolean, list, number, object, string } from '../../../shared/validation.ts'

export type ResourceKind = 'model' | 'agent' | 'knowledge' | 'skill' | 'application'
export type ResourceAction = 'used' | 'created' | 'published' | 'indexed' | 'uploaded' | 'failed'
export interface WorkbenchResource {
  kind: ResourceKind
  id: string
  name: string
  occurred_at: string
  status: string
  action: ResourceAction
}
export interface WorkbenchAlert {
  id: string
  level: 'info' | 'warning' | 'error'
  title: string
  description: string
  kind: ResourceKind | null
  resource_id: string | null
  occurred_at: string | null
}
export interface DailyCalls {
  day: string
  calls: number
}
export interface WorkbenchMetrics {
  today_calls: number
  yesterday_calls: number
  successful_calls: number
  failed_calls: number
  cancelled_calls: number
  today_known_tokens: number
  today_unconfirmed_calls: number
  trend: DailyCalls[]
}
export interface WorkbenchCredits {
  enabled: boolean
  monthly_points: string
  unconfirmed_calls: number
}
export interface WorkbenchOverview {
  scope: 'mine' | 'platform'
  can_view_platform: boolean
  period_days: 7 | 30
  timezone: string
  observed_at: string
  metrics: WorkbenchMetrics | null
  credits: WorkbenchCredits | null
  recent: WorkbenchResource[]
  activities: WorkbenchResource[]
  alerts: WorkbenchAlert[]
}
function kind(value: unknown): ResourceKind {
  if (!['model', 'agent', 'knowledge', 'skill', 'application'].includes(string(value)))
    throw new Error('资源类型无效')
  return value as ResourceKind
}
function resource(value: unknown): WorkbenchResource {
  const d = object(value)
  const action = string(d.action)
  if (!['used', 'created', 'published', 'indexed', 'uploaded', 'failed'].includes(action))
    throw new Error('资源动态无效')
  return {
    kind: kind(d.kind),
    id: string(d.id),
    name: string(d.name),
    occurred_at: string(d.occurred_at),
    status: string(d.status),
    action: action as ResourceAction
  }
}
function metrics(value: unknown): WorkbenchMetrics {
  const d = object(value)
  return {
    today_calls: number(d.today_calls),
    yesterday_calls: number(d.yesterday_calls),
    successful_calls: number(d.successful_calls),
    failed_calls: number(d.failed_calls),
    cancelled_calls: number(d.cancelled_calls),
    today_known_tokens: number(d.today_known_tokens),
    today_unconfirmed_calls: number(d.today_unconfirmed_calls),
    trend: list((v): DailyCalls => {
      const row = object(v)
      return { day: string(row.day), calls: number(row.calls) }
    })(d.trend)
  }
}
export function parseOverview(value: unknown): WorkbenchOverview {
  const d = object(value)
  if (
    !['mine', 'platform'].includes(string(d.scope)) ||
    (d.period_days !== 7 && d.period_days !== 30)
  )
    throw new Error('工作台统计范围无效')
  const c = d.credits === null ? null : object(d.credits)
  if (c && !/^\d+(?:\.\d+)?$/.test(string(c.monthly_points))) throw new Error('积分数据无效')
  return {
    scope: d.scope as 'mine' | 'platform',
    can_view_platform: boolean(d.can_view_platform),
    period_days: d.period_days,
    timezone: string(d.timezone),
    observed_at: string(d.observed_at),
    metrics: d.metrics === null ? null : metrics(d.metrics),
    credits: c
      ? {
          enabled: boolean(c.enabled),
          monthly_points: string(c.monthly_points),
          unconfirmed_calls: number(c.unconfirmed_calls)
        }
      : null,
    recent: list(resource)(d.recent),
    activities: list(resource)(d.activities),
    alerts: list((v): WorkbenchAlert => {
      const a = object(v)
      if (!['info', 'warning', 'error'].includes(string(a.level))) throw new Error('提醒类型无效')
      return {
        id: string(a.id),
        level: a.level as WorkbenchAlert['level'],
        title: string(a.title),
        description: string(a.description),
        kind: a.kind === null ? null : kind(a.kind),
        resource_id: a.resource_id === null ? null : string(a.resource_id),
        occurred_at: a.occurred_at === null ? null : string(a.occurred_at)
      }
    })(d.alerts)
  }
}
export const getOverview = (signal: AbortSignal, scope: 'mine' | 'platform', days: 7 | 30) =>
  http.request(`/overview?scope=${scope}&days=${days}`, { signal, decode: parseOverview })
