import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { RequestScope, isAborted } from '../../../shared/http.ts'
import { getAllCollection, getCollection } from '../api/index.ts'
import type { CollectionQuery, Decoder } from '../api/index.ts'
import { adminError } from './errors.ts'
export function useReadCollection<T>(
  endpoint: string,
  decode: Decoder<T>,
  initial: CollectionQuery = {},
  all = false
) {
  const rows = shallowRef<T[]>([]),
    total = ref(0),
    page = ref(1),
    search = ref(''),
    loading = ref(false),
    error = ref('')
  const scope = new RequestScope()
  async function load(): Promise<void> {
    const ticket = scope.replace()
    loading.value = true
    error.value = ''
    try {
      const query = { ...initial, search: search.value.trim(), page: page.value, page_size: 20 }
      const result = all
        ? await getAllCollection(endpoint, decode, query, ticket.signal).then((items) => ({
            items,
            total: items.length
          }))
        : await getCollection(endpoint, decode, query, ticket.signal)
      if (!ticket.isCurrent()) return
      rows.value = result.items
      total.value = result.total
      if (page.value > 1 && !result.items.length) {
        page.value -= 1
        await load()
      }
    } catch (failure) {
      if (ticket.isCurrent() && !isAborted(failure)) error.value = adminError(failure)
    } finally {
      if (ticket.isCurrent()) loading.value = false
    }
  }
  function find(value: string): void {
    search.value = value
    page.value = 1
    void load()
  }
  function changePage(value: number): void {
    page.value = value
    void load()
  }
  onMounted(load)
  onBeforeUnmount(() => scope.dispose())
  return { rows, total, page, search, loading, error, load, find, changePage }
}
