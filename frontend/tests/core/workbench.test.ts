import assert from 'node:assert/strict'
import test from 'node:test'
import { parseOverview } from '../../src/modules/overview/api/index.ts'
import {
  successRate,
  callChange,
  pointText,
  resourcePath,
  statusLabel
} from '../../src/modules/overview/presentation.ts'

const empty = {
  scope: 'mine',
  can_view_platform: false,
  period_days: 7,
  timezone: 'Asia/Shanghai',
  observed_at: '2026-11-16T03:00:00Z',
  metrics: null,
  credits: null,
  recent: [],
  activities: [],
  alerts: []
}
void test('workbench preserves unavailable metrics and exact decimal points', () => {
  const value = parseOverview({
    ...empty,
    credits: {
      enabled: true,
      monthly_points: '900719925474099312345.000000000000000123',
      unconfirmed_calls: 2
    }
  })
  assert.equal(value.metrics, null)
  assert.equal(value.credits?.monthly_points, '900719925474099312345.000000000000000123')
  assert.equal(value.credits?.unconfirmed_calls, 2)
  assert.throws(() =>
    parseOverview({
      ...empty,
      credits: { enabled: true, monthly_points: 1.25, unconfirmed_calls: 0 }
    })
  )
})
void test('empty and unconfirmed runs do not fabricate a success percentage or growth rate', () => {
  assert.equal(successRate(0, 0), '—')
  assert.equal(successRate(3, 1), '75.0%')
  assert.equal(callChange(12, 0), '昨日无调用')
  assert.equal(callChange(0, 0), '今日暂无调用')
  assert.equal(callChange(2, 4), '较昨日 -50.0%')
})
void test('point cards keep large integers and signal tiny nonzero consumption', () => {
  assert.equal(pointText('900719925474099312345.12'), '900,719,925,474,099,312,345.12')
  assert.equal(pointText('0.000000000000000123'), '<0.000001')
  assert.equal(pointText('12.123456'), '12.123456')
  assert.equal(pointText('0.0000'), '0')
})
void test('resource destinations use known routes and availability labels do not claim health', () => {
  assert.equal(resourcePath('agent', 'a/b'), '/agents/a%2Fb')
  assert.equal(resourcePath('model', 'private'), '/models')
  assert.equal(statusLabel('available'), '已启用')
  assert.throws(() => parseOverview({ ...empty, scope: 'all-users' }))
  assert.throws(() => parseOverview({ ...empty, period_days: 100 }))
})
