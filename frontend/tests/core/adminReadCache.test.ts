import assert from 'node:assert/strict'
import test from 'node:test'
import { reactive } from 'vue'
import { createAdminReadCache } from '../../src/modules/admin/readCache.ts'

const snapshot = () => ({
  rows: [{ name: 'Alice' }],
  total: 1,
  search: 'Alice',
  page: 2,
  filters: { kind: 'login', outcome: 'failed' },
  hasData: true,
  capturedAt: Date.now()
})

void test('cached read state restores rows and query without exposing mutable references', () => {
  const cache = createAdminReadCache('owner')
  const data = snapshot()
  cache.setCollection('users', data, cache.stamp())
  data.rows[0]!.name = 'changed'
  const restored = cache.getCollection<{ name: string }>('users')!
  assert.equal(restored.rows[0]?.name, 'Alice')
  assert.equal(restored.page, 2)
  assert.equal(restored.filters.outcome, 'failed')
  restored.rows[0].name = 'another mutation'
  assert.equal(cache.getCollection<{ name: string }>('users')?.rows[0]?.name, 'Alice')
})

void test('writes invalidate freshness and late old reads cannot make it fresh again', () => {
  const cache = createAdminReadCache('owner')
  const origin = cache.stamp()
  cache.setCollection('users', snapshot(), origin)
  cache.invalidate()
  cache.setCollection('users', snapshot(), origin)
  assert.equal(cache.isFresh(cache.getCollection('users')), false)
  const current = cache.stamp()
  cache.setCollection('users', snapshot(), current)
  assert.equal(cache.isFresh(cache.getCollection('users')), true)
})

void test('identity and authority changes drop cached data and reject late responses', () => {
  const cache = createAdminReadCache('old-account:permissions')
  const old = cache.stamp()
  cache.setCollection('users', snapshot(), old)
  cache.setResource('permissions', ['private-code'], old)
  cache.reset('new-account:permissions')
  cache.setCollection('users', snapshot(), old)
  cache.setResource('permissions', ['old-code'], old)
  assert.equal(cache.getCollection('users'), undefined)
  assert.equal(cache.getResource('permissions'), undefined)
})

void test('reactive DTO resources are copied and expiry never extends just by reading', () => {
  const cache = createAdminReadCache('owner', 15000)
  const value = reactive({ names: ['Alice'], setting: { enabled: false } })
  cache.setResource('resource', value, cache.stamp(), Date.now() - 16000)
  assert.equal(cache.isFresh(cache.getResource('resource')), false)
  value.names.push('Bob')
  assert.deepEqual(cache.getResource<typeof value>('resource')?.value.names, ['Alice'])
})

void test('leaving the console disposes its data and ignores delayed callbacks', () => {
  const cache = createAdminReadCache('owner')
  const stamp = cache.stamp()
  cache.dispose()
  cache.setCollection('users', snapshot(), stamp)
  cache.setResource('resource', ['private'], stamp)
  assert.equal(cache.getCollection('users'), undefined)
  assert.equal(cache.getResource('resource'), undefined)
})
