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
  id: string
  name: string
  description: string
  owner_id: string
  access: Access
  version: number
  acl_version: number
  embed_model_id: string
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
export function parseBase(value: unknown): KnowledgeBase {
  const d = object(value)
  return {
    id: string(d.id),
    name: string(d.name),
    description: string(d.description),
    owner_id: string(d.owner_id),
    access: choice(d.access, ['private', 'public']),
    version: number(d.version),
    acl_version: number(d.acl_version),
    embed_model_id: string(d.embed_model_id),
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
    score: number(d.score)
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
export const listBases = (signal: AbortSignal) =>
  http.request(`${root}/bases`, { signal, decode: list(parseBase) })
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
        mineru_available: boolean(d.mineru_available)
      }
    }
  })
export interface BaseCreate {
  name: string
  description: string
  embed_model_id: string
  chunk_size: number
  chunk_overlap: number
  access: Access
  enable_knowledge_graph: false
  llm_model_id: null
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
export const deleteBase = (base: KnowledgeBase, key: string, signal: AbortSignal) =>
  http.request(basePath(base.id), {
    method: 'DELETE',
    body: { expected_version: base.version, client_request_id: key },
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
  signal: AbortSignal
) => {
  const query = new URLSearchParams({ filename: file.name })
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
export const findKnowledgeUsers = (id: string, search: string, signal: AbortSignal) =>
  http.request(`${basePath(id)}/users?${new URLSearchParams({ search })}`, {
    signal,
    decode: list((value): KnowledgeUser => {
      const d = object(value)
      return { id: string(d.id), username: string(d.username) }
    })
  })
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
export const saveBaseAcl = (
  id: string,
  body: { expected_version: number; access: Access; entries: KnowledgeAclEntry[] },
  signal: AbortSignal
) => http.request(`${basePath(id)}/acl`, { method: 'PATCH', body, signal, decode: parseAcl })
