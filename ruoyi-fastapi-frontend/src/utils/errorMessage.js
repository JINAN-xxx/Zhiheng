const MESSAGE_KEYS = Object.freeze(['message', 'msg', 'detail', 'error', 'reason'])
const MAX_DEPTH = 4

function extractMessage(value, seen, depth) {
  if (typeof value === 'string') return value.trim() || null
  if (!value || typeof value !== 'object' || depth >= MAX_DEPTH || seen.has(value)) return null
  seen.add(value)
  for (const key of MESSAGE_KEYS) {
    const message = extractMessage(value[key], seen, depth + 1)
    if (message) return message
  }
  return null
}

export function normalizeErrorMessage(value, fallback = '系统接口异常') {
  return extractMessage(value, new WeakSet(), 0) || fallback
}
