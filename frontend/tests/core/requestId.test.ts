import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequestId } from '../../src/shared/requestId.ts'

void test('LAN HTTP fallback produces unique RFC 4122 UUIDs using getRandomValues', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto')!
  const random = globalThis.crypto.getRandomValues.bind(globalThis.crypto)
  Object.defineProperty(globalThis, 'crypto', {
    configurable: true,
    value: { getRandomValues: random }
  })
  try {
    const ids = Array.from({ length: 100 }, createRequestId)
    ids.forEach((id) =>
      assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    )
    assert.equal(new Set(ids).size, ids.length)
  } finally {
    Object.defineProperty(globalThis, 'crypto', original)
  }
})
