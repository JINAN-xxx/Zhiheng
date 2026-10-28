import { http } from '../../../shared/http.ts'
import { number, object, string } from '../../../shared/validation.ts'
export type HealthStatus = 'ok' | 'unavailable'
export interface Monitor {
  platform: {
    python_version: string | null
    system: string | null
    cpu_count: number | null
    rss_bytes: number | null
    disk_total_bytes: number | null
    disk_used_bytes: number | null
  }
  database: {
    status: HealthStatus
    version: string | null
    size_bytes: number | null
    connections: number | null
  }
  redis: {
    status: HealthStatus
    used_memory_bytes: number | null
    connected_clients: number | null
    uptime_seconds: number | null
    db_keys: number | null
  }
  worker: { status: HealthStatus; heartbeat_age_seconds: number | null }
  schema_version: string | null
}
const nullableNumber = (value: unknown): number | null => (value === null ? null : number(value))
const nullableString = (value: unknown): string | null => (value === null ? null : string(value))
const status = (value: unknown): HealthStatus => {
  if (value !== 'ok' && value !== 'unavailable') throw new Error('服务器返回的健康状态不正确')
  return value
}
export function decodeMonitor(value: unknown): Monitor {
  const r = object(value),
    p = object(r.platform),
    d = object(r.database),
    redis = object(r.redis),
    w = object(r.worker)
  return {
    platform: {
      python_version: nullableString(p.python_version),
      system: nullableString(p.system),
      cpu_count: nullableNumber(p.cpu_count),
      rss_bytes: nullableNumber(p.rss_bytes),
      disk_total_bytes: nullableNumber(p.disk_total_bytes),
      disk_used_bytes: nullableNumber(p.disk_used_bytes)
    },
    database: {
      status: status(d.status),
      version: nullableString(d.version),
      size_bytes: nullableNumber(d.size_bytes),
      connections: nullableNumber(d.connections)
    },
    redis: {
      status: status(redis.status),
      used_memory_bytes: nullableNumber(redis.used_memory_bytes),
      connected_clients: nullableNumber(redis.connected_clients),
      uptime_seconds: nullableNumber(redis.uptime_seconds),
      db_keys: nullableNumber(redis.db_keys)
    },
    worker: {
      status: status(w.status),
      heartbeat_age_seconds: nullableNumber(w.heartbeat_age_seconds)
    },
    schema_version: nullableString(r.schema_version)
  }
}
export const getMonitor = (signal?: AbortSignal) =>
  http.request('/admin/monitor', { ...(signal ? { signal } : {}), decode: decodeMonitor })
