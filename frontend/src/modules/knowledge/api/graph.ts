import { http } from '../../../shared/http.ts'
import { parseDiagnostics } from './index.ts'
import type { RetrievalDiagnostics } from './index.ts'
import {
  boolean,
  list,
  nullableNumber,
  number,
  object,
  string
} from '../../../shared/validation.ts'

export const graphStrategies = ['auto', 'default', 'low', 'disabled'] as const
export type GraphStrategy = (typeof graphStrategies)[number]
export const graphStatuses = [
  'pending',
  'running',
  'waiting_model',
  'draining',
  'completed',
  'failed',
  'cancelled',
  'interrupted'
] as const
export type GraphStatus = (typeof graphStatuses)[number]
export interface GraphConfig {
  version: number
  llm_model_id: string
  reasoning_strategy: GraphStrategy
  timeout_seconds: number
  max_output_tokens: number
  concurrency: number
}
export type GraphConfigInput = Omit<GraphConfig, 'version'>
export interface GraphConfigState {
  config: GraphConfig | null
  available: boolean
  active_revision: string | null
  requires_rebuild: boolean
  can_manage: boolean
  supported_reasoning_strategies: GraphStrategy[]
}
export interface GraphNode {
  id: string
  entity_name: string
  entity_type: string
  description: string
}
export interface GraphEdge {
  id: string
  source: string
  target: string
  description: string
}
export interface GraphData {
  revision: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  is_truncated: boolean
}
export interface GraphSource {
  document_id: string
  file_id: string
  chunk_id: string
  ordinal: number
  content_hash: string
  content: string
}
export interface GraphQueryResult {
  diagnostics?: RetrievalDiagnostics | null | undefined
  context: string
  entities: GraphNode[]
  relationships: GraphEdge[]
  sources: GraphSource[]
}
export interface GraphPreviewResult {
  entities: number
  relations: number
  chunks: number
  seconds: number
}
export interface GraphJob {
  id: string
  base_id: string
  operation: 'build' | 'preview' | 'query' | 'gc'
  status: GraphStatus
  phase: string
  version: number
  attempt_id: string
  graph_revision: string | null
  processed_documents: number | null
  total_documents: number | null
  progress: {
    extracted_chunks: number | null
    total_chunks: number | null
    entities: number | null
    relations: number | null
  }
  error: { code: string; message: string } | null
  result: GraphPreviewResult | GraphQueryResult | null
  cancel_allowed: boolean
  created_at: string
  updated_at: string
  finished_at: string | null
}
function choice<const T extends readonly string[]>(value: unknown, allowed: T): T[number] {
  const result = string(value)
  if (!allowed.includes(result)) throw new Error('图谱返回了未知状态')
  return result as T[number]
}
function config(value: unknown): GraphConfig {
  const d = object(value)
  return {
    version: number(d.version),
    llm_model_id: string(d.llm_model_id),
    reasoning_strategy: choice(d.reasoning_strategy, graphStrategies),
    timeout_seconds: number(d.timeout_seconds),
    max_output_tokens: number(d.max_output_tokens),
    concurrency: number(d.concurrency)
  }
}
function state(value: unknown): GraphConfigState {
  const d = object(value)
  return {
    config: d.config === null ? null : config(d.config),
    available: boolean(d.available),
    active_revision: d.active_revision === null ? null : string(d.active_revision),
    requires_rebuild: boolean(d.requires_rebuild),
    supported_reasoning_strategies: list((value) => choice(value, graphStrategies))(
      d.supported_reasoning_strategies
    ),
    can_manage: boolean(d.can_manage)
  }
}
function node(value: unknown): GraphNode {
  const d = object(value)
  return {
    id: string(d.id),
    entity_name: string(d.entity_name),
    entity_type: string(d.entity_type),
    description: string(d.description)
  }
}
function edge(value: unknown): GraphEdge {
  const d = object(value)
  return {
    id: string(d.id),
    source: string(d.source),
    target: string(d.target),
    description: string(d.description)
  }
}
function source(value: unknown): GraphSource {
  const d = object(value)
  return {
    document_id: string(d.document_id),
    file_id: string(d.file_id),
    chunk_id: string(d.chunk_id),
    ordinal: number(d.ordinal),
    content_hash: string(d.content_hash),
    content: string(d.content)
  }
}
function previewResult(value: unknown): GraphPreviewResult {
  const d = object(value)
  return {
    entities: number(d.entities),
    relations: number(d.relations),
    chunks: number(d.chunks),
    seconds: number(d.seconds)
  }
}
function queryResult(value: unknown): GraphQueryResult {
  const d = object(value)
  return {
    diagnostics: parseDiagnostics(d.diagnostics, true),
    context: string(d.context),
    entities: list(node)(d.entities),
    relationships: list(edge)(d.relationships),
    sources: list(source)(d.sources)
  }
}
export function parseGraphJob(value: unknown): GraphJob {
  const d = object(value),
    progress = object(d.progress)
  const operation = choice(d.operation, ['build', 'preview', 'query', 'gc'])
  const error = d.error === null ? null : object(d.error)
  return {
    id: string(d.id),
    base_id: string(d.base_id),
    operation,
    status: choice(d.status, graphStatuses),
    phase: string(d.phase),
    version: number(d.version),
    attempt_id: string(d.attempt_id),
    graph_revision: d.graph_revision === null ? null : string(d.graph_revision),
    processed_documents: nullableNumber(d.processed_documents),
    total_documents: nullableNumber(d.total_documents),
    progress: {
      extracted_chunks: nullableNumber(progress.extracted_chunks),
      total_chunks: nullableNumber(progress.total_chunks),
      entities: nullableNumber(progress.entities),
      relations: nullableNumber(progress.relations)
    },
    error: error ? { code: string(error.code), message: string(error.message) } : null,
    result:
      d.result === null
        ? null
        : operation === 'preview'
          ? previewResult(d.result)
          : operation === 'query'
            ? queryResult(d.result)
            : null,
    cancel_allowed: boolean(d.cancel_allowed),
    created_at: string(d.created_at),
    updated_at: string(d.updated_at),
    finished_at: d.finished_at === null ? null : string(d.finished_at)
  }
}
const endpoint = (baseId: string) => `/knowledge/bases/${encodeURIComponent(baseId)}/graph`
export const getGraphConfig = (baseId: string, signal: AbortSignal) =>
  http.request(endpoint(baseId) + '/config', { signal, decode: state })
export const saveGraphConfig = (
  baseId: string,
  body: GraphConfigInput & { expected_version: number },
  signal: AbortSignal
) => http.request(endpoint(baseId) + '/config', { method: 'PATCH', body, signal, decode: state })
export const listGraphJobs = (baseId: string, signal: AbortSignal) =>
  http.request(endpoint(baseId) + '/jobs', { signal, decode: list(parseGraphJob) })
export const getGraphJob = (baseId: string, jobId: string, signal: AbortSignal) =>
  http.request(endpoint(baseId) + '/jobs/' + encodeURIComponent(jobId), {
    signal,
    decode: parseGraphJob
  })
export const cancelGraphJob = (baseId: string, jobId: string, signal: AbortSignal) =>
  http.request(endpoint(baseId) + '/jobs/' + encodeURIComponent(jobId) + '/cancel', {
    method: 'POST',
    body: {},
    signal,
    decode: parseGraphJob
  })
export const startGraphBuild = (
  baseId: string,
  operation: 'build' | 'preview',
  body: {
    client_request_id: string
    expected_version: number
    rebuild?: boolean
    document_id?: string | null
  },
  signal: AbortSignal
) =>
  http.request(endpoint(baseId) + '/' + operation, {
    method: 'POST',
    body,
    signal,
    decode: parseGraphJob
  })
export const getGraphLabels = (baseId: string, signal: AbortSignal) =>
  http.request(endpoint(baseId) + '/labels', {
    signal,
    decode(value) {
      const d = object(value)
      return { items: list(string)(d.items), truncated: boolean(d.truncated) }
    }
  })
export const getGraphData = (
  baseId: string,
  options: { node_label: string; max_depth: number; max_nodes: number },
  signal: AbortSignal
) =>
  http.request(
    endpoint(baseId) +
      '/subgraph?' +
      new URLSearchParams({
        node_label: options.node_label,
        max_depth: String(options.max_depth),
        max_nodes: String(options.max_nodes)
      }).toString(),
    {
      signal,
      decode(value): GraphData {
        const d = object(value)
        return {
          revision: string(d.revision),
          nodes: list(node)(d.nodes),
          edges: list(edge)(d.edges),
          is_truncated: boolean(d.is_truncated)
        }
      }
    }
  )
export const queryGraph = (
  baseId: string,
  body: {
    client_request_id: string
    query: string
    mode: 'mix' | 'local' | 'global' | 'hybrid' | 'naive'
    top_k: number
  },
  signal: AbortSignal
) =>
  http.request(endpoint(baseId) + '/query', { method: 'POST', body, signal, decode: parseGraphJob })
