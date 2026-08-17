import assert from 'node:assert/strict'

import { parsePlatformSSE } from '../../plugins/ai/utils/sseParser.js'

const encoder = new TextEncoder()
const source = [
  encoder.encode('event: content\ndata: {"delta":"你'),
  encoder.encode('好"}\r\n\r\n: keepalive\r\nevent: reasoning\ndata: {"delta":"思考"}\r\n\r\n'),
  encoder.encode('event: message\ndata: ["first",\n'),
  encoder.encode('data: "second"]\n\nevent: usage\ndata: {"inputTokens":2,\n'),
  encoder.encode('data: "outputTokens":3}\n\nevent: done\ndata: [DONE]\n\n'),
]

const events = []
for await (const event of parsePlatformSSE(source)) events.push(event)
assert.deepEqual(events, [
  { event: 'content', data: { delta: '你好' } },
  { event: 'reasoning', data: { delta: '思考' } },
  { event: 'message', data: ['first', 'second'] },
  { event: 'usage', data: { inputTokens: 2, outputTokens: 3 } },
  { event: 'done', data: { status: 'completed' } },
])

const tail = []
for await (const event of parsePlatformSSE([encoder.encode('event: meta\ndata: {"sessionId":"ses_1"}')])) {
  tail.push(event)
}
assert.deepEqual(tail, [{ event: 'meta', data: { sessionId: 'ses_1' } }])
console.log('ok sse parser')
