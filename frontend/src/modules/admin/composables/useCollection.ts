import { onBeforeUnmount, ref } from 'vue'
import { createEntity, deleteEntity, updateEntity } from '../api/index.ts'
import type { CollectionQuery, Decoder, Entity } from '../api/index.ts'
import { adminError } from './errors.ts'
import { useReadCollection } from './useReadCollection.ts'
export { adminError } from './errors.ts'
export function useCollection<T extends Entity, Payload extends object>(
  endpoint: string,
  decode: Decoder<T>,
  initial: CollectionQuery = {},
  all = false
) {
  const collection = useReadCollection(endpoint, decode, initial, all)
  const saving = ref(false)
  let alive = true
  async function save(entity: T | null, body: Payload): Promise<boolean> {
    if (saving.value) return false
    saving.value = true
    try {
      if (entity) await updateEntity(endpoint, entity, body, decode)
      else await createEntity(endpoint, body, decode)
      if (alive) void collection.load()
      return true
    } finally {
      if (alive) saving.value = false
    }
  }
  async function remove(entity: T): Promise<void> {
    if (saving.value) return
    saving.value = true
    try {
      await deleteEntity(endpoint, entity)
      if (alive) await collection.load()
    } catch (failure) {
      if (alive) collection.error.value = adminError(failure)
    } finally {
      if (alive) saving.value = false
    }
  }
  onBeforeUnmount(() => {
    alive = false
  })
  return { ...collection, saving, save, remove }
}
