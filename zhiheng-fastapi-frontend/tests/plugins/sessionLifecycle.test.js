import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { createApp } from 'vue'
import { createPinia, defineStore } from 'pinia'

const lifecyclePath = new URL('../../src/store/sessionLifecycle.js', import.meta.url)
assert.ok(existsSync(lifecyclePath), 'The platform must own a generic session lifecycle')
const { sessionLifecyclePlugin, clearSessionState } = await import(lifecyclePath)

function createScope() {
  const pinia = createPinia().use(sessionLifecyclePlugin)
  createApp({}).use(pinia)
  return pinia
}

const useSession = defineStore('test-session', { state: () => ({}) })
const useBusiness = defineStore('test-business', {
  sessionCleanup: 'resetForSession',
  state: () => ({ value: 'private', resets: 0 }),
  actions: {
    resetForSession() {
      this.value = ''
      this.resets += 1
    },
  },
})

const firstScope = createScope()
const secondScope = createScope()
const firstSession = useSession(firstScope)
const secondSession = useSession(secondScope)
assert.deepEqual(clearSessionState(firstSession), [], 'Clearing must not instantiate unopened modules')
assert.equal(Object.keys(firstScope.state.value).length, 1)
const firstBusiness = useBusiness(firstScope)
const secondBusiness = useBusiness(secondScope)
assert.equal(useBusiness(firstScope), firstBusiness, 'Repeated use must not duplicate cleanup registration')
clearSessionState(firstSession)
assert.equal(firstBusiness.value, '')
assert.equal(firstBusiness.resets, 1)
assert.equal(secondBusiness.value, 'private', 'Cleanup must use the caller scope, not the active Pinia')
clearSessionState(secondSession)
assert.equal(secondBusiness.resets, 1)

firstBusiness.$dispose()
clearSessionState(firstSession)
assert.equal(firstBusiness.resets, 1, 'Disposed stores must be unregistered')
const reopened = useBusiness(firstScope)
clearSessionState(firstSession)
assert.equal(reopened.resets, 2, 'A recreated store must participate in cleanup once')

const useBroken = defineStore('broken-cleanup', {
  sessionCleanup: 'resetForSession',
  actions: { resetForSession() { throw new Error('private failure details') } },
})
const useSecondBusiness = defineStore('second-business', {
  sessionCleanup: 'resetForSession',
  state: () => ({ value: 'another private value' }),
  actions: { resetForSession() { this.value = '' } },
})
useBroken(firstScope)
const healthy = useSecondBusiness(firstScope)
assert.deepEqual(clearSessionState(firstSession), ['broken-cleanup'])
assert.equal(healthy.value, '', 'One failure must not prevent other module cleanups')
assert.deepEqual(clearSessionState(firstSession), ['broken-cleanup'], 'Cleanup remains usable after failure')
console.log('ok session lifecycle: scope isolation, lazy stores, disposal, repeated cleanup and failure isolation')
