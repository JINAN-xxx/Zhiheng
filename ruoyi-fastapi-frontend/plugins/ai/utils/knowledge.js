const text = (value) => value == null ? '' : String(value)
const array = (value) => Array.isArray(value) ? value : []
const KNOWLEDGE_VIEWS = new Set(['documents', 'query', 'graph'])

export function normalizeKnowledgeView(view, graphEnabled = false) {
  const normalized = text(view)
  if (!KNOWLEDGE_VIEWS.has(normalized)) return 'documents'
  return normalized === 'graph' && !graphEnabled ? 'documents' : normalized
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
  return ['chroma', 'milvus', 'lightrag'].filter(value => types[value]).map(value => ({
    value, label: ({ chroma: 'Chroma', milvus: 'Milvus', lightrag: 'LightRAG' })[value],
    available: types[value].available === true, disabled: types[value].available !== true,
    description: text(types[value].description),
  }))
}

export function buildKnowledgeDatabasePayload(form) {
  const database_name = text(form.database_name).trim()
  if (!database_name || database_name.length > 100) throw new Error('请输入 1–100 字的知识库名称')
  if (!['chroma', 'milvus', 'lightrag'].includes(form.kb_type)) throw new Error('请选择可用的存储类型')
  if (!form.embed_model_id) throw new Error('请选择 Embedding 模型')
  const enable_knowledge_graph = form.enable_knowledge_graph === true || form.kb_type === 'lightrag'
  if (enable_knowledge_graph && !form.llm_model_id) throw new Error('开启图谱需要选择对话模型')
  return { database_name, description: text(form.description).trim(), kb_type: form.kb_type,
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
