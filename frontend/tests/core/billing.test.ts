import test from 'node:test'
import assert from 'node:assert/strict'

void test('points preserve exact decimal precision and reject numeric API amounts', async () => {
  const rules = await import('../../src/modules/billing/presentation.ts').catch(() => null)
  assert.ok(rules, 'billing decimal rules must exist')
  assert.equal(
    rules.formatPoints('9007199254740993.000000000000000001'),
    '9,007,199,254,740,993.000000000000000001'
  )
  assert.equal(rules.formatPoints('-0.000000000000000001'), '-0.000000000000000001')
  assert.equal(rules.formatPoints('100.000000000000000000'), '100')
  assert.equal(rules.parsePoints('1.000000000000000001'), '1.000000000000000001')
  assert.throws(() => rules.parsePoints(1))
  assert.throws(() => rules.parsePoints('NaN'))
  assert.equal(rules.validPoints('0', true), false)
  assert.equal(rules.validPoints('0.000000000000000001', true), true)
  assert.equal(rules.validPoints('1e3'), false)
  assert.equal(rules.validPoints('1.0000000000000000001'), false)
})

void test('credit permissions never follow model administration and switching is superadmin only', async () => {
  const rules = await import('../../src/modules/billing/presentation.ts').catch(() => null)
  assert.ok(rules, 'billing permissions must exist')
  const modelAdmin = { is_admin: false, permissions: ['ai:models:write', 'ai:rates:write'] }
  assert.equal(rules.creditAccess(modelAdmin).read, false)
  assert.equal(rules.creditAccess(modelAdmin).write, false)
  const manager = rules.creditAccess({ is_admin: false, permissions: ['ai:credits:write'] })
  assert.deepEqual(manager, { read: true, write: true, switchMode: false })
  assert.deepEqual(rules.creditAccess({ is_admin: true }), {
    read: true,
    write: true,
    switchMode: true
  })
})

void test('zero rates require explicit free while relevant prices retain decimal strings', async () => {
  const rules = await import('../../src/modules/billing/presentation.ts').catch(() => null)
  assert.ok(rules, 'billing rate validation must exist')
  const rate = {
    billing_mode: 'token' as const,
    is_free: false,
    input_points: '0',
    output_points: '0',
    cache_points: '0',
    search_points: '0'
  }
  assert.ok(rules.rateError(rate))
  assert.equal(rules.rateError({ ...rate, is_free: true }), '')
  assert.equal(rules.rateError({ ...rate, input_points: '0.000000000000000001' }), '')
  assert.ok(rules.rateError({ ...rate, billing_mode: 'search_unit', input_points: '1' }))
  assert.ok(rules.rateError({ ...rate, is_free: true, output_points: '1' }))
})

void test('billing summaries reject floating JSON values at the API boundary', async () => {
  const api = await import('../../src/modules/billing/api/index.ts')
  const summary = {
    user_id: 'user-1',
    enabled: true,
    version: 0,
    allocated_points: '0',
    reclaimed_points: '0',
    used_points: '0',
    reserved_points: '0',
    available_points: '9007199254740993.000000000000000001'
  }
  assert.equal(api.parseCreditSummary(summary).available_points, summary.available_points)
  assert.throws(() => api.parseCreditSummary({ ...summary, available_points: 99.8 }))
})

void test('key monthly point limits are optional exact decimals with zero blocking calls', async () => {
  const rules = await import('../../src/modules/billing/presentation.ts')
  assert.equal(
    typeof rules.monthlyPointsLimit,
    'function',
    'monthly point limit validation must exist'
  )
  assert.equal(rules.monthlyPointsLimit(''), null)
  assert.equal(rules.monthlyPointsLimit(' 0 '), '0')
  assert.equal(rules.monthlyPointsLimit('10.000000000000000001'), '10.000000000000000001')
  assert.throws(() => rules.monthlyPointsLimit('-1'))
  assert.throws(() => rules.monthlyPointsLimit('NaN'))
})

void test('reconciliation preserves partial known output and requires missing token input', async () => {
  const rules = await import('../../src/modules/billing/reconciliation.ts').catch(() => null)
  assert.ok(rules, 'nullable reconciliation rules must exist')
  const row = {
    version: 2,
    billing_mode: 'token' as const,
    input_tokens: null,
    output_tokens: 200,
    cache_tokens: null,
    search_units: null
  }
  const draft = rules.reconciliationDraft(row)
  assert.equal(draft.input_tokens, null)
  assert.equal(draft.output_tokens, 200)
  assert.equal(draft.cache_tokens, null)
  draft.reason = '已确认实际输入'
  assert.throws(() => rules.reconciliationPayload(row, draft), /输入与输出/)
  draft.input_tokens = 1000
  draft.output_tokens = null
  const payload = rules.reconciliationPayload(row, draft)
  assert.equal(payload.input_tokens, 1000)
  assert.equal(
    payload.output_tokens,
    200,
    'confirmed output survives a missing or overwritten form value'
  )
  assert.equal(payload.cache_tokens, null)
  assert.equal(payload.search_units, null)
  assert.equal(payload.rejected_before_execution, false)
})

void test('search-only reconciliation requires search units while token metrics stay unknown', async () => {
  const rules = await import('../../src/modules/billing/reconciliation.ts').catch(() => null)
  assert.ok(rules, 'search-unit reconciliation rules must exist')
  const row = {
    version: 3,
    billing_mode: 'search_unit' as const,
    input_tokens: null,
    output_tokens: null,
    cache_tokens: null,
    search_units: null
  }
  const draft = rules.reconciliationDraft(row)
  draft.reason = '已确认检索用量'
  assert.throws(() => rules.reconciliationPayload(row, draft), /检索单元/)
  draft.search_units = 2
  const payload = rules.reconciliationPayload(row, draft)
  assert.equal(payload.search_units, 2)
  assert.equal(payload.input_tokens, null)
  assert.equal(payload.output_tokens, null)
  assert.equal(payload.cache_tokens, null)
  assert.equal(rules.formatUsageCount(null), '未提供')
  assert.equal(rules.formatUsageCount(0), '0')
})

void test('only a confirmed rejection produces a full zero usage payload', async () => {
  const rules = await import('../../src/modules/billing/reconciliation.ts').catch(() => null)
  assert.ok(rules, 'explicit rejection rules must exist')
  const row = {
    version: 1,
    billing_mode: 'token' as const,
    input_tokens: null,
    output_tokens: null,
    cache_tokens: null,
    search_units: null
  }
  const draft = rules.reconciliationDraft(row)
  draft.reason = '已确认执行前拒绝'
  assert.throws(() => rules.reconciliationPayload(row, draft))
  draft.rejected_before_execution = true
  const payload = rules.reconciliationPayload(row, draft)
  assert.deepEqual(
    [payload.input_tokens, payload.output_tokens, payload.cache_tokens, payload.search_units],
    [0, 0, 0, 0]
  )
  assert.throws(
    () => rules.reconciliationPayload({ ...row, output_tokens: 200 }, draft),
    /已有实际用量/
  )
})

void test('cache counts are compared only when input and cache are both known', async () => {
  const rules = await import('../../src/modules/billing/reconciliation.ts').catch(() => null)
  assert.ok(rules, 'partial usage validation must exist')
  const row = {
    version: 4,
    billing_mode: 'search_unit' as const,
    input_tokens: null,
    output_tokens: null,
    cache_tokens: 50,
    search_units: 2
  }
  const draft = rules.reconciliationDraft(row)
  draft.reason = '保留已知部分'
  assert.equal(rules.reconciliationPayload(row, draft).cache_tokens, 50)
  assert.throws(() => rules.reconciliationPayload({ ...row, input_tokens: 20 }, draft), /缓存/)
})
