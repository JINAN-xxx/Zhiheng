import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mergeConfig } from 'axios'

const providerUrl = new URL('../../src/utils/transportCryptoProvider.js', import.meta.url)
assert.ok(existsSync(providerUrl), 'HTTP transport crypto provider must exist')
const { getTransportCrypto } = await import(providerUrl)

const encoder = new TextEncoder()
const plaintext = encoder.encode(JSON.stringify({ message: '验证码：你好 🌏', answer: 42 }))
const aad = encoder.encode(JSON.stringify({ method: 'GET', path: '/captchaImage' }))
const aesAlgorithm = { name: 'AES-GCM', length: 256 }
const aesUsages = ['encrypt', 'decrypt']
const rsaAlgorithm = { name: 'RSA-OAEP', hash: 'SHA-256' }

function createHttpCrypto() {
  const runtime = {
    requests: [],
    getRandomValues(bytes) {
      assert.equal(this, runtime, 'getRandomValues must retain its Crypto receiver')
      this.requests.push(bytes.byteLength)
      return webcrypto.getRandomValues(bytes)
    },
  }
  return runtime
}

function offsetView(value) {
  const bytes = new Uint8Array(value.buffer ?? value, value.byteOffset ?? 0, value.byteLength)
  const padded = new Uint8Array(bytes.length + 10).fill(0xee)
  padded.set(bytes, 4)
  return new Uint8Array(padded.buffer, 4, bytes.length)
}

async function check(name, run) {
  await run()
  console.log(`ok transport crypto ${name}`)
}

await check('prefers native subtle and supplies secure UUIDs when randomUUID is absent', async () => {
  const runtime = createHttpCrypto()
  runtime.subtle = webcrypto.subtle
  const provider = await getTransportCrypto(runtime)
  assert.equal(provider.subtle, webcrypto.subtle)
  assert.equal(provider, await getTransportCrypto(runtime))
  const bytes = new Uint8Array(12)
  assert.equal(provider.getRandomValues(bytes), bytes)
  assert.match(provider.randomUUID(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  assert.deepEqual(runtime.requests, [12, 16])
  assert.equal(runtime.randomUUID, undefined, 'provider must not patch the browser Crypto object')
})

await check('rejects runtimes without secure randomness', async () => {
  for (const runtime of [null, {}, { subtle: webcrypto.subtle }, { getRandomValues: true }]) {
    await assert.rejects(getTransportCrypto(runtime), /安全随机|secure random/i)
  }
})

const runtime = createHttpCrypto()
const provider = await getTransportCrypto(runtime)
const key = await provider.subtle.generateKey(aesAlgorithm, true, aesUsages)
const rawKey = await provider.subtle.exportKey('raw', key)
const nativeKey = await webcrypto.subtle.importKey('raw', rawKey, aesAlgorithm, false, aesUsages)

await check('caches fallback per runtime and generates AES keys with secure randomness', async () => {
  assert.equal(provider, await getTransportCrypto(runtime))
  assert.notEqual(provider.subtle, webcrypto.subtle)
  assert.equal(rawKey.byteLength, 32)
  assert.deepEqual(runtime.requests, [32])
  assert.match(provider.randomUUID(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  assert.deepEqual(runtime.requests, [32, 16])
  assert.equal(runtime.subtle, undefined)
})

await check('fallback AES encrypts Unicode with AAD for native decryption', async () => {
  const iv = webcrypto.getRandomValues(new Uint8Array(12))
  const algorithm = { name: 'AES-GCM', iv, additionalData: aad }
  const ciphertext = await provider.subtle.encrypt(algorithm, key, plaintext)
  assert.ok(ciphertext instanceof ArrayBuffer)
  assert.equal(ciphertext.byteLength, plaintext.length + 16, 'GCM ciphertext must include the 128-bit tag')
  assert.deepEqual(new Uint8Array(await webcrypto.subtle.decrypt(algorithm, nativeKey, ciphertext)), plaintext)
})

await check('fallback AES decrypts native ciphertext and honors BufferSource offsets', async () => {
  const iv = webcrypto.getRandomValues(new Uint8Array(12))
  const algorithm = { name: 'AES-GCM', iv, additionalData: aad, tagLength: 128 }
  const ciphertext = await webcrypto.subtle.encrypt(algorithm, nativeKey, plaintext)
  const offsetAlgorithm = { ...algorithm, iv: offsetView(iv), additionalData: offsetView(aad) }
  const offsetCiphertext = offsetView(ciphertext)
  assert.deepEqual(new Uint8Array(await provider.subtle.decrypt(offsetAlgorithm, key, offsetCiphertext)), plaintext)
  assert.deepEqual(
    new Uint8Array(await provider.subtle.encrypt(offsetAlgorithm, key, offsetView(plaintext))),
    new Uint8Array(ciphertext),
    'same key, IV, plaintext and AAD must produce the same GCM ciphertext and tag',
  )
  const dataView = new DataView(offsetCiphertext.buffer, offsetCiphertext.byteOffset, offsetCiphertext.byteLength)
  assert.deepEqual(new Uint8Array(await provider.subtle.decrypt(offsetAlgorithm, key, dataView)), plaintext)
})

await check('rejects tampered ciphertext, authentication tags and AAD', async () => {
  const iv = webcrypto.getRandomValues(new Uint8Array(12))
  const algorithm = { name: 'AES-GCM', iv, additionalData: aad }
  const ciphertext = new Uint8Array(await webcrypto.subtle.encrypt(algorithm, nativeKey, plaintext))
  for (const index of [0, ciphertext.length - 1]) {
    const changed = ciphertext.slice()
    changed[index] ^= 1
    await assert.rejects(provider.subtle.decrypt(algorithm, key, changed), /authenticat|decrypt|解密|认证/i)
  }
  await assert.rejects(provider.subtle.decrypt({ ...algorithm, additionalData: encoder.encode('wrong AAD') }, key, ciphertext))
  await assert.rejects(provider.subtle.decrypt(algorithm, key, ciphertext.subarray(0, 15)))
  await assert.rejects(provider.subtle.decrypt({ ...algorithm, iv: iv.subarray(0, 11) }, key, ciphertext))
  await assert.rejects(provider.subtle.decrypt({ ...algorithm, tagLength: 96 }, key, ciphertext))
})

await check('handles empty plaintext and omitted AAD with the standard GCM tag', async () => {
  const algorithm = { name: 'AES-GCM', iv: webcrypto.getRandomValues(new Uint8Array(12)) }
  const ciphertext = await provider.subtle.encrypt(algorithm, key, new Uint8Array())
  assert.equal(ciphertext.byteLength, 16)
  assert.equal((await webcrypto.subtle.decrypt(algorithm, nativeKey, ciphertext)).byteLength, 0)
  assert.equal((await provider.subtle.decrypt(algorithm, key, ciphertext)).byteLength, 0)
})

await check('keeps AES material private and returns independent raw exports', async () => {
  assert.deepEqual(Reflect.ownKeys(key).sort(), ['algorithm', 'extractable', 'type', 'usages'])
  assert.equal(key.type, 'secret')
  assert.deepEqual(key.algorithm, aesAlgorithm)
  assert.deepEqual(key.usages, aesUsages)
  assert.equal(key.extractable, true)
  assert.ok(Object.isFrozen(key))
  assert.ok(Object.isFrozen(key.algorithm))
  assert.ok(Object.isFrozen(key.usages))
  const serialized = JSON.stringify({ __transportCryptoContext: { aesKey: key } })
  assert.ok(!serialized.includes(Buffer.from(rawKey).toString('base64')))
  assert.ok(!serialized.includes(Array.from(new Uint8Array(rawKey)).join(',')))
  const exported = new Uint8Array(await provider.subtle.exportKey('raw', key))
  exported.fill(0)
  assert.deepEqual(new Uint8Array(await provider.subtle.exportKey('raw', key)), new Uint8Array(rawKey))
})

await check('preserves opaque key identity through Axios config merging', async () => {
  const config = mergeConfig({}, { __transportCryptoContext: { aesKey: key } })
  assert.equal(config.__transportCryptoContext.aesKey, key, 'Axios must preserve the private key handle')
  assert.deepEqual(
    new Uint8Array(await provider.subtle.exportKey('raw', config.__transportCryptoContext.aesKey)),
    new Uint8Array(rawKey),
  )
})

await check('rejects invalid algorithms, usages and keys from another provider', async () => {
  const algorithm = { name: 'AES-GCM', iv: new Uint8Array(12) }
  const otherProvider = await getTransportCrypto(createHttpCrypto())
  assert.notEqual(provider, otherProvider)
  const invalidOperations = [
    () => provider.subtle.generateKey({ name: 'AES-GCM', length: 128 }, true, aesUsages),
    () => provider.subtle.generateKey(aesAlgorithm, true, ['sign']),
    () => provider.subtle.generateKey(aesAlgorithm, true, []),
    () => provider.subtle.encrypt({ ...algorithm, name: 'AES-CBC' }, key, plaintext),
    () => provider.subtle.encrypt({ ...algorithm, additionalData: 'text is not BufferSource' }, key, plaintext),
    () => provider.subtle.encrypt(algorithm, { ...key }, plaintext),
    () => otherProvider.subtle.encrypt(algorithm, key, plaintext),
    () => provider.subtle.encrypt(algorithm, nativeKey, plaintext),
    () => provider.subtle.exportKey('jwk', key),
    () => provider.subtle.exportKey('raw', {}),
  ]
  for (const operation of invalidOperations) {
    await assert.rejects(operation)
  }
})

await check('RSA-OAEP wraps AES bytes for native SHA-256/MGF1-SHA256 decryption', async () => {
  const pair = await webcrypto.subtle.generateKey(
    { ...rsaAlgorithm, modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]) },
    true,
    ['encrypt', 'decrypt'],
  )
  const spki = await webcrypto.subtle.exportKey('spki', pair.publicKey)
  const publicKey = await provider.subtle.importKey('spki', offsetView(spki), rsaAlgorithm, false, ['encrypt'])
  const before = runtime.requests.length
  const encrypted = await provider.subtle.encrypt({ name: 'RSA-OAEP' }, publicKey, offsetView(rawKey))
  assert.deepEqual(runtime.requests.slice(before), [32], 'OAEP seed must come from secure getRandomValues')
  assert.deepEqual(
    new Uint8Array(await webcrypto.subtle.decrypt({ name: 'RSA-OAEP' }, pair.privateKey, encrypted)),
    new Uint8Array(rawKey),
  )
  const another = await provider.subtle.encrypt({ name: 'RSA-OAEP' }, publicKey, rawKey)
  assert.notDeepEqual(new Uint8Array(encrypted), new Uint8Array(another), 'OAEP must use a fresh random seed')
  await assert.rejects(provider.subtle.importKey('spki', spki, { ...rsaAlgorithm, hash: 'SHA-1' }, false, ['encrypt']))
  await assert.rejects(provider.subtle.importKey('spki', spki, rsaAlgorithm, false, ['decrypt']))
  await assert.rejects(provider.subtle.importKey('spki', new Uint8Array([1, 2, 3]), rsaAlgorithm, false, ['encrypt']))
  await assert.rejects(provider.subtle.importKey('raw', spki, rsaAlgorithm, false, ['encrypt']))
  await assert.rejects(provider.subtle.encrypt({ name: 'RSA-OAEP', label: encoder.encode('unsupported') }, publicKey, rawKey))
  await assert.rejects(provider.subtle.encrypt({ name: 'RSA-OAEP' }, key, rawKey))
  await assert.rejects(provider.subtle.exportKey('raw', publicKey))
})

await check('fails closed when the secure random source throws', async () => {
  const failure = new Error('secure random source unavailable')
  const broken = await getTransportCrypto({ getRandomValues() { throw failure } })
  await assert.rejects(broken.subtle.generateKey(aesAlgorithm, true, aesUsages), error => error === failure)
  assert.throws(() => broken.randomUUID(), error => error === failure)
})
