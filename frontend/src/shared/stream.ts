import { ApiError, isAborted } from './http.ts'
import { nullableNumber, number, object, string } from './validation.ts'

export type RunStatus =
  | 'pending'
  | 'running'
  | 'cancel_requested'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'
export interface RunSnapshot {
  id: string
  resource_type:
    | 'chat'
    | 'chat_summary'
    | 'connection_test'
    | 'public'
    | 'agent_preview'
    | 'agent_chat'
    | 'agent_invocation'
    | 'work_turn'
    | 'knowledge_embedding'
    | 'knowledge_rerank'
    | 'knowledge_graph'
  resource_id: string
  status: RunStatus
  attempt_id: string | null
  attempt_no: number
  revision: number
  projection_seq: number
  text: string
  reasoning?: string
  error: { code: string; message: string } | null
  created_at: string
  result?: Record<string, unknown> | null
  usage: {
    input_tokens: number | null
    output_tokens: number | null
    status: 'pending' | 'known' | 'unknown'
  }
}
function integer(value: unknown): number {
  const parsed = number(value)
  if (!Number.isSafeInteger(parsed) || parsed < 0) throw new Error('快照版本格式不正确')
  return parsed
}
export function parseRunSnapshot(value: unknown): RunSnapshot {
  try {
    const run = object(value)
    const usage = object(run.usage)
    const status = run.status
    const usageStatus = usage.status
    const resourceType = run.resource_type
    if (
      resourceType !== 'chat' &&
      resourceType !== 'chat_summary' &&
      resourceType !== 'connection_test' &&
      resourceType !== 'public' &&
      resourceType !== 'agent_preview' &&
      resourceType !== 'agent_chat' &&
      resourceType !== 'agent_invocation' &&
      resourceType !== 'work_turn' &&
      resourceType !== 'knowledge_embedding' &&
      resourceType !== 'knowledge_rerank' &&
      resourceType !== 'knowledge_graph'
    )
      throw new Error('resource_type')
    if (
      status !== 'pending' &&
      status !== 'running' &&
      status !== 'cancel_requested' &&
      status !== 'completed' &&
      status !== 'failed' &&
      status !== 'cancelled' &&
      status !== 'interrupted'
    )
      throw new Error('status')
    if (usageStatus !== 'pending' && usageStatus !== 'known' && usageStatus !== 'unknown')
      throw new Error('usage')
    const error = run.error === null ? null : object(run.error)
    return {
      id: string(run.id),
      resource_type: resourceType,
      resource_id: string(run.resource_id),
      status,
      attempt_id: run.attempt_id === null ? null : string(run.attempt_id),
      attempt_no: integer(run.attempt_no),
      revision: integer(run.revision),
      projection_seq: integer(run.projection_seq),
      text: string(run.text),
      ...(run.reasoning === undefined ? {} : { reasoning: string(run.reasoning) }),
      error: error === null ? null : { code: string(error.code), message: string(error.message) },
      created_at: string(run.created_at),
      ...(run.result === undefined
        ? {}
        : { result: run.result === null ? null : object(run.result) }),
      usage: {
        input_tokens: nullableNumber(usage.input_tokens),
        output_tokens: nullableNumber(usage.output_tokens),
        status: usageStatus
      }
    }
  } catch {
    throw new Error('运行快照格式不正确，请刷新后重试')
  }
}
export function isTerminal(run: RunSnapshot): boolean {
  return (
    run.status === 'completed' ||
    run.status === 'failed' ||
    run.status === 'cancelled' ||
    run.status === 'interrupted'
  )
}
export function acceptSnapshot(current: RunSnapshot | null, incoming: RunSnapshot): boolean {
  if (!current) return true
  if (current.id !== incoming.id || incoming.attempt_no < current.attempt_no) return false
  if (incoming.attempt_no > current.attempt_no) return incoming.revision >= current.revision
  if (current.attempt_id !== null && current.attempt_id !== incoming.attempt_id) return false
  return (
    incoming.revision > current.revision ||
    (incoming.revision === current.revision && incoming.projection_seq > current.projection_seq)
  )
}
export async function consumeSnapshots(
  response: Response,
  onSnapshot: (snapshot: RunSnapshot) => void,
  signal: AbortSignal
): Promise<void> {
  if (!response.headers.get('Content-Type')?.includes('text/event-stream') || !response.body)
    throw new Error('服务器未返回有效事件流')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = ''
  let data: string[] = []
  let dataLength = 0
  const maxEventLength = 16 * 1024 * 1024
  const abort = () => {
    void reader.cancel().catch(() => undefined)
  }
  signal.addEventListener('abort', abort, { once: true })
  function line(value: string): void {
    if (value.length > maxEventLength) throw new Error('事件流超过允许长度')
    if (value === '') {
      if (data.length) {
        if (eventType !== 'snapshot') throw new Error('服务器返回了未知事件类型')
        const payload: unknown = JSON.parse(data.join('\n'))
        onSnapshot(parseRunSnapshot(payload))
      }
      eventType = ''
      data = []
      dataLength = 0
      return
    }
    if (value.startsWith(':')) return
    const colon = value.indexOf(':')
    const field = colon === -1 ? value : value.slice(0, colon)
    const content = colon === -1 ? '' : value.slice(colon + 1).replace(/^ /, '')
    if (field === 'event') eventType = content
    if (field === 'data') {
      dataLength += content.length
      if (dataLength > maxEventLength) throw new Error('事件流超过允许长度')
      data.push(content)
    }
  }
  try {
    signal.throwIfAborted()
    while (true) {
      const chunk = await reader.read()
      signal.throwIfAborted()
      buffer += decoder.decode(chunk.value, { stream: !chunk.done })
      let newline = buffer.indexOf('\n')
      while (newline >= 0) {
        line(buffer.slice(0, newline).replace(/\r$/, ''))
        buffer = buffer.slice(newline + 1)
        newline = buffer.indexOf('\n')
      }
      if (buffer.length > maxEventLength) throw new Error('事件流超过允许长度')
      if (chunk.done) break
    }
  } finally {
    signal.removeEventListener('abort', abort)
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
interface RunTransport {
  get: (signal: AbortSignal) => Promise<RunSnapshot>
  open: (signal: AbortSignal) => Promise<Response>
}
export async function followRun(
  transport: RunTransport,
  initial: RunSnapshot,
  onSnapshot: (snapshot: RunSnapshot) => void,
  signal: AbortSignal
): Promise<RunSnapshot> {
  let latest = initial
  let failures = 0
  const accept = (run: RunSnapshot) => {
    if (acceptSnapshot(latest, run)) {
      latest = run
      onSnapshot(run)
    }
  }
  while (!isTerminal(latest)) {
    signal.throwIfAborted()
    try {
      await consumeSnapshots(await transport.open(signal), accept, signal)
      if (isTerminal(latest)) break
    } catch (error) {
      if (isAborted(error) || error instanceof ApiError) throw error
      if (failures >= 3) throw new Error('实时连接已断开，任务仍可能运行。请点击恢复查看状态')
    }
    failures += 1
    if (failures > 4) throw new Error('实时连接已断开，任务仍可能运行。请点击恢复查看状态')
    await new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      }
      const timer = setTimeout(() => {
        signal.removeEventListener('abort', abort)
        resolve()
      }, 700)
      signal.addEventListener('abort', abort, { once: true })
    })
    accept(await transport.get(signal))
  }
  return latest
}
export const runStatusLabel: Record<RunStatus, string> = {
  pending: '排队中',
  running: '生成中',
  cancel_requested: '正在停止',
  completed: '已完成',
  failed: '失败',
  cancelled: '已停止',
  interrupted: '已中断'
}
