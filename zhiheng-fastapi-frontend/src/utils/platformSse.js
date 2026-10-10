// Shared parser for the platform JSON SSE protocol used by Chat and Work.
const decoder = () => new TextDecoder('utf-8', { fatal: false })

async function* asChunks(source) {
  if (source?.getReader) {
    const reader = source.getReader()
    let finished = false
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          finished = true
          return
        }
        yield value
      }
    } finally {
      try {
        if (!finished) await reader.cancel?.()
      } catch {
        // Cleanup is best effort: keep the original parse/read/abort outcome.
      } finally {
        reader.releaseLock?.()
      }
    }
    return
  }
  for await (const chunk of source) yield chunk
}

function parseEventBlock(block) {
  const lines = block.split(/\n/)
  let event = 'message'
  const data = []
  for (const line of lines) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
    if (field === 'event') event = value || 'message'
    if (field === 'data') data.push(value)
  }
  if (!data.length) return null
  const raw = data.join('\n')
  if (raw === '[DONE]') return { event: 'done', data: { status: 'completed' } }
  try {
    const parsed = JSON.parse(raw)
    return { event, data: parsed && typeof parsed === 'object' ? parsed : { value: parsed } }
  } catch {
    throw new Error('平台 SSE 数据无效')
  }
}

export async function* parsePlatformSSE(source) {
  const textDecoder = decoder()
  let buffer = ''
  let skipLeadingLF = false
  const appendText = text => {
    if (!text) return
    // A CR at the end of a chunk has already become LF. Consume the paired LF
    // in the next non-empty decoded chunk without creating an event boundary.
    if (skipLeadingLF && text.startsWith('\n')) text = text.slice(1)
    skipLeadingLF = text.endsWith('\r')
    buffer += text.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  }
  for await (const chunk of asChunks(source)) {
    appendText(textDecoder.decode(chunk, { stream: true }))
    let boundary
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const parsed = parseEventBlock(block)
      if (parsed) yield parsed
    }
  }
  appendText(textDecoder.decode())
  if (buffer.trim()) {
    const parsed = parseEventBlock(buffer.replace(/\n+$/, ''))
    if (parsed) yield parsed
  }
}
