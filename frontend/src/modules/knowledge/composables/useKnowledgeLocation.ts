import { message } from 'ant-design-vue'
import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { KnowledgeBase, KnowledgeFolder } from '../api/index.ts'

export function useKnowledgeLocation(
  selected: Ref<KnowledgeBase | null>,
  folders: Ref<KnowledgeFolder[]>
) {
  const route = useRoute()
  const router = useRouter()
  const baseId = computed(() =>
    typeof route.params.baseId === 'string' ? route.params.baseId : ''
  )
  const view = computed(() =>
    route.params.view === 'graph' ? 'graph' : route.params.view === 'query' ? 'query' : 'documents'
  )
  const folderQuery = computed(() => route.query.folder)
  const folderId = ref<string | null>(null)
  const invalidLocations = new Set<string>()

  function folderFromRoute(available: KnowledgeFolder[]): string | null {
    const value = folderQuery.value
    return typeof value === 'string' &&
      available.some((folder) => folder.id === value && folder.base_id === baseId.value)
      ? value
      : null
  }

  function folderLocation(folder: string | null) {
    const query = { ...route.query }
    if (folder) query.folder = folder
    else delete query.folder
    return { path: route.path, query, hash: route.hash }
  }

  function synchronizeFolderLocation(): boolean {
    if (selected.value?.id !== baseId.value) return false
    const previous = folderId.value
    const valid = folderFromRoute(folders.value)
    folderId.value = valid
    const value = folderQuery.value
    if (value !== undefined && !valid) {
      const key = `${baseId.value}:${JSON.stringify(value)}`
      if (!invalidLocations.has(key)) {
        invalidLocations.add(key)
        void message.warning({
          key: 'knowledge-folder-location',
          content: '目录已失效或不属于当前知识库，已返回根目录',
          duration: 3
        })
      }
      void router.replace(folderLocation(null))
    }
    return previous !== valid
  }

  async function selectFolder(id: string | null): Promise<void> {
    if (selected.value?.id !== baseId.value) return
    if (
      id !== null &&
      !folders.value.some((folder) => folder.id === id && folder.base_id === baseId.value)
    )
      return
    await router.push(folderLocation(id))
  }

  async function navigate(base: KnowledgeBase, destination: string): Promise<void> {
    const query: Record<string, string> = {}
    if (base.id === baseId.value && folderId.value) query.folder = folderId.value
    await router.push({
      path: `/knowledge/${encodeURIComponent(base.id)}/${destination === 'graph' ? 'graph' : destination === 'query' ? 'query' : 'documents'}`,
      query
    })
  }

  async function back(): Promise<void> {
    await router.push('/knowledge')
  }

  return {
    baseId,
    view,
    folderId,
    folderQuery,
    folderFromRoute,
    synchronizeFolderLocation,
    selectFolder,
    navigate,
    back
  }
}
