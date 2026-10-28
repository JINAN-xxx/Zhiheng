import { http, decodeVoid } from '../../../shared/http.ts'
import { list, object } from '../../../shared/validation.ts'
import { decodePage, decodePermission, decodeNavigation, decodeUserOptions } from './types.ts'
import type { Decoder, Entity } from './types.ts'
export * from './types.ts'
export interface CollectionQuery {
  search?: string
  page?: number
  page_size?: number
  [key: string]: string | number | undefined
}
export function collectionPath(endpoint: string, query: CollectionQuery = {}): string {
  const parameters = new URLSearchParams()
  for (const [key, value] of Object.entries(query))
    if (value !== undefined && value !== '') parameters.set(key, String(value))
  return `/admin/${endpoint}${parameters.size ? `?${parameters}` : ''}`
}
export const getCollection = <T>(
  endpoint: string,
  decode: Decoder<T>,
  query: CollectionQuery = {},
  signal?: AbortSignal
) =>
  http.request(collectionPath(endpoint, query), {
    decode: decodePage(decode),
    ...(signal ? { signal } : {})
  })
export const createEntity = <T>(endpoint: string, body: object, decode: Decoder<T>) =>
  http.request(`/admin/${endpoint}`, { method: 'POST', body, decode })
export const updateEntity = <T>(
  endpoint: string,
  entity: Entity,
  body: object,
  decode: Decoder<T>
) =>
  http.request(`/admin/${endpoint}/${encodeURIComponent(entity.id)}`, {
    method: 'PATCH',
    body: { ...body, expected_version: entity.version },
    decode
  })
export const deleteEntity = (endpoint: string, entity: Entity) =>
  http.request(
    `/admin/${endpoint}/${encodeURIComponent(entity.id)}?expected_version=${entity.version}`,
    { method: 'DELETE', decode: decodeVoid }
  )
export const getUserOptions = (signal?: AbortSignal) =>
  http.request('/admin/users/options', { decode: decodeUserOptions, ...(signal ? { signal } : {}) })
export const getPermissions = (signal?: AbortSignal) =>
  http.request('/admin/permissions', {
    decode: (value) => list(decodePermission)(object(value).items),
    ...(signal ? { signal } : {})
  })
export async function getAllCollection<T>(
  endpoint: string,
  decode: Decoder<T>,
  query: CollectionQuery = {},
  signal?: AbortSignal
): Promise<T[]> {
  const items: T[] = []
  for (let page = 1; ; page += 1) {
    const result = await getCollection(endpoint, decode, { ...query, page, page_size: 100 }, signal)
    items.push(...result.items)
    if (items.length >= result.total || result.items.length === 0) return items
  }
}
export const getNavigation = (signal?: AbortSignal) =>
  getAllCollection('menus', decodeNavigation, { navigation: 'true' }, signal)
export const resetPassword = (entity: Entity, password: string) =>
  http.request(`/admin/users/${encodeURIComponent(entity.id)}/password`, {
    method: 'POST',
    body: { password, expected_version: entity.version },
    decode: decodeVoid
  })
export const revokeSession = (id: string, version: number) =>
  http.request(`/admin/sessions/${encodeURIComponent(id)}?expected_version=${version}`, {
    method: 'DELETE',
    decode: decodeVoid
  })
