import { readonly, ref } from 'vue'
import { ApiError, http } from '../../shared/http.ts'
import { authenticate, endSession, getCurrentUser } from './api/index.ts'
import type { User } from './api/index.ts'
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
  return Boolean(user?.is_admin || codes.some((code) => user?.permissions?.includes(code)))
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

export { loginDestination } from './navigation.ts'
