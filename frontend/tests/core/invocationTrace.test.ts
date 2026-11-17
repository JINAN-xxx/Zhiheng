import assert from 'node:assert/strict'
import test from 'node:test'
import { parseInvocationTrace } from '../../src/modules/billing/api/trace.ts'
import { acceptedAge, reconciliationTarget } from '../../src/modules/billing/trace.ts'
import { reconciliationPayload } from '../../src/modules/billing/reconciliation.ts'

const fixture = () => ({
  billing: {
    invocation: {
      id: 'invocation',
      run_id: 'run',
      user_id: 'owner',
      model_id: 'model',
      operation: 'chat',
      source: 'chat',
      status: 'unknown',
      version: 7,
      points_enforced: true,
      input_tokens: 100,
      output_tokens: null,
      cache_tokens: 0,
      reasoning_tokens: 5,
      search_units: null,
      reserved_tokens: 900,
      charged_tokens: null,
      currency: 'USD',
      reserved_cost: '0.123456789012',
      billable_cost: null,
      rate_card_id: 'currency-rate',
      rate_version: 2,
      application_id: null,
      key_id: null,
      created_at: '2026-11-01T00:00:00Z'
    },
    reservation: {
      run_id: 'run',
      user_id: 'owner',
      model_id: 'model',
      source: 'chat',
      status: 'unknown',
      version: 3,
      billing_mode: 'token',
      input_tokens: null,
      output_tokens: 20,
      cache_tokens: 0,
      reasoning_tokens: null,
      search_units: null,
      reserved_points: '0.000000000000000001',
      charged_points: null,
      rate_version: 4,
      created_at: '2026-11-01T00:00:00Z'
    },
    ledger: [],
    ledger_page: 1,
    ledger_page_size: 20,
    ledger_total: 0
  },
  execution: null,
  related: [],
  related_has_more: false,
  observed_at: '2026-11-04T00:00:00Z'
})
void test('trace keeps exact currency, points and missing execution as separate facts', () => {
  const trace = parseInvocationTrace(fixture())
  assert.equal(trace.billing.invocation.reserved_cost, '0.123456789012')
  assert.equal(trace.billing.reservation!.reserved_points, '0.000000000000000001')
  assert.equal(trace.billing.invocation.billable_cost, null)
  assert.equal(trace.execution, null)
  const bad = fixture()
  Object.assign(bad.billing.invocation, { reserved_cost: 0.123 })
  assert.throws(() => parseInvocationTrace(bad), /精确十进制字符串/)
})
void test('credit reconciliation freezes confirmed values from both original meters', () => {
  const target = reconciliationTarget(parseInvocationTrace(fixture()))!
  assert.equal(target.version, 3)
  assert.equal(target.input_tokens, 100)
  assert.equal(target.output_tokens, 20)
  assert.equal(target.cache_tokens, 0)
  assert.equal(target.reasoning_tokens, 5)
  const payload = reconciliationPayload(target, {
    input_tokens: 0,
    output_tokens: 0,
    cache_tokens: 1,
    search_units: null,
    reason: 'verified',
    rejected_before_execution: false
  })
  assert.equal(payload.input_tokens, 100)
  assert.equal(payload.output_tokens, 20)
  assert.equal(payload.cache_tokens, 0)
  assert.throws(
    () => reconciliationPayload(target, { ...payload, rejected_before_execution: true }),
    /已有实际用量/
  )
})
void test('settled points with unknown token diagnostics have no second settlement entry', () => {
  const trace = parseInvocationTrace(fixture())
  trace.billing.reservation!.status = 'settled'
  assert.equal(reconciliationTarget(trace), null)
})
void test('disagreeing confirmed facts block an editor rather than choosing an arbitrary meter', () => {
  const trace = parseInvocationTrace(fixture())
  trace.billing.reservation!.input_tokens = 99
  assert.equal(reconciliationTarget(trace), null)
})
void test('token-only reconciliation uses invocation version and preserves confirmed zero cache', () => {
  const trace = parseInvocationTrace(fixture())
  trace.billing.invocation.points_enforced = false
  trace.billing.reservation = null
  const target = reconciliationTarget(trace)!
  assert.equal(target.kind, 'tokens')
  assert.equal(target.version, 7)
  assert.equal(target.cache_tokens, 0)
  assert.equal(target.output_tokens, null)
})
void test('age describes acceptance instead of inventing unknown transition timestamps', () => {
  assert.equal(acceptedAge('2026-11-01T00:00:00Z', '2026-11-04T00:00:00Z'), '3 天')
  assert.equal(acceptedAge('2026-11-01T00:00:00Z', '2026-11-01T00:00:30Z'), '不足 1 分钟')
  assert.equal(acceptedAge('2026-11-02T00:00:00Z', '2026-11-01T00:00:00Z'), '时间待确认')
})
