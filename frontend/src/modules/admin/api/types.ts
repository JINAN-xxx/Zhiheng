import { boolean, list, number, object, string } from '../../../shared/validation.ts'

export interface Entity {
  id: string
  version: number
  created_at?: string | undefined
  updated_at?: string | undefined
}
export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
export interface AdminUser extends Entity {
  username: string
  display_name: string
  email: string
  phone: string
  department_id: string | null
  post_ids: string[]
  role_ids: string[]
  is_active: boolean
  is_admin: boolean
  remark: string
}
export interface Role extends Entity {
  code: string
  name: string
  permissions: string[]
  is_active: boolean
  remark: string
}
export interface Department extends Entity {
  parent_id: string | null
  name: string
  sort_order: number
  leader: string
  phone: string
  email: string
  is_active: boolean
}
export interface Post extends Entity {
  code: string
  name: string
  sort_order: number
  is_active: boolean
  remark: string
}
export interface Menu extends Entity {
  parent_id: string | null
  name: string
  path: string
  permission: string
  sort_order: number
  is_visible: boolean
}
export interface Dictionary extends Entity {
  code: string
  name: string
  is_active: boolean
  remark: string
}
export interface DictionaryItem extends Entity {
  label: string
  value: string
  sort_order: number
  is_active: boolean
  remark: string
}
export interface Config extends Entity {
  key: string
  name: string
  value: string
  remark: string
  is_active: boolean
}
export interface Notice extends Entity {
  title: string
  content: string
  kind: 'notice' | 'announcement'
  status: 'draft' | 'published'
  remark: string
}
export interface Permission {
  code: string
  label: string
  group: string
}
export interface Option {
  id: string
  name: string
}
export interface UserOptions {
  departments: Option[]
  posts: Option[]
  roles: Option[]
}
export interface NavigationEntry {
  name: string
  path: string
  permission: string
  sort_order: number
  is_visible: boolean
}
export interface Session {
  version: number
  session_id: string
  user_id: string
  username: string
  created_at: string | null
  expires_at: string
  is_current: boolean
}
export interface AdminLog {
  id: string
  kind: 'login' | 'operation'
  action: string
  username: string
  resource_type: string
  resource_id: string | null
  outcome: 'success' | 'failed'
  message: string
  created_at: string
}
export type Decoder<T> = (value: unknown) => T
const optionalTime = (value: unknown): string | undefined =>
  value === undefined || value === null ? undefined : string(value)
const nullableString = (value: unknown): string | null => (value === null ? null : string(value))
function base(row: Record<string, unknown>): Entity {
  const version = number(row.version)
  if (!Number.isInteger(version) || version < 1) throw new Error('服务器返回的版本不正确')
  return {
    id: string(row.id),
    version,
    created_at: optionalTime(row.created_at),
    updated_at: optionalTime(row.updated_at)
  }
}
export function decodePage<T>(decode: Decoder<T>): Decoder<Page<T>> {
  return (value) => {
    const row = object(value)
    return {
      items: list(decode)(row.items),
      total: number(row.total),
      page: number(row.page),
      page_size: number(row.page_size)
    }
  }
}
export const decodeUser: Decoder<AdminUser> = (value) => {
  const r = object(value)
  return {
    ...base(r),
    username: string(r.username),
    display_name: string(r.display_name),
    email: string(r.email),
    phone: string(r.phone),
    department_id: nullableString(r.department_id),
    post_ids: list(string)(r.post_ids),
    role_ids: list(string)(r.role_ids),
    is_active: boolean(r.is_active),
    is_admin: boolean(r.is_admin),
    remark: string(r.remark)
  }
}
export const decodeRole: Decoder<Role> = (value) => {
  const r = object(value)
  return {
    ...base(r),
    code: string(r.code),
    name: string(r.name),
    permissions: list(string)(r.permissions),
    is_active: boolean(r.is_active),
    remark: string(r.remark)
  }
}
export const decodeDepartment: Decoder<Department> = (value) => {
  const r = object(value)
  return {
    ...base(r),
    parent_id: nullableString(r.parent_id),
    name: string(r.name),
    sort_order: number(r.sort_order),
    leader: string(r.leader),
    phone: string(r.phone),
    email: string(r.email),
    is_active: boolean(r.is_active)
  }
}
export const decodePost: Decoder<Post> = (value) => {
  const r = object(value)
  return {
    ...base(r),
    code: string(r.code),
    name: string(r.name),
    sort_order: number(r.sort_order),
    is_active: boolean(r.is_active),
    remark: string(r.remark)
  }
}
export const decodeMenu: Decoder<Menu> = (value) => {
  const r = object(value)
  return {
    ...base(r),
    parent_id: nullableString(r.parent_id),
    name: string(r.name),
    path: string(r.path),
    permission: string(r.permission),
    sort_order: number(r.sort_order),
    is_visible: boolean(r.is_visible)
  }
}
export const decodeDictionary: Decoder<Dictionary> = (value) => {
  const r = object(value)
  return {
    ...base(r),
    code: string(r.code),
    name: string(r.name),
    is_active: boolean(r.is_active),
    remark: string(r.remark)
  }
}
export const decodeDictionaryItem: Decoder<DictionaryItem> = (value) => {
  const r = object(value)
  return {
    ...base(r),
    label: string(r.label),
    value: string(r.value),
    sort_order: number(r.sort_order),
    is_active: boolean(r.is_active),
    remark: string(r.remark)
  }
}
export const decodeConfig: Decoder<Config> = (value) => {
  const r = object(value)
  return {
    ...base(r),
    key: string(r.key),
    name: string(r.name),
    value: string(r.value),
    remark: string(r.remark),
    is_active: boolean(r.is_active)
  }
}
export const decodeNotice: Decoder<Notice> = (value) => {
  const r = object(value)
  const kind = string(r.kind),
    status = string(r.status)
  if (!['notice', 'announcement'].includes(kind) || !['draft', 'published'].includes(status))
    throw new Error('服务器返回的公告状态不正确')
  return {
    ...base(r),
    title: string(r.title),
    content: string(r.content),
    kind: kind as Notice['kind'],
    status: status as Notice['status'],
    remark: string(r.remark)
  }
}
export const decodePermission: Decoder<Permission> = (value) => {
  const r = object(value)
  return { code: string(r.code), label: string(r.label), group: string(r.group) }
}
export const decodeUserOptions: Decoder<UserOptions> = (value) => {
  const r = object(value)
  const option = (value: unknown): Option => {
    const r = object(value)
    return { id: string(r.id), name: string(r.name) }
  }
  return {
    departments: list(option)(r.departments),
    posts: list(option)(r.posts),
    roles: list(option)(r.roles)
  }
}
export const decodeNavigation: Decoder<NavigationEntry> = (value) => {
  const r = object(value)
  return {
    name: string(r.name),
    path: string(r.path),
    permission: string(r.permission),
    sort_order: number(r.sort_order),
    is_visible: boolean(r.is_visible)
  }
}
export const decodeSession: Decoder<Session> = (value) => {
  const r = object(value)
  return {
    session_id: string(r.session_id),
    version: number(r.version),
    user_id: string(r.user_id),
    username: string(r.username),
    created_at: nullableString(r.created_at),
    expires_at: string(r.expires_at),
    is_current: boolean(r.is_current)
  }
}
export const decodeLog: Decoder<AdminLog> = (value) => {
  const r = object(value),
    kind = string(r.kind),
    outcome = string(r.outcome)
  if (!['login', 'operation'].includes(kind) || !['success', 'failed'].includes(outcome))
    throw new Error('服务器返回的日志类型不正确')
  return {
    id: string(r.id),
    kind: kind as AdminLog['kind'],
    action: string(r.action),
    username: string(r.username),
    resource_type: string(r.resource_type),
    resource_id: nullableString(r.resource_id),
    outcome: outcome as AdminLog['outcome'],
    message: string(r.message),
    created_at: string(r.created_at)
  }
}

export type UserPayload = Omit<AdminUser, keyof Entity | 'username' | 'is_admin'> & {
  username?: string
  password?: string
  is_admin?: boolean
}
export type RolePayload = Omit<Role, keyof Entity | 'code'> & { code?: string }
export type DepartmentPayload = Omit<Department, keyof Entity>
export type PostPayload = Omit<Post, keyof Entity | 'code'> & { code?: string }
export type MenuPayload = Omit<Menu, keyof Entity>
export type DictionaryPayload = Omit<Dictionary, keyof Entity>
export type DictionaryItemPayload = Omit<DictionaryItem, keyof Entity>
export type ConfigPayload = Omit<Config, keyof Entity>
export type NoticePayload = Omit<Notice, keyof Entity>
