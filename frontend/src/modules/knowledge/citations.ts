import type { KnowledgeHit } from './api/index.ts'

export interface KnowledgeSourceDocument {
  key: string
  number: number
  source: KnowledgeHit
  hits: KnowledgeHit[]
}

export function groupKnowledgeSources(sources: readonly KnowledgeHit[]): KnowledgeSourceDocument[] {
  const documents = new Map<string, KnowledgeSourceDocument>()
  for (const hit of sources) {
    const key = JSON.stringify([hit.knowledge_base_id, hit.document_id])
    let document = documents.get(key)
    if (!document) {
      document = { key, number: documents.size + 1, source: hit, hits: [] }
      documents.set(key, document)
    }
    if (!document.hits.some((value) => value.chunk_id === hit.chunk_id)) document.hits.push(hit)
  }
  return [...documents.values()]
}

// Preserve code, explicit Markdown links and reference definitions while formatting prose.
const protectedMarkdown =
  /^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)[\s\S]*?(?:^ {0,3}\1[ \t]*(?:\n|$)|(?![\s\S]))|(`+)[\s\S]*?(?:\2|(?![\s\S]))|!?\[(?:\\.|[^\]\\\n])*\](?:\([^\n)]*\)|\[[^\]\n]*\])|^ {0,3}\[[^\]\n]+\]:[^\n]*/gm

export function formatKnowledgeCitations(
  content: string,
  sources: readonly KnowledgeHit[],
  options: { linkPrefix?: string; loading?: boolean } = {}
): string {
  if (!sources.length || !content) return content
  const documents = groupKnowledgeSources(sources)
  const aliases = new Map<string, number | null>()
  const references = new Set(
    [...content.matchAll(/^ {0,3}\[([^\]\n]+)\]:/gm)].map((match) => match[1]!.toLowerCase())
  )
  for (const document of documents) {
    aliases.set(String(document.number), document.number)
    for (const hit of document.hits) {
      for (const value of [hit.source_id, hit.document_id, hit.file_id, hit.chunk_id]) {
        const key = value.toLowerCase()
        aliases.set(
          key,
          aliases.has(key) && aliases.get(key) !== document.number ? null : document.number
        )
      }
    }
  }
  function prose(value: string, trailing: boolean): string {
    let text = value
    if (trailing && options.loading) {
      const pending = /(?<!\\)\[([^\]\n]*)$/.exec(text)
      if (pending && [...aliases.keys()].some((key) => key.startsWith(pending[1]!.toLowerCase()))) {
        text = text.slice(0, pending.index)
      }
    }
    return text.replace(/(?<!\\)\[([^\]\n]+)\]/g, (original, identifier: string) => {
      const key = identifier.trim().toLowerCase()
      const number = aliases.get(key)
      if (!number || references.has(key)) return original
      if (!options.linkPrefix) return `[${number}]`
      const title = documents[number - 1]!.source.title.replace(/\\/g, '\\\\')
        .replace(/"/g, '\\"')
        .replace(/[\r\n]/g, ' ')
      return `[\\[${number}\\]](${options.linkPrefix}${number} "查看来源 ${number}：${title}")`
    })
  }
  let result = '',
    start = 0
  for (const match of content.matchAll(protectedMarkdown)) {
    result += prose(content.slice(start, match.index), false) + match[0]
    start = match.index + match[0].length
  }
  return result + prose(content.slice(start), true)
}
