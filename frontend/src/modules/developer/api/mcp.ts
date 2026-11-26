import { decodeVoid, http } from '../../../shared/http.ts'
import { boolean, list, number, object, string } from '../../../shared/validation.ts'

const publication = (value: unknown) => {
  const row = object(value)
  return {
    id: string(row.id),
    name: string(row.name),
    toolName: string(row.tool_name),
    serverName: string(row.server_name),
    available: boolean(row.available)
  }
}
const source = (value: unknown) => {
  const row = object(value)
  return {
    serverId: string(row.server_id),
    serverName: string(row.server_name),
    name: string(row.name),
    version: number(row.version)
  }
}
export type McpPublication = ReturnType<typeof publication>
export type McpSource = ReturnType<typeof source>
const path = (appId: string) =>
  '/developer/applications/' + encodeURIComponent(appId) + '/mcp-publications'
export const listMcpPublications = (appId: string, signal: AbortSignal) =>
  http.request(path(appId), { signal, decode: list(publication) })
export const listMcpSources = (signal: AbortSignal) =>
  http.request('/mcp/publication-sources', { signal, decode: list(source) })
export const publishMcpTool = (appId: string, tool: McpSource, name: string, signal: AbortSignal) =>
  http.request(path(appId), {
    method: 'POST',
    signal,
    body: {
      server_id: tool.serverId,
      tool_name: tool.name,
      expected_version: tool.version,
      name,
      confirm_read_only: true
    },
    decode: publication
  })
export const withdrawMcpTool = (appId: string, id: string, signal: AbortSignal) =>
  http.request(path(appId) + '/' + encodeURIComponent(id), {
    method: 'DELETE',
    body: {},
    signal,
    decode: decodeVoid
  })
