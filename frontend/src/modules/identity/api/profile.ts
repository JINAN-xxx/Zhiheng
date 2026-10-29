import { http } from '../../../shared/http.ts'
import { boolean, list, number, object, string } from '../../../shared/validation.ts'

export interface Profile {
  id: string
  username: string
  display_name: string
  email: string
  phone: string
  is_admin: boolean
  department_name: string | null
  roles: string[]
  posts: string[]
  created_at: string | null
  version: number
}
export interface ProfileUpdate {
  display_name: string
  email: string
  phone: string
  expected_version: number
}
export interface PasswordUpdate {
  current_password: string
  new_password: string
  expected_version: number
}

export function parseProfile(value: unknown): Profile {
  const profile = object(value)
  const version = number(profile.version)
  if (!Number.isSafeInteger(version) || version < 1) throw new Error('服务器返回的资料版本不正确')
  return {
    id: string(profile.id),
    username: string(profile.username),
    display_name: string(profile.display_name),
    email: string(profile.email),
    phone: string(profile.phone),
    is_admin: boolean(profile.is_admin),
    department_name: profile.department_name === null ? null : string(profile.department_name),
    roles: list(string)(profile.roles),
    posts: list(string)(profile.posts),
    created_at: profile.created_at === null ? null : string(profile.created_at),
    version
  }
}

export const getProfile = (signal: AbortSignal) =>
  http.request('/auth/profile', { signal, decode: parseProfile })

export const updateProfile = (body: ProfileUpdate, signal: AbortSignal) =>
  http.request('/auth/profile', { method: 'PATCH', body, signal, decode: parseProfile })

export async function changePassword(body: PasswordUpdate, signal: AbortSignal): Promise<void> {
  const response = await http.open('/auth/password', { method: 'POST', body, signal })
  if (response.status !== 204) throw new Error('服务器未确认密码修改成功，请重新登录后检查')
}
