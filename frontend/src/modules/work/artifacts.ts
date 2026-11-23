import type { WorkArtifact, WorkInput } from './api/index.ts'

export type WorkFile =
  | (WorkArtifact & { kind?: 'artifact' })
  | (WorkInput & { kind: 'input'; id: string })
export type PreviewKind = 'image' | 'pdf' | 'markdown' | 'text' | ''

export function previewKind(mime: string, name = ''): PreviewKind {
  mime = mime.split(';')[0]!.trim().toLowerCase()
  if (/^image\/(png|jpeg|webp|gif)$/.test(mime)) return 'image'
  if (mime === 'application/pdf') return 'pdf'
  if (/\.(md|markdown)$/i.test(name) || mime === 'text/markdown') return 'markdown'
  if (mime.startsWith('text/') || /\.(txt|csv|json)$/i.test(name)) return 'text'
  return ''
}

export function canPreviewArtifact(item: WorkArtifact): boolean {
  return !!item.previewFileId || !!previewKind(item.mime, item.name)
}

/** Keep a reply's latest publication per path; later runs never replace its files. */
export function artifactsByRun(items: readonly WorkArtifact[]): Map<string, WorkArtifact[]> {
  const runs = new Map<string, Map<string, WorkArtifact>>()
  for (const item of items) {
    let files = runs.get(item.runId)
    if (!files) runs.set(item.runId, (files = new Map<string, WorkArtifact>()))
    const previous = files.get(item.logicalPath)
    if (!previous || item.version > previous.version) files.set(item.logicalPath, item)
  }
  return new Map(
    [...runs].map(([runId, files]) => [
      runId,
      [...files.values()].sort((a, b) =>
        a.logicalPath.localeCompare(b.logicalPath, undefined, { numeric: true })
      )
    ])
  )
}

/** Resolve registered files only. External links keep their original destination. */
export function artifactReference(
  href: string,
  items: readonly WorkArtifact[],
  taskId: string
): WorkArtifact | undefined {
  let path: string
  try {
    path = decodeURIComponent(href)
  } catch {
    return undefined
  }
  const prefix = `/api/work/tasks/${taskId}/artifacts/`
  if (path.startsWith(prefix)) {
    return items.find((item) =>
      ['download', 'preview'].some((action) => path === `${prefix}${item.id}/${action}`)
    )
  }
  if (/^(https?:|\/\/|data:|javascript:|file:)/i.test(path)) return undefined
  path = path.replace(/^sandbox:/, '').replace(/^\.\//, '')
  path = path.replace(/^\/(?:mnt\/data|workspace|app\/workspace)\//, '')
  if (path.startsWith('/outputs/')) path = path.slice(1)
  if (path.split('/').includes('..') || /[?#\\]/.test(path)) return undefined
  const exact = items.filter((item) => item.logicalPath === path)
  if (exact.length) return exact.reduce((a, b) => (a.version >= b.version ? a : b))
  if (path.includes('/')) return undefined
  const matches = items.filter((item) => item.name === path)
  const paths = new Set(matches.map((item) => item.logicalPath))
  return paths.size === 1 ? matches.reduce((a, b) => (a.version >= b.version ? a : b)) : undefined
}

// Fenced and inline code are intentionally left untouched.
const markdownTokens =
  /^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)[\s\S]*?(?:^ {0,3}\1[ \t]*(?:\n|$)|(?![\s\S]))|(`+)[\s\S]*?(?:\2|(?![\s\S]))|!?\[([^\]\n]*)\]\(<?([^\s<>)]*)>?(?:\s+"[^"\n]*")?\)|\*\*([^*\n]+)\*\*/gm

export function formatArtifactLinks(
  content: string,
  items: readonly WorkArtifact[],
  taskId: string,
  linkPrefix: string
): string {
  return content.replace(
    markdownTokens,
    (
      original: string,
      fence: string | undefined,
      code: string | undefined,
      label: string,
      href: string | undefined,
      filename: string | undefined
    ) => {
      if (fence || code || (!href && !filename)) return original
      const item = artifactReference(href ?? filename ?? '', items, taskId)
      if (!item) return original
      const title = `预览 ${item.name}`.replaceAll('"', '').replace(/[\r\n\\]/g, ' ')
      return `[${filename ? `**${filename}**` : label || item.name}](${linkPrefix}${encodeURIComponent(item.id)} "${title}")`
    }
  )
}
