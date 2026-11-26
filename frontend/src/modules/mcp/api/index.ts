import { http } from '../../../shared/http.ts'
import { boolean, list, number, object, string } from '../../../shared/validation.ts'

export interface ToolSelection {
  server_id: string
  name: string
  version: number
  arguments: Record<string, unknown>
}

const definition = (row: Record<string, unknown>) => ({
  name: string(row.name),
  description: string(row.description),
  inputSchema: object(row.input_schema),
  readOnly: boolean(row.read_only)
})

const tool = (value: unknown) => {
  const row = object(value)
  return {
    ...definition(row),
    serverId: string(row.server_id),
    serverName: string(row.server_name),
    version: number(row.version)
  }
}
const managedTool = (value: unknown) => {
  const row = object(value)
  return { ...definition(row), enabled: boolean(row.enabled), roleIds: list(string)(row.role_ids) }
}
const server = (value: unknown) => {
  const row = object(value)
  return {
    id: string(row.id),
    name: string(row.name),
    endpoint: row.endpoint === null ? null : string(row.endpoint),
    enabled: boolean(row.enabled),
    hasToken: row.has_token === null ? null : boolean(row.has_token),
    version: number(row.version),
    syncedAt: row.synced_at === null ? null : string(row.synced_at),
    tools: list(managedTool)(row.tools)
  }
}
const role = (value: unknown) => {
  const row = object(value)
  return { id: string(row.id), name: string(row.name) }
}
const call = (value: unknown) => {
  const row = object(value)
  return {
    id: string(row.id),
    name: string(row.tool_name),
    status: string(row.status),
    code: row.error_code === null ? null : string(row.error_code),
    time: string(row.created_at),
    duration: number(row.duration_ms)
  }
}
export type CatalogTool = ReturnType<typeof tool>
export type ManagedTool = ReturnType<typeof managedTool>
export type McpServer = ReturnType<typeof server>
type ServerBody = { name: string; endpoint: string; token?: string }

const path = (id: string) => '/mcp/servers/' + encodeURIComponent(id)
export const listMcpTools = (signal: AbortSignal) =>
  http.request('/mcp/tools', { signal, decode: list(tool) })
export const listMcpServers = (signal: AbortSignal) =>
  http.request('/mcp/servers', { signal, decode: list(server) })
export const listMcpRoles = (signal: AbortSignal) =>
  http.request('/mcp/roles', { signal, decode: list(role) })
export const listMcpCalls = (id: string, signal: AbortSignal) =>
  http.request(path(id) + '/calls', { signal, decode: list(call) })
export const createMcpServer = (body: ServerBody, signal: AbortSignal) =>
  http.request('/mcp/servers', { method: 'POST', body, signal, decode: server })
export const updateMcpServer = (
  id: string,
  body: ServerBody & { expected_version: number; enabled: boolean; clear_token?: boolean },
  signal: AbortSignal
) => http.request(path(id), { method: 'PATCH', body, signal, decode: server })
export const syncMcpServer = (id: string, version: number, signal: AbortSignal) =>
  http.request(path(id) + '/sync', {
    method: 'POST',
    body: { expected_version: version },
    signal,
    decode: server
  })
export const grantMcpTool = (
  id: string,
  name: string,
  version: number,
  enabled: boolean,
  roleIds: string[],
  signal: AbortSignal
) =>
  http.request(path(id) + '/tools/' + encodeURIComponent(name), {
    method: 'PUT',
    body: { expected_version: version, enabled, role_ids: roleIds },
    signal,
    decode: server
  })
