import { decodeVoid, http } from '../../../shared/http.ts'
import { waitForBackgroundObservation } from '../../../shared/backgroundObservation.ts'
import {
  boolean,
  list,
  nullableNumber,
  number,
  object,
  string
} from '../../../shared/validation.ts'
import { parseRunSnapshot } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { parseManagedFile } from '../../files/public.ts'
import type { ManagedFile } from '../../files/public.ts'

type Failure = { code: string; message: string } | null
type Access = 'private' | 'public'
export interface KnowledgeBase {
  availability: 'available'
  id: string
  name: string
  description: string
  owner_id: string
  owner_name?: string | undefined
  sharing_scope?: 'private' | 'users' | 'public' | undefined
  document_policy?: 'legacy' | 'library'
  access: Access
  version: number
  acl_version: number
  embed_model_id: string
  rerank_model_id?: string | null
  use_default_rerank?: boolean
  chunk_size: number
  chunk_overlap: number
  active_revision: string | null
  active_job_id: string | null
  index_status: 'empty' | 'building' | 'ready' | 'failed' | 'reindex_required'
  document_count: number
  last_error: Failure
  enable_knowledge_graph: boolean
  llm_model_id: string | null
  graph_status: 'disabled' | 'pending' | 'ready' | 'building' | 'stale' | 'failed'
  created_at: string
  updated_at: string
  can_manage: boolean
}
export interface UnavailableKnowledgeBase {
  id: string
  name: string
  owner_id: string
  owner_name: string
  availability: 'owner_disabled' | 'owner_deleted'
}
export type KnowledgeBaseListItem = KnowledgeBase | UnavailableKnowledgeBase
export interface KnowledgeFolder {
  id: string
  base_id: string
  parent_id: string | null
  name: string
  version: number
  created_at: string
  updated_at: string
}
export interface KnowledgeDocument {
  parsing_engine?: string | null
  id: string
  base_id: string
  file_id: string
  file_name: string
  file_size: number
  mime: string
  folder_id: string | null
  version: number
  status: 'pending' | 'processing' | 'ready' | 'failed' | 'deleting'
  active_revision: string | null
  job_id: string | null
  chunk_count: number | null
  chunk_size: number
  chunk_overlap: number
  error: Failure
  graph_status: 'disabled' | 'pending' | 'ready' | 'building' | 'stale' | 'failed'
  graph_error: Failure
  created_at: string
  updated_at: string
}
export interface KnowledgeJob {
  id: string
  base_id: string
  operation:
    | 'import'
    | 'rechunk'
    | 'rebuild'
    | 'delete_document'
    | 'delete_folder'
    | 'delete_base'
    | 'gc'
  status:
    | 'pending'
    | 'running'
    | 'waiting_model'
    | 'waiting_parse'
    | 'publishing'
    | 'completed'
    | 'partial'
    | 'failed'
    | 'cancel_requested'
    | 'cancelled'
    | 'interrupted'
  phase: string
  version: number
  attempt_id: string
  index_revision: string | null
  total_documents: number
  completed_documents: number
  failed_documents: number
  chunk_count: number | null
  document_results: {
    document_id: string
    file_id: string
    status: string
    chunk_count: number | null
    error: Failure
  }[]
  error: Failure
  created_at: string
  updated_at: string
  finished_at: string | null
  cancel_allowed: boolean
}
export interface KnowledgeHit {
  chunk_id: string
  document_id: string
  file_id: string
  source_id: string
  knowledge_base_id: string
  index_revision: string
  title: string
  content: string
  score: number
  vector_score?: number | null | undefined
  fusion_score?: number | null | undefined
  rerank_score?: number | null | undefined
}
export interface RetrievalDiagnostics {
  candidate_count: number | null
  retrieval_modes: Record<string, 'hybrid' | 'vector'>
  rerank_status:
    | 'not_configured'
    | 'pending'
    | 'completed'
    | 'fallback'
    | 'no_candidates'
    | 'failed'
  rerank_run_id?: string | undefined
  rerank_milliseconds?: number | undefined
  rerank_reason?: string | undefined
}
export function parseDiagnostics(value: unknown, partial = false): RetrievalDiagnostics | null {
  if (value == null) return null
  const d = object(value)
  if (!Object.keys(d).length) return null
  const modes = partial && d.retrieval_modes == null ? {} : object(d.retrieval_modes)
  return {
    candidate_count: partial && d.candidate_count == null ? null : number(d.candidate_count),
    retrieval_modes: Object.fromEntries(
      Object.entries(modes).map(([id, mode]) => [id, choice(mode, ['hybrid', 'vector'])])
    ),
    rerank_status: choice(d.rerank_status, [
      'not_configured',
      'pending',
      'completed',
      'fallback',
      'no_candidates',
      'failed'
    ]),
    rerank_run_id: typeof d.rerank_run_id === 'string' ? d.rerank_run_id : undefined,
    rerank_milliseconds:
      typeof d.rerank_milliseconds === 'number' ? d.rerank_milliseconds : undefined,
    rerank_reason: typeof d.rerank_reason === 'string' ? d.rerank_reason : undefined
  }
}
export interface KnowledgePreparation {
  kind: 'knowledge_preparation'
  id: string
  resource_type: string
  resource_id: string
  status:
    | 'queued'
    | 'waiting_models'
    | 'searching'
    | 'ready'
    | 'admitted'
    | 'completed'
    | 'failed'
    | 'cancel_requested'
    | 'cancelled'
    | 'interrupted'
  phase: string
  run: RunSnapshot | null
  hits: KnowledgeHit[]
  diagnostics?: RetrievalDiagnostics | null | undefined
  error: Failure
  created_at: string
  updated_at: string
}
export interface KnowledgeUser {
  id: string
  username: string
}
export interface KnowledgeAclEntry {
  user_id: string
  effect: 'allow' | 'deny'
  expires_at: string | null
}
export interface KnowledgeAcl {
  base: KnowledgeBase
  entries: KnowledgeAclEntry[]
  builtin_permissions: { source: string; enabled: boolean; deny_overridable: boolean }[]
}
function choice<const T extends readonly string[]>(value: unknown, values: T): T[number] {
  const result = string(value)
  if (!values.includes(result)) throw new Error('知识库返回了未知状态')
  return result as T[number]
}
const nullableString = (value: unknown) => (value === null ? null : string(value))
function failure(value: unknown): Failure {
  if (value === null) return null
  const data = object(value)
  return { code: string(data.code), message: string(data.message) }
}
function parseBaseListItem(value: unknown): KnowledgeBaseListItem {
  const d = object(value)
  if (d.availability === 'owner_disabled' || d.availability === 'owner_deleted') {
    return {
      id: string(d.id),
      name: string(d.name),
      owner_id: string(d.owner_id),
      owner_name: string(d.owner_name),
      availability: d.availability
    }
  }
  return parseBase(value)
}
export function parseBase(value: unknown): KnowledgeBase {
  const d = object(value)
  if (d.availability !== undefined && d.availability !== 'available')
    throw new Error('知识库当前不可用')
  return {
    availability: 'available',
    id: string(d.id),
    name: string(d.name),
    description: string(d.description),
    owner_id: string(d.owner_id),
    owner_name: typeof d.owner_name === 'string' ? d.owner_name : undefined,
    sharing_scope:
      d.sharing_scope == null ? undefined : choice(d.sharing_scope, ['private', 'users', 'public']),
    document_policy:
      d.document_policy == null ? 'legacy' : choice(d.document_policy, ['legacy', 'library']),
    access: choice(d.access, ['private', 'public']),
    version: number(d.version),
    acl_version: number(d.acl_version),
    embed_model_id: string(d.embed_model_id),
    rerank_model_id: d.rerank_model_id == null ? null : string(d.rerank_model_id),
    use_default_rerank: d.use_default_rerank === undefined ? true : boolean(d.use_default_rerank),
    chunk_size: number(d.chunk_size),
    chunk_overlap: number(d.chunk_overlap),
    active_revision: nullableString(d.active_revision),
    active_job_id: nullableString(d.active_job_id),
    index_status: choice(d.index_status, [
      'empty',
      'building',
      'ready',
      'failed',
      'reindex_required'
    ]),
    document_count: number(d.document_count),
    last_error: failure(d.last_error),
    enable_knowledge_graph: boolean(d.enable_knowledge_graph),
    llm_model_id: nullableString(d.llm_model_id),
    graph_status: choice(d.graph_status, [
      'disabled',
      'pending',
      'ready',
      'building',
      'stale',
      'failed'
    ]),
    created_at: string(d.created_at),
    updated_at: string(d.updated_at),
    can_manage: boolean(d.can_manage)
  }
}
function parseFolder(value: unknown): KnowledgeFolder {
  const d = object(value)
  return {
    id: string(d.id),
    base_id: string(d.base_id),
    parent_id: nullableString(d.parent_id),
    name: string(d.name),
    version: number(d.version),
    created_at: string(d.created_at),
    updated_at: string(d.updated_at)
  }
}
function parseDocument(value: unknown): KnowledgeDocument {
  const d = object(value)
  return {
    parsing_engine: d.parsing_engine == null ? null : string(d.parsing_engine),
    id: string(d.id),
    base_id: string(d.base_id),
    file_id: string(d.file_id),
    file_name: string(d.file_name),
    file_size: number(d.file_size),
    mime: string(d.mime),
    folder_id: nullableString(d.folder_id),
    version: number(d.version),
    status: choice(d.status, ['pending', 'processing', 'ready', 'failed', 'deleting']),
    active_revision: nullableString(d.active_revision),
    job_id: nullableString(d.job_id),
    chunk_count: nullableNumber(d.chunk_count),
    chunk_size: number(d.chunk_size),
    chunk_overlap: number(d.chunk_overlap),
    error: failure(d.error),
    graph_status: choice(d.graph_status, [
      'disabled',
      'pending',
      'ready',
      'building',
      'stale',
      'failed'
    ]),
    graph_error: failure(d.graph_error),
    created_at: string(d.created_at),
    updated_at: string(d.updated_at)
  }
}
function parseJob(value: unknown): KnowledgeJob {
  const d = object(value)
  return {
    id: string(d.id),
    base_id: string(d.base_id),
    operation: choice(d.operation, [
      'import',
      'rechunk',
      'rebuild',
      'delete_document',
      'delete_folder',
      'delete_base',
      'gc'
    ]),
    status: choice(d.status, [
      'pending',
      'running',
      'waiting_model',
      'waiting_parse',
      'publishing',
      'completed',
      'partial',
      'failed',
      'cancel_requested',
      'cancelled',
      'interrupted'
    ]),
    phase: string(d.phase),
    version: number(d.version),
    attempt_id: string(d.attempt_id),
    index_revision: nullableString(d.index_revision),
    total_documents: number(d.total_documents),
    completed_documents: number(d.completed_documents),
    failed_documents: number(d.failed_documents),
    chunk_count: nullableNumber(d.chunk_count),
    document_results: list((value) => {
      const r = object(value)
      return {
        document_id: string(r.document_id),
        file_id: string(r.file_id),
        status: string(r.status),
        chunk_count: nullableNumber(r.chunk_count),
        error: failure(r.error)
      }
    })(d.document_results),
    error: failure(d.error),
    created_at: string(d.created_at),
    updated_at: string(d.updated_at),
    finished_at: nullableString(d.finished_at),
    cancel_allowed: boolean(d.cancel_allowed)
  }
}
export function parseHit(value: unknown): KnowledgeHit {
  const d = object(value)
  return {
    chunk_id: string(d.chunk_id),
    document_id: string(d.document_id),
    file_id: string(d.file_id),
    source_id: string(d.source_id),
    knowledge_base_id: string(d.knowledge_base_id),
    index_revision: string(d.index_revision),
    title: string(d.title),
    content: string(d.content),
    score: number(d.score),
    vector_score: d.vector_score == null ? null : number(d.vector_score),
    fusion_score: d.fusion_score == null ? null : number(d.fusion_score),
    rerank_score: d.rerank_score == null ? null : number(d.rerank_score)
  }
}
export function parsePreparation(value: unknown): KnowledgePreparation {
  const d = object(value)
  if (d.kind !== 'knowledge_preparation') throw new Error('知识检索准备响应无效')
  return {
    kind: d.kind,
    id: string(d.id),
    resource_type: string(d.resource_type),
    resource_id: string(d.resource_id),
    status: choice(d.status, [
      'queued',
      'waiting_models',
      'searching',
      'ready',
      'admitted',
      'completed',
      'failed',
      'cancel_requested',
      'cancelled',
      'interrupted'
    ]),
    phase: string(d.phase),
    run: d.run === null ? null : parseRunSnapshot(d.run),
    hits: list(parseHit)(d.hits),
    diagnostics: parseDiagnostics(d.diagnostics),
    error: failure(d.error),
    created_at: string(d.created_at),
    updated_at: string(d.updated_at)
  }
}
export type RunAdmission = RunSnapshot | KnowledgePreparation
export function parseRunAdmission(value: unknown): RunAdmission {
  return object(value).kind === 'knowledge_preparation'
    ? parsePreparation(value)
    : parseRunSnapshot(value)
}
export function isPreparation(value: RunAdmission): value is KnowledgePreparation {
  return 'kind' in value && value.kind === 'knowledge_preparation'
}
const root = '/knowledge'
const basePath = (id: string) => `${root}/bases/${encodeURIComponent(id)}`
const docPath = (id: string, document: string) =>
  `${basePath(id)}/documents/${encodeURIComponent(document)}`
export const listBases = (signal: AbortSignal, scope?: 'owned' | 'shared' | 'all') =>
  http.request(`${root}/bases${scope ? `?${new URLSearchParams({ scope })}` : ''}`, {
    signal,
    decode: list(parseBaseListItem)
  })
export const availableBases = (signal: AbortSignal, agentId?: string) =>
  http.request(
    `${root}/available${agentId ? `?${new URLSearchParams({ agent_id: agentId })}` : ''}`,
    { signal, decode: list(parseBase) }
  )
export const getBase = (id: string, signal: AbortSignal) =>
  http.request(basePath(id), { signal, decode: parseBase })
export const getCapabilities = (signal: AbortSignal) =>
  http.request(`${root}/capabilities`, {
    signal,
    decode: (value) => {
      const d = object(value)
      return {
        milvus_configured: boolean(d.milvus_configured),
        milvus_available: boolean(d.milvus_available),
        native_formats: list(string)(d.native_formats),
        graph_available: boolean(d.graph_available),
        mineru_available: boolean(d.mineru_available),
        default_embedding_configured: d.default_embedding_configured === true,
        default_rerank_configured: d.default_rerank_configured === true,
        default_rerank_model_id:
          d.default_rerank_model_id == null ? null : string(d.default_rerank_model_id)
      }
    }
  })
export interface BaseCreate {
  name: string
  description: string
}
export const createBase = (body: BaseCreate, signal: AbortSignal) =>
  http.request(`${root}/bases`, { method: 'POST', body, signal, decode: parseBase })
export const updateBase = (
  base: KnowledgeBase,
  body: { name: string; description: string },
  signal: AbortSignal
) =>
  http.request(basePath(base.id), {
    method: 'PATCH',
    body: { ...body, expected_version: base.version },
    signal,
    decode: parseBase
  })
export const deleteBase = (
  base: KnowledgeBase,
  key: string,
  signal: AbortSignal,
  confirmReferences = false
) =>
  http.request(basePath(base.id), {
    method: 'DELETE',
    body: {
      expected_version: base.version,
      client_request_id: key,
      confirm_references: confirmReferences
    },
    signal,
    decode: parseJob
  })
export const rebuildBase = (base: KnowledgeBase, key: string, signal: AbortSignal) =>
  http.request(`${basePath(base.id)}/rebuild`, {
    method: 'POST',
    body: { expected_version: base.version, client_request_id: key },
    signal,
    decode: parseJob
  })
export const listFolders = (id: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/folders`, { signal, decode: list(parseFolder) })
export const createFolder = (
  id: string,
  name: string,
  parentId: string | null,
  signal: AbortSignal
) =>
  http.request(`${basePath(id)}/folders`, {
    method: 'POST',
    body: { name, parent_id: parentId },
    signal,
    decode: parseFolder
  })
export const renameFolder = (folder: KnowledgeFolder, name: string, signal: AbortSignal) =>
  http.request(`${basePath(folder.base_id)}/folders/${encodeURIComponent(folder.id)}`, {
    method: 'PATCH',
    body: { expected_version: folder.version, name },
    signal,
    decode: parseFolder
  })
export const deleteFolder = (folder: KnowledgeFolder, key: string, signal: AbortSignal) =>
  http.request(`${basePath(folder.base_id)}/folders/${encodeURIComponent(folder.id)}`, {
    method: 'DELETE',
    body: { expected_version: folder.version, client_request_id: key },
    signal,
    decode: parseJob
  })
export const listDocuments = (id: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/documents`, {
    signal,
    decode: (value) => {
      const d = object(value)
      return { items: list(parseDocument)(d.items), total: number(d.total) }
    }
  })
export const previewDocument = (id: string, document: string, signal: AbortSignal) =>
  http.request(`${docPath(id, document)}/preview`, {
    signal,
    decode: (value) => {
      const d = object(value)
      return {
        document_id: string(d.document_id),
        file_id: string(d.file_id),
        text: string(d.text),
        parser_version: string(d.parser_version),
        chunk_count: nullableNumber(d.chunk_count)
      }
    }
  })
export const uploadDocument = (
  id: string,
  file: File,
  folderId: string | null,
  signal: AbortSignal,
  requestId?: string
) => {
  const query = new URLSearchParams({ filename: file.name })
  if (requestId) query.set('client_request_id', requestId)
  if (folderId) query.set('folder_id', folderId)
  return http.request(`${basePath(id)}/uploads?${query}`, {
    method: 'POST',
    body: file,
    signal,
    decode: (value): { file: ManagedFile; upload_id: string; folder_id: string | null } => {
      const d = object(value)
      return {
        file: parseManagedFile(d.file),
        upload_id: string(d.upload_id),
        folder_id: nullableString(d.folder_id)
      }
    }
  })
}
export const discardUpload = (id: string, uploadId: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/uploads/${encodeURIComponent(uploadId)}`, {
    method: 'DELETE',
    body: {},
    signal,
    decode: decodeVoid
  })
export const importDocuments = (
  id: string,
  body: {
    file_ids: string[]
    folder_id: string | null
    chunk_size: number
    chunk_overlap: number
    client_request_id: string
  },
  signal: AbortSignal
) => http.request(`${basePath(id)}/documents`, { method: 'POST', body, signal, decode: parseJob })
export const rechunkDocument = (
  doc: KnowledgeDocument,
  body: { chunk_size: number; chunk_overlap: number; client_request_id: string },
  signal: AbortSignal
) =>
  http.request(`${docPath(doc.base_id, doc.id)}/rechunk`, {
    method: 'POST',
    body: { ...body, expected_version: doc.version },
    signal,
    decode: parseJob
  })
export const deleteDocument = (doc: KnowledgeDocument, key: string, signal: AbortSignal) =>
  http.request(docPath(doc.base_id, doc.id), {
    method: 'DELETE',
    body: { expected_version: doc.version, client_request_id: key },
    signal,
    decode: parseJob
  })
export const listJobs = (id: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/jobs`, { signal, decode: list(parseJob) })
export const getJob = (id: string, signal: AbortSignal) =>
  http.request(`${root}/jobs/${encodeURIComponent(id)}`, { signal, decode: parseJob })
export const cancelJob = (id: string, signal: AbortSignal) =>
  http.request(`${root}/jobs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseJob
  })
export const queryBase = (
  id: string,
  body: { query: string; top_k: number; similarity_threshold: number; client_request_id: string },
  signal: AbortSignal
) =>
  http.request(`${basePath(id)}/query`, { method: 'POST', body, signal, decode: parsePreparation })
export const getPreparation = (id: string, signal: AbortSignal) =>
  http.request(`${root}/preparations/${encodeURIComponent(id)}`, {
    signal,
    decode: parsePreparation
  })
export const cancelPreparation = (id: string, signal: AbortSignal) =>
  http.request(`${root}/preparations/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
    body: {},
    signal,
    decode: parsePreparation
  })
export const preparationFinished = (value: KnowledgePreparation) =>
  ['admitted', 'completed', 'failed', 'cancelled', 'interrupted'].includes(value.status)
export function waitForObservation(signal: AbortSignal, delay = 500): Promise<void> {
  signal.throwIfAborted()
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer)
      reject(
        signal.reason instanceof Error
          ? signal.reason
          : new DOMException('Observation aborted', 'AbortError')
      )
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, delay)
    signal.addEventListener('abort', abort, { once: true })
  })
}
export async function observePreparation(
  initial: KnowledgePreparation,
  update: (value: KnowledgePreparation) => KnowledgePreparation | void,
  signal: AbortSignal
): Promise<KnowledgePreparation> {
  let value = update(initial) ?? initial
  while (!preparationFinished(value)) {
    await waitForBackgroundObservation(signal)
    const incoming = await getPreparation(value.id, signal)
    value = update(incoming) ?? incoming
  }
  return value
}
function parseAcl(value: unknown): KnowledgeAcl {
  const d = object(value)
  return {
    base: parseBase(d.base),
    entries: list((value): KnowledgeAclEntry => {
      const e = object(value)
      return {
        user_id: string(e.user_id),
        effect: choice(e.effect, ['allow', 'deny']),
        expires_at: nullableString(e.expires_at)
      }
    })(d.entries),
    builtin_permissions: list((value) => {
      const e = object(value)
      return {
        source: string(e.source),
        enabled: boolean(e.enabled),
        deny_overridable: boolean(e.deny_overridable)
      }
    })(d.builtin_permissions)
  }
}
export const getBaseAcl = (id: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/acl`, { signal, decode: parseAcl })
export const cleanupKnowledgeSharing = (
  id: string,
  expectedAclVersion: number,
  signal: AbortSignal
) =>
  http.request(`${basePath(id)}/sharing/cleanup`, {
    method: 'POST',
    body: { expected_acl_version: expectedAclVersion },
    signal,
    decode: parseAcl
  })

export async function downloadDocument(
  baseId: string,
  documentId: string,
  signal: AbortSignal
): Promise<Blob> {
  const response = await http.open(`${docPath(baseId, documentId)}/download`, { signal })
  const blob = await response.blob()
  signal.throwIfAborted()
  return blob
}
export const addChatAttachment = (
  baseId: string,
  body: { file_id: string; client_request_id: string },
  signal: AbortSignal
) =>
  http.request(`${basePath(baseId)}/attachments`, {
    method: 'POST',
    body,
    signal,
    decode: (value) => {
      const d = object(value)
      return {
        document: parseDocument(d.document),
        job: parseJob(d.job),
        already_exists: boolean(d.already_exists)
      }
    }
  })
export interface MigrationPreview {
  base_id: string
  document_policy: 'legacy' | 'library'
  expected_version: number
  acl_version: number
  sharing: { access: Access; entries: KnowledgeAclEntry[] }
  documents: {
    document_id: string
    file_name: string
    can_migrate: boolean
    reason?: string | undefined
  }[]
  requires_acl_cleanup: boolean
  can_migrate: boolean
}
export const getMigration = (baseId: string, signal: AbortSignal) =>
  http.request(`${basePath(baseId)}/migration`, {
    signal,
    decode: (value) => {
      const d = object(value),
        sharing = object(d.sharing)
      return {
        base_id: string(d.base_id),
        document_policy: choice(d.document_policy, ['legacy', 'library']),
        expected_version: number(d.expected_version),
        acl_version: number(d.acl_version),
        sharing: {
          access: choice(sharing.access, ['private', 'public']),
          entries: list((value) => {
            const e = object(value)
            return {
              user_id: string(e.user_id),
              effect: choice(e.effect, ['allow', 'deny']),
              expires_at: nullableString(e.expires_at)
            }
          })(sharing.entries)
        },
        documents: list((value) => {
          const doc = object(value)
          return {
            document_id: string(doc.document_id),
            file_name: string(doc.file_name),
            can_migrate: boolean(doc.can_migrate),
            reason: typeof doc.reason === 'string' ? doc.reason : undefined
          }
        })(d.documents),
        requires_acl_cleanup: boolean(d.requires_acl_cleanup),
        can_migrate: boolean(d.can_migrate)
      } satisfies MigrationPreview
    }
  })
export const migrateBase = (
  id: string,
  body: {
    expected_version: number
    expected_acl_version: number
    confirm_shared_access: true
    client_request_id: string
  },
  signal: AbortSignal
) => http.request(`${basePath(id)}/migration`, { method: 'POST', body, signal, decode: parseJob })
export const baseReferences = (id: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/references`, {
    signal,
    decode: (value) => {
      const d = object(value)
      return {
        items: list((value) => {
          const row = object(value)
          return {
            name:
              typeof row.name === 'string'
                ? row.name
                : typeof row.title === 'string'
                  ? row.title
                  : '关联资源',
            resource_type: typeof row.resource_type === 'string' ? row.resource_type : '',
            path:
              typeof row.path === 'string' && row.path.startsWith('/') && !row.path.startsWith('//')
                ? row.path
                : null
          }
        })(d.items),
        hidden_count: number(d.hidden_count),
        total: number(d.total)
      }
    }
  })

export interface KnowledgeSettingsInput {
  name?: string
  description?: string
  expected_version: number
  embed_model_id: string
  rerank_model_id: string | null
  use_default_rerank: boolean
  chunk_size: number
  chunk_overlap: number
  confirm_rebuild: boolean
  client_request_id: string
}
export const configureKnowledge = (id: string, body: KnowledgeSettingsInput, signal: AbortSignal) =>
  http.request(`${basePath(id)}/settings`, {
    method: 'PATCH',
    body,
    signal,
    decode: (value) => {
      const result = object(value)
      return {
        base: parseBase(result.base),
        job: result.job === null ? null : parseJob(result.job)
      }
    }
  })
export const getUploadReceipt = (id: string, requestId: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/uploads/requests/${encodeURIComponent(requestId)}`, {
    signal,
    decode: (value) => {
      const result = object(value)
      return { file: parseManagedFile(result.file), upload_id: string(result.upload_id) }
    }
  })
export interface KnowledgeDefaults {
  default_embed_model_id: string | null
  default_rerank_model_id: string | null
  chunk_size: number
  chunk_overlap: number
  version: number
  updated_at: string
}
const parseDefaults = (value: unknown): KnowledgeDefaults => {
  const d = object(value)
  return {
    default_embed_model_id: nullableString(d.default_embed_model_id),
    default_rerank_model_id: nullableString(d.default_rerank_model_id),
    chunk_size: number(d.chunk_size),
    chunk_overlap: number(d.chunk_overlap),
    version: number(d.version),
    updated_at: string(d.updated_at)
  }
}
export const getKnowledgeDefaults = (signal: AbortSignal) =>
  http.request('/knowledge/settings', { signal, decode: parseDefaults })
export const updateKnowledgeDefaults = (
  body: Omit<KnowledgeDefaults, 'version' | 'updated_at'> & { expected_version: number },
  signal: AbortSignal
) => http.request('/knowledge/settings', { method: 'PATCH', body, signal, decode: parseDefaults })

export interface KnowledgeInvitation {
  id: string
  status: 'active' | 'used' | 'expired' | 'revoked'
  expires_at: string
  created_at: string
  accepted_at: string | null
}
export interface KnowledgeMember {
  id: string
  name: string
  active: boolean
}
export interface KnowledgeInvitationPreview {
  base_id: string
  base_name: string
  owner_name: string
  expires_at: string
  already_member: boolean
}
function parseInvitation(value: unknown): KnowledgeInvitation {
  const d = object(value)
  return {
    id: string(d.id),
    status: choice(d.status, ['active', 'used', 'expired', 'revoked']),
    expires_at: string(d.expires_at),
    created_at: string(d.created_at),
    accepted_at: nullableString(d.accepted_at)
  }
}
function parseInvitationLink(value: unknown) {
  const d = object(value)
  return { invitation: parseInvitation(d.invitation), token: string(d.token) }
}
export const listKnowledgeInvitations = (id: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/invitations`, { signal, decode: list(parseInvitation) })
export const createKnowledgeInvitation = (
  id: string,
  body: { expected_acl_version: number; client_request_id: string },
  signal: AbortSignal
) =>
  http.request(`${basePath(id)}/invitations`, {
    method: 'POST',
    body,
    signal,
    decode: parseInvitationLink
  })
export const getKnowledgeInvitationLink = (id: string, invitationId: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/invitations/${encodeURIComponent(invitationId)}/link`, {
    signal,
    decode: parseInvitationLink
  })
export const revokeKnowledgeInvitation = (id: string, invitationId: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/invitations/${encodeURIComponent(invitationId)}/revoke`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseInvitation
  })
export const listKnowledgeMembers = (id: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/members`, {
    signal,
    decode: list((value): KnowledgeMember => {
      const d = object(value)
      return { id: string(d.id), name: string(d.name), active: boolean(d.active) }
    })
  })
export const removeKnowledgeMember = (
  id: string,
  userId: string,
  version: number,
  signal: AbortSignal
) =>
  http.request(`${basePath(id)}/members/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
    body: { expected_acl_version: version },
    signal,
    decode: decodeVoid
  })
export const previewKnowledgeInvitation = (token: string, signal: AbortSignal) =>
  http.request('/knowledge/invitations/preview', {
    method: 'POST',
    body: { token },
    signal,
    decode: (value): KnowledgeInvitationPreview => {
      const d = object(value)
      return {
        base_id: string(d.base_id),
        base_name: string(d.base_name),
        owner_name: string(d.owner_name),
        expires_at: string(d.expires_at),
        already_member: boolean(d.already_member)
      }
    }
  })
export const acceptKnowledgeInvitation = (token: string, signal: AbortSignal) =>
  http.request('/knowledge/invitations/accept', {
    method: 'POST',
    body: { token },
    signal,
    decode: (value) => {
      const d = object(value)
      return { base_id: string(d.base_id), already_member: boolean(d.already_member) }
    }
  })

export const getPendingUploads = (id: string, folderId: string | null, signal: AbortSignal) =>
  http.request(
    `${basePath(id)}/uploads${folderId ? `?folder_id=${encodeURIComponent(folderId)}` : ''}`,
    {
      signal,
      decode: (value) => {
        const data = object(value)
        return {
          total: number(data.total),
          items: list((value) => {
            const item = object(value)
            return {
              requestId: string(item.request_id),
              uploadId: string(item.upload_id),
              file: parseManagedFile(item.file)
            }
          })(data.items)
        }
      }
    }
  )
