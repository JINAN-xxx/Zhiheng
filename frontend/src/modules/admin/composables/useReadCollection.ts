import { inject, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError, RequestScope, isAborted } from '../../../shared/http.ts'
import { getAllCollection, getCollection } from '../api/index.ts'
import type { CollectionQuery, Decoder } from '../api/index.ts'
import { adminReadCacheKey } from '../readCache.ts'
import { adminError } from './errors.ts'
export function useReadCollection<T>(
  endpoint: string,
  decode: Decoder<T>,
  initial: CollectionQuery = {},
  all = false
) {
  const cache = inject(adminReadCacheKey, null)
  const route = useRoute()
  const cacheKey = `${route.path}:${endpoint}:${all}`
  const restored = cache?.getCollection<T>(cacheKey)
  if (restored)
    for (const key of Object.keys(initial))
      if (key in restored.filters) initial[key] = restored.filters[key]
  const rows = shallowRef<T[]>(restored?.rows ?? []),
    total = ref(restored?.total ?? 0),
    page = ref(restored?.page ?? 1),
    search = ref(restored?.search ?? ''),
    loading = ref(!restored?.hasData),
    error = ref('')
  const scope = new RequestScope()
  let hasData = restored?.hasData ?? false,
    capturedAt = restored?.capturedAt ?? 0
  let lastStamp = cache?.stamp()
  function query() {
    return { ...initial, search: search.value.trim(), page: page.value, page_size: 20 }
  }
  let loadedQuery = hasData ? JSON.stringify(query()) : ''
  function remember() {
    if (!cache || !lastStamp) return
    const matches = hasData && loadedQuery === JSON.stringify(query())
    cache.setCollection(
      cacheKey,
      {
        rows: matches ? rows.value : [],
        total: matches ? total.value : 0,
        page: page.value,
        search: search.value,
        filters: { ...initial },
        hasData: matches,
        capturedAt: matches ? capturedAt : 0
      },
      lastStamp
    )
  }
  async function load(): Promise<void> {
    const ticket = scope.replace(),
      requested = query(),
      stamp = cache?.stamp()
    loading.value = true
    error.value = ''
    try {
      const result = all
        ? await getAllCollection(endpoint, decode, requested, ticket.signal).then((items) => ({
            items,
            total: items.length
          }))
        : await getCollection(endpoint, decode, requested, ticket.signal)
      if (!ticket.isCurrent()) return
      rows.value = result.items
      total.value = result.total
      hasData = true
      capturedAt = Date.now()
      loadedQuery = JSON.stringify(requested)
      lastStamp = stamp
      if (page.value > 1 && !result.items.length) {
        page.value -= 1
        await load()
        return
      }
      remember()
    } catch (failure) {
      if (ticket.isCurrent() && !isAborted(failure)) {
        error.value = adminError(failure)
        capturedAt = 0
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
          rows.value = []
          total.value = 0
          hasData = false
          loadedQuery = ''
          cache?.removeCollection(cacheKey)
        }
      }
    } finally {
      if (ticket.isCurrent()) loading.value = false
    }
  }
  function find(value: string) {
    search.value = value
    page.value = 1
    void load()
  }
  function changePage(value: number) {
    page.value = value
    void load()
  }
  onMounted(() => {
    if (!restored?.hasData || !cache?.isFresh(restored)) void load()
  })
  onBeforeUnmount(() => {
    remember()
    scope.dispose()
  })
  return { rows, total, page, search, loading, error, load, find, changePage }
}
