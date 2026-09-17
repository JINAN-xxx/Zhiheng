import request from '@/utils/request'
import { buildKnowledgeGraphParams } from '../utils/knowledge.js'

const prefix = '/ai/knowledge'
const id = value => encodeURIComponent(String(value))
const database = dbId => `${prefix}/databases/${id(dbId)}`
const document = (dbId, docId) => `${database(dbId)}/documents/${id(docId)}`
const slowOperation = { timeout: 600000 }

export const listKnowledgeBases = () => request({ url: `${prefix}/databases`, method: 'get' })
export const listAvailableKnowledgeBases = () => request({ url: `${prefix}/available`, method: 'get' })
export const listKnowledgeModels = modelType => request({ url: `${prefix}/models`, method: 'get', params: { model_type: modelType } })
export const listKnowledgeTypes = () => request({ url: `${prefix}/types`, method: 'get' })
export const createKnowledgeBase = data => request({ url: `${prefix}/databases`, method: 'post', data, ...slowOperation })
export const getKnowledgeBase = dbId => request({ url: database(dbId), method: 'get' })
export const updateKnowledgeBase = (dbId, data) => request({ url: database(dbId), method: 'put', data })
export const deleteKnowledgeBase = dbId => request({ url: database(dbId), method: 'delete', ...slowOperation })
export const listKnowledgeFolders = dbId => request({ url: `${prefix}/folders`, method: 'get', params: { db_id: dbId } })
export const createKnowledgeFolder = data => request({ url: `${prefix}/folders`, method: 'post', data })
export const renameKnowledgeFolder = (dbId, folderId, name) => request({ url: `${prefix}/folders/${id(folderId)}`, method: 'put', params: { db_id: dbId }, data: { name } })
export const deleteKnowledgeFolder = (dbId, folderId) => request({ url: `${prefix}/folders/${id(folderId)}`, method: 'delete', params: { db_id: dbId }, ...slowOperation })
export const listKnowledgeDocuments = dbId => request({ url: `${database(dbId)}/documents`, method: 'get' })
export const uploadKnowledgeFile = (dbId, file, folderId, onUploadProgress, signal) => {
  const data = new FormData()
  data.append('file', file)
  return request({ url: `${prefix}/files/upload`, method: 'post', params: { db_id: dbId, ...(folderId ? { folder_id: folderId } : {}) }, data,
    headers: { 'Content-Type': 'multipart/form-data', repeatSubmit: false }, onUploadProgress, signal, ...slowOperation })
}
export const indexKnowledgeDocuments = (dbId, uploadIds, params, signal) => request({ url: `${database(dbId)}/documents`, method: 'post', data: { items: uploadIds, params: { content_type: 'file', ...params } }, signal, ...slowOperation })
export const previewKnowledgeDocument = (dbId, docId) => request({ url: `${document(dbId, docId)}/preview`, method: 'get', ...slowOperation })
export const deleteKnowledgeDocument = (dbId, docId) => request({ url: document(dbId, docId), method: 'delete', ...slowOperation })
export const rechunkKnowledgeDocument = (dbId, docId, data) => request({ url: `${document(dbId, docId)}/rechunk`, method: 'post', data, ...slowOperation })
export const queryKnowledgeBase = (dbId, query, meta = {}, signal) => request({ url: `${database(dbId)}/query`, method: 'post', data: { query, meta }, signal, ...slowOperation })
export const queryKnowledgeGraph = (dbId, query, meta = {}, signal) => request({ url: `${database(dbId)}/query-graph`, method: 'post', data: { query, meta }, signal, ...slowOperation })
export const listKnowledgeGraphLabels = dbId => request({ url: `${prefix}/graphs/${id(dbId)}/labels`, method: 'get', ...slowOperation })
export const getKnowledgeSubgraph = (dbId, params) => request({ url: `${prefix}/graphs/${id(dbId)}/subgraph`, method: 'get', params: buildKnowledgeGraphParams(params), ...slowOperation })

export const getKnowledgeGraphBuild = (dbId, signal) => request({ url: `${database(dbId)}/graph-build`, method: 'get', signal })
export const startKnowledgeGraphBuild = (dbId, data, signal) => request({ url: `${database(dbId)}/graph-build`, method: 'post', data, signal, headers: { repeatSubmit: false } })
