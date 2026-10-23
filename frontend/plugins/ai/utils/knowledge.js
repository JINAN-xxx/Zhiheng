const text = (value) => value == null ? '' : String(value)
const array = (value) => Array.isArray(value) ? value : []
const KNOWLEDGE_VIEWS = new Set(['documents', 'query', 'graph'])

export function normalizeKnowledgeView(view) {
  const normalized = text(view)
  if (!KNOWLEDGE_VIEWS.has(normalized)) return 'documents'
  return normalized
}

export function buildKnowledgeLocation(dbId, view = 'documents') {
  return {
    path: '/ai/knowledge',
    query: { dbId: text(dbId), view: normalizeKnowledgeView(view, true) },
  }
}

export function unwrapKnowledgeResponse(response) {
  if (response?.code != null && Number(response.code) !== 200) throw new Error(text(response.msg || response.message || '知识库请求失败'))
  const data = response?.code != null ? response.data : response
  if (!data || typeof data !== 'object') throw new Error('知识库响应无效')
  if (data.status === 'failed' || data.status === 'error' || data.success === false) throw new Error(text(data.message || data.error || '知识库操作失败'))
  const failed = array(data.items).filter(item => item.status === 'failed' || item.status === 'error')
  if (failed.length) throw new Error(failed.map(item => text(item.error || item.message || '文件索引失败')).join('；'))
  return data
}

export function normalizeKnowledgeDatabases(response) {
  return array(unwrapKnowledgeResponse(response).databases).filter(row => row?.db_id).map(row => ({
    ...row, db_id: text(row.db_id), name: text(row.name || row.database_name || row.db_id),
    description: text(row.description), enable_knowledge_graph: row.enable_knowledge_graph === true || row.kb_type === 'lightrag',
  }))
}

export function normalizeKnowledgeTypes(response) {
  const types = unwrapKnowledgeResponse(response).kb_types || {}
  return ['milvus'].filter(value => types[value]).map(value => ({
    value, label: 'Milvus',
    available: types[value].available === true, disabled: types[value].available !== true,
    description: text(types[value].description),
  }))
}

export function buildKnowledgeDatabasePayload(form) {
  const database_name = text(form.database_name).trim()
  if (!database_name || database_name.length > 100) throw new Error('请输入 1–100 字的知识库名称')
  if (!form.embed_model_id) throw new Error('请选择 Embedding 模型')
  const enable_knowledge_graph = form.enable_knowledge_graph === true
  if (enable_knowledge_graph && !form.llm_model_id) throw new Error('开启图谱需要选择对话模型')
  return { database_name, description: text(form.description).trim(), kb_type: 'milvus',
    embed_model_id: form.embed_model_id, enable_knowledge_graph,
    ...(enable_knowledge_graph ? { llm_model_id: form.llm_model_id } : {}),
  }
}

export function validateChunkParams(params) {
  const chunk_size = Number(params.chunk_size)
  const chunk_overlap = Number(params.chunk_overlap)
  if (!Number.isInteger(chunk_size) || chunk_size < 100 || chunk_size > 4000) throw new Error('切片大小应为 100–4000 的整数')
  if (!Number.isInteger(chunk_overlap) || chunk_overlap < 0 || chunk_overlap > 1000 || chunk_overlap >= chunk_size) throw new Error('重叠大小应为 0–1000 的整数且小于切片大小')
  return { chunk_size, chunk_overlap }
}

export function buildKnowledgeGraphParams(form) {
  const bound = (value, fallback, min, max) => Number.isFinite(Number(value)) ? Math.max(min, Math.min(max, Math.floor(Number(value)))) : fallback
  // Keep labels literal here: the shared request client's params serializer encodes them once.
  return { node_label: text(form.node_label || '*'), max_depth: bound(form.max_depth, 2, 1, 5), max_nodes: bound(form.max_nodes, 100, 1, 200) }
}

export function normalizeKnowledgeGraph(response) {
  const data = unwrapKnowledgeResponse(response)
  const ids = new Set()
  const categories = []
  const nodes = array(data.nodes).filter(node => {
    if (!node?.id || ids.has(text(node.id))) return false
    ids.add(text(node.id)); return true
  }).map(node => {
    const name = text(node.entity_name || node.name || node.labels?.[0] || node.id)
    const type = text(node.entity_type || node.properties?.entity_type || '其他')
    if (!categories.includes(type)) categories.push(type)
    const description = Object.entries(node.properties || {}).slice(0, 12).map(([key, value]) => `${key}: ${text(typeof value === 'object' ? JSON.stringify(value) : value).slice(0, 1000)}`).join('\n')
    return { id: text(node.id), name, category: categories.indexOf(type), symbolSize: 24,
      tooltipText: `${name}\n类型: ${type}${description ? `\n${description}` : ''}` }
  })
  const links = array(data.edges).filter(edge => ids.has(text(edge.source)) && ids.has(text(edge.target))).map(edge => ({
    source: text(edge.source), target: text(edge.target), name: text(edge.type || ''), tooltipText: text(edge.type || ''),
  }))
  return { nodes, links, categories: categories.map(name => ({ name })), truncated: data.is_truncated === true }
}

export function resetKnowledgeSelection(dbId = null) {
  return { dbId, folderId: null, tab: 'documents', queryResult: null }
}

export function knowledgeFolderTrail(folders, folderId) {
  const trail = [], seen = new Set()
  let current = folderId
  while (current && !seen.has(current)) {
    seen.add(current)
    const folder = folders.find(item => item.id === current)
    if (!folder) break
    trail.unshift(folder); current = folder.parent_id
  }
  return trail
}

export function filterKnowledgeDocuments(documents, { folderId = null, search = '', time = '' } = {}) {
  const query = search.trim().toLowerCase()
  const now = new Date()
  const cutoff = time === 'today' ? new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() : time === 'week' ? Date.now() - 7 * 86400000 : time === 'month' ? Date.now() - 30 * 86400000 : 0
  return documents.filter(doc => {
    if ((doc.folder_id || null) !== folderId) return false
    if (query && !text(doc.file_name || doc.name).toLowerCase().includes(query)) return false
    if (cutoff) {
      let value = doc.created_at
      if (typeof value === 'number') value = value < 1e12 ? value * 1000 : value
      if (!(new Date(value).getTime() >= cutoff)) return false
    }
    return true
  })
}

export function normalizeKnowledgeQuery(response) {
  const { result } = unwrapKnowledgeResponse(response)
  if (typeof result === 'string') return { answer: result, items: [] }
  if (Array.isArray(result)) return { answer: '', items: result.map((item, index) => ({
    id: text(item.id || index), content: text(item.content || item.text),
    source: text(item.metadata?.source || item.source || item.title || '未知来源'),
    score: Number.isFinite(Number(item.score)) ? Number(item.score) : null,
  })) }
  return { answer: result == null ? '' : JSON.stringify(result, null, 2), items: [] }
}

const GRAPH_BUILD_STATUSES = new Set(['disabled', 'idle', 'queued', 'running', 'completed', 'failed', 'interrupted'])

export function normalizeKnowledgeGraphBuild(response) {
  // A failed job is valid data, unlike a failed knowledge operation.
  if (response?.code != null && Number(response.code) !== 200) throw new Error(text(response.msg || response.message || '图谱状态请求失败'))
  const data = response?.code != null ? response.data : response
  if (!data || !GRAPH_BUILD_STATUSES.has(data.status)) throw new Error('图谱构建状态无效')
  const count = value => Math.max(0, Math.floor(Number(value) || 0))
  return { ...data, total_documents: count(data.total_documents), processed_documents: count(data.processed_documents),
    failed_documents: count(data.failed_documents), current_document: text(data.current_document?.file_name || data.current_document?.id || (typeof data.current_document === 'string' ? data.current_document : '')),
    error: text(data.error?.message || (typeof data.error === 'string' ? data.error : '')) }
}

export function isKnowledgeGraphBuilding(job) {
  return job?.status === 'queued' || job?.status === 'running'
}

export function knowledgeGraphProgress(job) {
  if (!job?.total_documents) return job?.status === 'completed' ? 100 : 0
  return Math.min(100, Math.round((job.processed_documents || 0) / job.total_documents * 100))
}

export function buildKnowledgeGraphBuildPayload(database, modelId, options = {}) {
  if (options.rebuild || (options.requires_rebuild && !options.can_resume)) return { rebuild: true }
  if (options.config) return {}
  if (database.enable_knowledge_graph) return {}
  if (!modelId) throw new Error('启用图谱需要选择对话模型')
  return { llm_model_id: modelId }
}

export const KNOWLEDGE_GRAPH_CONFIG_DEFAULTS = Object.freeze({
  llm_model_id: '', reasoning_strategy: 'auto', timeout_seconds: 240, max_output_tokens: 16384, concurrency: 2,
})

export function buildKnowledgeGraphConfigPayload(form) {
  const value = { ...KNOWLEDGE_GRAPH_CONFIG_DEFAULTS, ...form }
  const llm_model_id = text(value.llm_model_id).trim()
  if (!llm_model_id) throw new Error('请选择图谱对话模型')
  if (!['auto', 'default', 'low', 'disabled'].includes(value.reasoning_strategy)) throw new Error('请选择有效的推理策略')
  const integer = (field, label, min, max) => {
    const number = Number(value[field])
    if (!Number.isInteger(number) || number < min || number > max) throw new Error(`${label}应为 ${min}–${max} 的整数`)
    return number
  }
  return { llm_model_id, reasoning_strategy: value.reasoning_strategy,
    timeout_seconds: integer('timeout_seconds', '响应超时秒数', 30, 600),
    max_output_tokens: integer('max_output_tokens', '最大输出长度', 1024, 32768),
    concurrency: integer('concurrency', '并行任务数', 1, 4) }
}

export function normalizeKnowledgeGraphConfig(response) {
  const data = unwrapKnowledgeResponse(response)
  return { config: data.config ? buildKnowledgeGraphConfigPayload(data.config) : null,
    active_config: data.active_config ? buildKnowledgeGraphConfigPayload(data.active_config) : null,
    active_revision: data.active_revision ? text(data.active_revision) : null,
    requires_rebuild: data.requires_rebuild === true, can_resume: data.can_resume === true }
}

export function isKnowledgeGraphReadable(database, job) {
  if (typeof job?.graph_readable === 'boolean') return job.graph_readable
  if (typeof database?.graph_readable === 'boolean') return database.graph_readable
  return database?.graph_readable === true || Boolean(job?.active_revision) || job?.status === 'completed'
}

export function normalizeKnowledgeGraphPreview(response) {
  if (response?.code != null && Number(response.code) !== 200) throw new Error(text(response.msg || response.message || '试构建状态请求失败'))
  const data = response?.code != null ? response.data : response
  if (!data || !['idle', 'queued', 'running', 'completed', 'failed', 'interrupted'].includes(data.status)) throw new Error('图谱试构建状态无效')
  const count = value => Math.max(0, Math.floor(Number(value) || 0))
  return { status: data.status, job_id: text(data.job_id), document_id: text(data.document_id), file_name: text(data.file_name),
    result: data.result ? { entities: count(data.result.entities), relations: count(data.result.relations),
      chunks: count(data.result.chunks), seconds: Math.max(0, Number(data.result.seconds) || 0) } : null,
    progress: data.progress || null, started_at: data.started_at || null, finished_at: data.finished_at || null,
    error: text(data.error?.message || (typeof data.error === 'string' ? data.error : '')),
    error_code: text(data.error?.code) }
}

export function knowledgeGraphStage(progress) {
  return ({ preparing: '准备文档', extracting: '提取实体与关系', graph_indexing: '整理图谱索引',
    embedding: '生成向量', persisting: '保存图谱', completed: '已完成', failed: '处理失败' })[progress?.stage] || ''
}

export function normalizeKnowledgeIndexResults(response, expectedCount) {
  if (response?.code != null && Number(response.code) !== 200) throw new Error(text(response.msg || response.message || '文档索引失败'))
  const data = response?.code != null ? response.data : response
  if (!Array.isArray(data?.items) || data.items.length !== expectedCount) throw new Error(text(data?.message || '文档索引结果不完整，请刷新文档列表确认'))
  return data.items.map(item => {
    if (!['done', 'failed', 'error'].includes(item?.status)) throw new Error('文档索引结果无效，请刷新文档列表确认')
    return { documentId: item.id || null, status: item.status === 'done' ? 'done' : 'failed',
      error: item.status === 'done' ? '' : text(item.error?.message || item.message || (typeof item.error === 'string' ? item.error : '文档索引失败')) }
  })
}
