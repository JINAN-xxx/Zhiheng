import { readonly, ref } from 'vue'
import type { InjectionKey } from 'vue'
import type { CollectionQuery } from './api/index.ts'

function cloneReadValue<T>(value: T): T {
  const copy: unknown = JSON.parse(JSON.stringify(value))
  return copy as T
}

export interface CollectionSnapshot<T> {
  rows: T[]
  total: number
  search: string
  page: number
  filters: CollectionQuery
  hasData: boolean
  capturedAt: number
}
export interface ResourceSnapshot<T> {
  value: T
  capturedAt: number
}
export interface ReadStamp {
  generation: number
  revision: number
}

export function createAdminReadCache(owner: string, lifetime = 15000) {
  const generation = ref(0)
  const collections = new Map<string, CollectionSnapshot<unknown>>()
  const resources = new Map<string, ResourceSnapshot<unknown>>()
  let revision = 0,
    active = true
  function limit<T>(values: Map<string, T>) {
    while (values.size > 32) {
      const first = values.keys().next().value
      if (first === undefined) break
      values.delete(first)
    }
  }
  function invalidate() {
    if (!active) return
    revision += 1
    for (const value of collections.values()) value.capturedAt = 0
    for (const value of resources.values()) value.capturedAt = 0
  }
  function stamp(): ReadStamp {
    return { generation: generation.value, revision }
  }
  function valid(value: ReadStamp) {
    return active && value.generation === generation.value
  }
  return {
    generation: readonly(generation),
    stamp,
    invalidate,
    isFresh(value: { capturedAt: number } | undefined) {
      const age = value ? Date.now() - value.capturedAt : Infinity
      return !!value && value.capturedAt > 0 && age >= 0 && age < lifetime
    },
    removeCollection(key: string) {
      collections.delete(key)
    },
    removeResource(key: string) {
      resources.delete(key)
    },
    getCollection<T>(key: string): CollectionSnapshot<T> | undefined {
      const value = collections.get(key)
      if (!value || !active) return undefined
      collections.delete(key)
      collections.set(key, value)
      return cloneReadValue(value) as CollectionSnapshot<T>
    },
    setCollection<T>(key: string, value: CollectionSnapshot<T>, origin: ReadStamp) {
      if (!valid(origin)) return
      const copy = cloneReadValue(value)
      if (origin.revision !== revision) copy.capturedAt = 0
      collections.delete(key)
      collections.set(key, copy as CollectionSnapshot<unknown>)
      limit(collections)
    },
    getResource<T>(key: string): ResourceSnapshot<T> | undefined {
      const value = resources.get(key)
      return value && active ? (cloneReadValue(value) as ResourceSnapshot<T>) : undefined
    },
    setResource<T>(key: string, value: T, origin: ReadStamp, capturedAt = Date.now()) {
      if (!valid(origin)) return
      resources.set(key, {
        value: cloneReadValue(value),
        capturedAt: origin.revision === revision ? capturedAt : 0
      })
      limit(resources)
    },
    reset(nextOwner: string) {
      if (owner === nextOwner || !active) return
      owner = nextOwner
      generation.value += 1
      revision += 1
      collections.clear()
      resources.clear()
    },
    dispose() {
      active = false
      generation.value += 1
      collections.clear()
      resources.clear()
    }
  }
}
export type AdminReadCache = ReturnType<typeof createAdminReadCache>
export const adminReadCacheKey: InjectionKey<AdminReadCache> = Symbol('admin-read-cache')
