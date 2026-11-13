import { readonly, ref } from 'vue'
import { hasPermission } from '../../shared/platformAccess.ts'
import { ApiError, http } from '../../shared/http.ts'
import { authenticate, endSession, getCurrentUser } from './api/index.ts'
import type { User } from './api/index.ts'
import {
  hasModule,
  hasCapability,
  moduleForPath,
  firstModulePath
} from '../../shared/platformAccess.ts'
import { loginDestination as safeDestination } from './navigation.ts'
export type { User } from './api/index.ts'
const currentUser = ref<User | null>(null)
let checkedAt = 0
const identityTtl = 30000
let version = 0
let restorePromise: Promise<User | null> | null = null
export const identity = readonly(currentUser)
export function hasAnyPermission(
  user: Pick<User, 'is_admin' | 'permissions'> | null | undefined,
  ...codes: string[]
): boolean {
  return Boolean(user?.is_admin || codes.some((code) => hasPermission(user, code)))
}
export function clearIdentity(): void {
  checkedAt = 0
  version += 1
  http.invalidate()
  currentUser.value = null
  restorePromise = null
}
export function restoreIdentity(force = false): Promise<User | null> {
  if (!force && currentUser.value && Date.now() - checkedAt < identityTtl)
    return Promise.resolve(currentUser.value)
  if (restorePromise) return restorePromise
  const expected = version
  restorePromise = getCurrentUser()
    .then((user) => {
      if (expected === version) {
        checkedAt = Date.now()
        if (JSON.stringify(currentUser.value) !== JSON.stringify(user)) {
          if (currentUser.value) http.invalidate()
          currentUser.value = user
        }
      }
      return expected === version ? user : null
    })
    .catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        if (expected === version) clearIdentity()
        return null
      }
      throw error
    })
    .finally(() => {
      if (expected === version) restorePromise = null
    })
  return restorePromise
}
export async function login(
  username: string,
  password: string,
  signal: AbortSignal
): Promise<void> {
  clearIdentity()
  const expected = version
  const user = await authenticate(username, password, signal)
  if (expected === version && !signal.aborted) {
    currentUser.value = user
    checkedAt = Date.now()
  }
}
export async function logout(): Promise<void> {
  clearIdentity()
  await endSession()
}

export function canUseCapability(module: string, action = 'access'): boolean {
  return hasCapability(identity.value, module, action)
}
export function canUseModule(module: string): boolean {
  return hasModule(identity.value, module)
}
export function loginDestination(value: unknown): string {
  const path = typeof value === 'string' ? safeDestination(value) : firstModulePath(identity.value)
  const module = moduleForPath(path.split('?')[0] ?? path)
  return module && !hasModule(identity.value, module) ? firstModulePath(identity.value) : path
}
export { hasModule, firstModulePath } from '../../shared/platformAccess.ts'
