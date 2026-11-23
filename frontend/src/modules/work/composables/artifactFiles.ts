import { computed } from 'vue'
import { canUseModule, identity } from '../../identity/public.ts'
import { attachmentFile, makeFilePreview } from '../../../shared/messageFiles.ts'
import type { MessageFile } from '../../../shared/messageFiles.ts'
import { downloadArtifact } from '../api/index.ts'
import type { WorkArtifact } from '../api/index.ts'
import { canPreviewArtifact, previewKind } from '../artifacts.ts'

export function workArtifactFile(item: WorkArtifact): MessageFile {
  return {
    ...attachmentFile({ ...item, file_id: item.id, original_filename: item.name }),
    detail: `v${item.version}`
  }
}

export function useWorkArtifactFiles(
  taskId: () => string,
  find: (id: string) => WorkArtifact | undefined
) {
  const accessKey = computed(() =>
    identity.value && canUseModule('work') ? `${identity.value.id}:${taskId()}` : null
  )
  function readFile(id: string, signal: AbortSignal): Promise<Blob> {
    return downloadArtifact(taskId(), id, signal)
  }
  async function readPreview(id: string, signal: AbortSignal) {
    const item = find(id)
    if (!item || !canPreviewArtifact(item)) return null
    const blob = await downloadArtifact(taskId(), id, signal, true)
    return makeFilePreview(previewKind(blob.type, item.previewFileId ? '' : item.name), blob)
  }
  return { accessKey, readFile, readPreview }
}
