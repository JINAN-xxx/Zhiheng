import type { ApiDocument } from './apiDocs.ts'

export type ApiDocNavigationNode =
  | { kind: 'link'; id: string; label: string; anchor: string; method?: string }
  | { kind: 'folder'; id: string; label: string; children: ApiDocNavigationNode[] }

type Category = readonly [id: string, label: string]

function categories(item: ApiDocument): Category[] {
  const path = item.path
  if (/^\/api\/v1\/knowledge-bases(?:\/|$)/.test(path)) {
    const section: Category = path.includes('/folders')
      ? ['knowledge-folders', '目录管理']
      : /\/(query|rebuild)$/.test(path)
        ? ['knowledge-search', '检索与索引']
        : path.includes('/documents')
          ? ['knowledge-documents', '文档与入库']
          : ['knowledge-bases', '库管理']
    return [['knowledge', '知识库'], section]
  }
  if (/^\/api\/v1\/(skills|skill-imports)(?:\/|$)/.test(path)) {
    const section: Category = path.startsWith('/api/v1/skill-imports')
      ? ['skill-imports', '导入与确认']
      : path.includes('/versions')
        ? ['skill-versions', '版本与原包']
        : ['skill-management', '技能管理']
    return [['skills', '个人技能'], section]
  }
  if (/^\/api\/v1\/files(?:\/|$)/.test(path))
    return [
      ['files', '文件资源'],
      item.method === 'DELETE' || path.endsWith('/restore')
        ? ['file-lifecycle', '回收与恢复']
        : ['file-content', '上传与读取']
    ]
  if (/^\/api\/v1\/operations(?:\/|$)/.test(path)) return [['operations', '异步操作']]
  if (path.startsWith('/v1/agents/')) return [['agents', '智能体调用']]
  if (/^\/v1\/(models|chat|embeddings|rerank)(?:\/|$)/.test(path)) return [['models', '模型调用']]
  return [['other', '其他接口']]
}

export function buildApiDocNavigation(
  documents: ApiDocument[],
  group: 'resources' | 'inference'
): ApiDocNavigationNode[] {
  const guides = [
    { anchor: 'api-overview', label: '接入说明' },
    { anchor: 'api-auth', label: '鉴权方式' },
    { anchor: 'api-protocol', label: '版本与错误' },
    { anchor: 'api-recipes', label: '资源接入顺序' },
    ...(group === 'inference' ? [{ anchor: 'api-streaming', label: '流式调用' }] : [])
  ]
  const result: ApiDocNavigationNode[] = [
    {
      kind: 'folder',
      id: 'guide',
      label: '接入指南',
      children: guides.map((item) => ({ kind: 'link', id: item.anchor, ...item }))
    }
  ]
  for (const item of documents) {
    let siblings = result
    for (const [id, label] of categories(item)) {
      let folder = siblings.find((node) => node.kind === 'folder' && node.id === id)
      if (!folder) {
        folder = { kind: 'folder', id, label, children: [] }
        siblings.push(folder)
      }
      if (folder.kind === 'folder') siblings = folder.children
    }
    siblings.push({
      kind: 'link',
      id: `api-${item.id}`,
      anchor: `api-${item.id}`,
      label: item.title,
      method: item.method
    })
  }
  const order = ['guide', 'knowledge', 'skills', 'files', 'operations', 'models', 'agents', 'other']
  return result.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id))
}

export function apiDocAncestorIds(nodes: ApiDocNavigationNode[], anchor: string): string[] {
  function find(items: ApiDocNavigationNode[]): string[] | undefined {
    for (const node of items) {
      if (node.kind === 'link' && node.anchor === anchor) return []
      if (node.kind === 'folder') {
        const path = find(node.children)
        if (path) return [node.id, ...path]
      }
    }
    return undefined
  }
  return find(nodes) ?? []
}
