import { object } from './validation.ts'

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  constructor(code: string, message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}
export function isAborted(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof TypeError) return '无法连接服务，请检查网络后重试'
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}
export interface ContextTicket {
  signal: AbortSignal
  isCurrent: () => boolean
}
export class RequestScope {
  private controller = new AbortController()
  current(): ContextTicket {
    const controller = this.controller
    return {
      signal: controller.signal,
      isCurrent: () => controller === this.controller && !controller.signal.aborted
    }
  }
  replace(): ContextTicket {
    this.controller.abort()
    this.controller = new AbortController()
    return this.current()
  }
  dispose(): void {
    this.controller.abort()
  }
}
interface HttpOptions {
  fetch?: typeof fetch
  onUnauthorized?: () => void
}
interface OpenOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
}
interface RequestOptions<T> extends OpenOptions {
  decode: (value: unknown) => T
}
export function createHttpClient(options: HttpOptions = {}) {
  let controller = new AbortController()
  let unauthorized = options.onUnauthorized
  const fetcher = options.fetch ?? globalThis.fetch.bind(globalThis)
  function invalidate(): void {
    controller.abort()
    controller = new AbortController()
  }
  async function open(path: string, init: OpenOptions = {}): Promise<Response> {
    const session = controller
    const signal = init.signal ? AbortSignal.any([session.signal, init.signal]) : session.signal
    signal.throwIfAborted()
    const response = await fetcher(`/api${path}`, {
      method: init.method ?? 'GET',
      credentials: 'include',
      headers: {
        Accept:
          init.body === undefined ? 'application/json, text/event-stream' : 'application/json',
        ...(init.body !== undefined || (init.method !== undefined && init.method !== 'GET')
          ? {
              'Content-Type':
                init.body instanceof Blob ? 'application/octet-stream' : 'application/json'
            }
          : {})
      },
      ...(init.body === undefined
        ? {}
        : { body: init.body instanceof Blob ? init.body : JSON.stringify(init.body) }),
      signal
    })
    if (!response.ok) {
      let code = `http_${response.status}`
      let message =
        response.status === 401 ? '登录已过期，请重新登录' : `请求失败（${response.status}）`
      try {
        const payload: unknown = await response.json()
        const error = object(object(payload).error)
        if (typeof error.code === 'string') code = error.code
        if (typeof error.message === 'string') message = error.message
      } catch {
        /* Invalid error bodies use the status fallback. */
      }
      if (response.status === 401 && session === controller && !signal.aborted) unauthorized?.()
      throw new ApiError(code, message, response.status)
    }
    signal.throwIfAborted()
    return response
  }
  async function request<T>(path: string, init: RequestOptions<T>): Promise<T> {
    const session = controller
    const response = await open(path, init)
    if (response.status === 204) return init.decode(undefined)
    const payload: unknown = await response.json()
    session.signal.throwIfAborted()
    init.signal?.throwIfAborted()
    return init.decode(payload)
  }
  return {
    open,
    request,
    invalidate,
    setUnauthorizedHandler(handler: () => void) {
      unauthorized = handler
    }
  }
}
export const http = createHttpClient()
export const decodeVoid = (): void => undefined
