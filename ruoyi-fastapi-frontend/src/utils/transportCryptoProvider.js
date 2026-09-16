import { v4 as uuidv4 } from 'uuid'

const providers = new WeakMap()

function toBytes(value) {
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value)
  }
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
  }
  throw new TypeError('Transport crypto requires a BufferSource')
}

function toBinary(value) {
  const bytes = toBytes(value)
  const chunks = []
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)))
  }
  return chunks.join('')
}

function toArrayBuffer(binary) {
  return Uint8Array.from(binary, character => character.charCodeAt(0)).buffer
}

function validateUsages(usages, allowed) {
  if (!Array.isArray(usages) || !usages.length || usages.some(usage => !allowed.includes(usage))) {
    throw new TypeError('Unsupported transport crypto key usages')
  }
}

// A class instance preserves key identity when Axios merges request configs.
class TransportKey {
  constructor(type, algorithm, extractable, usages) {
    this.type = type
    this.algorithm = Object.freeze(algorithm)
    this.extractable = extractable
    this.usages = Object.freeze([...usages])
    Object.freeze(this)
  }
}

/** Only the RSA-OAEP-SHA256 / AES-256-GCM operations used by transportCrypto.js. */
function createFallbackSubtle(forge, getRandomValues) {
  // Key handles may reach Axios error/config logging; secret material must not.
  const keys = new WeakMap()

  function createKey(type, algorithm, extractable, usages, material) {
    const key = new TransportKey(type, algorithm, extractable, usages)
    keys.set(key, material)
    return key
  }

  function getKey(key, algorithm, usage) {
    if (!keys.has(key) || key.algorithm.name !== algorithm || (usage && !key.usages.includes(usage))) {
      throw new TypeError('Invalid transport crypto key or usage')
    }
    return keys.get(key)
  }

  function gcmOptions(algorithm) {
    if (algorithm?.name !== 'AES-GCM' || (algorithm.tagLength !== undefined && algorithm.tagLength !== 128)) {
      throw new TypeError('Transport crypto requires AES-GCM with a 128-bit tag')
    }
    const iv = toBytes(algorithm.iv)
    if (iv.length !== 12) {
      throw new TypeError('Transport crypto requires a 12-byte AES-GCM IV')
    }
    return {
      iv: toBinary(iv),
      additionalData: algorithm.additionalData === undefined ? '' : toBinary(algorithm.additionalData),
      tagLength: 128,
    }
  }

  return Object.freeze({
    async importKey(format, data, algorithm, extractable, usages) {
      const hash = typeof algorithm?.hash === 'string' ? algorithm.hash : algorithm?.hash?.name
      if (format !== 'spki' || algorithm?.name !== 'RSA-OAEP' || hash !== 'SHA-256' || extractable !== false) {
        throw new TypeError('Transport crypto requires a non-extractable RSA-OAEP-SHA256 SPKI public key')
      }
      validateUsages(usages, ['encrypt'])
      const asn1 = forge.asn1.fromDer(toBinary(data), { strict: true, parseAllBytes: true })
      if (!forge.asn1.validate(asn1, forge.pki.rsa.publicKeyValidator, {}, [])) {
        throw new TypeError('Invalid transport crypto SPKI public key')
      }
      const publicKey = forge.pki.publicKeyFromAsn1(asn1)
      return createKey('public', { name: 'RSA-OAEP', hash: Object.freeze({ name: 'SHA-256' }) }, false, usages, publicKey)
    },

    async generateKey(algorithm, extractable, usages) {
      if (algorithm?.name !== 'AES-GCM' || algorithm.length !== 256 || typeof extractable !== 'boolean') {
        throw new TypeError('Transport crypto requires a 256-bit AES-GCM key')
      }
      validateUsages(usages, ['encrypt', 'decrypt'])
      const material = toBinary(getRandomValues(new Uint8Array(32)))
      return createKey('secret', { name: 'AES-GCM', length: 256 }, extractable, usages, material)
    },

    async exportKey(format, key) {
      const material = getKey(key, 'AES-GCM')
      if (format !== 'raw' || !key.extractable) {
        throw new TypeError('Transport crypto only exports extractable raw AES keys')
      }
      return toArrayBuffer(material)
    },

    async encrypt(algorithm, key, data) {
      if (algorithm?.name === 'RSA-OAEP') {
        if (algorithm.label !== undefined) {
          throw new TypeError('Transport crypto does not support RSA-OAEP labels')
        }
        const publicKey = getKey(key, 'RSA-OAEP', 'encrypt')
        return toArrayBuffer(publicKey.encrypt(toBinary(data), 'RSA-OAEP', {
          md: forge.md.sha256.create(),
          mgf1: { md: forge.md.sha256.create() },
          // Supplying the seed prevents forge from using its own random fallback.
          seed: toBinary(getRandomValues(new Uint8Array(32))),
        }))
      }
      const options = gcmOptions(algorithm)
      const cipher = forge.cipher.createCipher('AES-GCM', getKey(key, 'AES-GCM', 'encrypt'))
      cipher.start(options)
      cipher.update(forge.util.createBuffer(toBinary(data)))
      if (!cipher.finish()) {
        throw new Error('Transport AES-GCM encryption failed')
      }
      // Web Crypto appends the authentication tag to ciphertext.
      return toArrayBuffer(cipher.output.getBytes() + cipher.mode.tag.getBytes())
    },

    async decrypt(algorithm, key, data) {
      const options = gcmOptions(algorithm)
      const material = getKey(key, 'AES-GCM', 'decrypt')
      const ciphertext = toBytes(data)
      if (ciphertext.length < 16) {
        throw new TypeError('Invalid transport AES-GCM ciphertext')
      }
      const decipher = forge.cipher.createDecipher('AES-GCM', material)
      decipher.start({ ...options, tag: forge.util.createBuffer(toBinary(ciphertext.subarray(-16))) })
      decipher.update(forge.util.createBuffer(toBinary(ciphertext.subarray(0, -16))))
      if (!decipher.finish()) {
        throw new Error('Transport AES-GCM authentication failed')
      }
      return toArrayBuffer(decipher.output.getBytes())
    },
  })
}

/** Prefer native Web Crypto; HTTP origins use forge with native secure randomness. */
export async function getTransportCrypto(runtimeCrypto = globalThis.crypto) {
  if (!runtimeCrypto || typeof runtimeCrypto.getRandomValues !== 'function') {
    throw new Error('当前浏览器不支持安全随机数生成')
  }
  if (!providers.has(runtimeCrypto)) {
    const getRandomValues = runtimeCrypto.getRandomValues.bind(runtimeCrypto)
    const createProvider = subtle => Object.freeze({
      subtle,
      getRandomValues,
      randomUUID: () => uuidv4({ random: getRandomValues(new Uint8Array(16)) }),
    })
    const pending = runtimeCrypto.subtle
      ? Promise.resolve(createProvider(runtimeCrypto.subtle))
      : import('node-forge').then(module => createProvider(createFallbackSubtle(module.default, getRandomValues)))
    providers.set(runtimeCrypto, pending)
    pending.catch(() => providers.delete(runtimeCrypto))
  }
  return providers.get(runtimeCrypto)
}
