import { decodeVoid, http } from '../../../shared/http.ts'
import { boolean, list, object, string } from '../../../shared/validation.ts'
export interface User {
  id: string
  username: string
  is_admin: boolean
  permissions?: readonly string[]
}
function parseUser(value: unknown): User {
  const user = object(value)
  return {
    id: string(user.id),
    username: string(user.username),
    is_admin: boolean(user.is_admin),
    permissions: user.permissions === undefined ? [] : list(string)(user.permissions)
  }
}
export const getCurrentUser = () => http.request('/auth/me', { decode: parseUser })
export const authenticate = (username: string, password: string, signal: AbortSignal) =>
  http.request('/auth/login', {
    method: 'POST',
    body: { username, password },
    signal,
    decode: (value) => parseUser(object(value).user)
  })
export const endSession = () =>
  http.request('/auth/logout', { method: 'POST', body: {}, decode: decodeVoid })
