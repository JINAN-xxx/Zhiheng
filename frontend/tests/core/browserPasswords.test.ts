import assert from 'node:assert/strict'
import { test } from 'node:test'
import { savePasswordInBrowser } from '../../src/modules/identity/browserPasswords.ts'

class BrowserPassword implements Credential {
  readonly type = 'password'
  readonly id: string
  readonly password: string
  constructor(input: { id: string; password: string }) {
    this.id = input.id
    this.password = input.password
  }
}

function browser() {
  const frame = {}
  const saved: Credential[] = []
  return {
    isSecureContext: true,
    top: frame,
    self: frame,
    PasswordCredential: BrowserPassword,
    navigator: {
      credentials: {
        store(credential: Credential): Promise<unknown> {
          saved.push(credential)
          return Promise.resolve(undefined)
        }
      }
    },
    saved
  }
}

void test('a supported browser receives the verified username and password', async () => {
  const target = browser()
  assert.equal(await savePasswordInBrowser(target, 'admin', 'verified-test-password'), 'requested')
  assert.equal(target.saved.length, 1)
  const credential = target.saved[0]
  assert.ok(credential instanceof BrowserPassword)
  assert.equal(credential.id, 'admin')
  assert.equal(credential.password, 'verified-test-password')
})

void test('an insecure context never receives password credentials', async () => {
  const target = browser()
  target.isSecureContext = false
  assert.equal(await savePasswordInBrowser(target, 'admin', 'secret'), 'unsupported')
  assert.equal(target.saved.length, 0)
})

void test('an embedded page never reports a password store request', async () => {
  const target = browser()
  target.top = {}
  assert.equal(await savePasswordInBrowser(target, 'admin', 'secret'), 'unsupported')
  assert.equal(target.saved.length, 0)
})

void test('a browser without PasswordCredential falls back without throwing', async () => {
  const target = browser()
  const unsupported = {
    isSecureContext: target.isSecureContext,
    top: target.top,
    self: target.self,
    navigator: target.navigator
  }
  assert.equal(await savePasswordInBrowser(unsupported, 'admin', 'secret'), 'unsupported')
  assert.equal(target.saved.length, 0)
})

void test('a browser without credential storage falls back without throwing', async () => {
  const target = { ...browser(), navigator: {} }
  assert.equal(await savePasswordInBrowser(target, 'admin', 'secret'), 'unsupported')
})

void test('a constructor rejection is a password save failure', async () => {
  class RejectedPassword extends BrowserPassword {
    constructor(input: { id: string; password: string }) {
      super(input)
      throw new Error('Credentials unavailable')
    }
  }
  const target = { ...browser(), PasswordCredential: RejectedPassword }
  assert.equal(await savePasswordInBrowser(target, 'admin', 'secret'), 'failed')
  assert.equal(target.saved.length, 0)
})

void test('a rejected browser store is handled without changing authentication', async () => {
  const target = browser()
  target.navigator.credentials.store = () => Promise.reject(new Error('Storage rejected'))
  assert.equal(await savePasswordInBrowser(target, 'admin', 'secret'), 'failed')
})
