import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'

const helperPath = '../../src/utils/sessionLogout.js'
assert.ok(existsSync(new URL(helperPath, import.meta.url)), 'best-effort logout helper must exist')

const { bestEffortLogout } = await import(helperPath)

let requestCount = 0
await bestEffortLogout(async token => {
  requestCount += 1
  assert.equal(token, 'stale-token')
  throw new Error('backend unavailable')
}, 'stale-token', () => {})
assert.equal(requestCount, 1)

await bestEffortLogout(async () => {
  requestCount += 1
}, '', () => {})
assert.equal(requestCount, 1)

const pendingRequest = new Promise(() => {})
const completion = await Promise.race([
  bestEffortLogout(() => pendingRequest, 'pending-token', () => {}).then(() => 'completed'),
  new Promise(resolve => setTimeout(() => resolve('timed-out'), 100)),
])
assert.equal(completion, 'completed')

console.log('ok server logout failure does not block local logout')
