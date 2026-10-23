import { readonly, ref } from 'vue'
import { ApiError, http } from '../../shared/http.ts'
import { authenticate, endSession, getCurrentUser } from './api/index.ts'
import type { User } from './api/index.ts'
const currentUser = ref<User | null>(null)
let version = 0
let restorePromise: Promise<User | null> | null = null
export const identity = readonly(currentUser)
export function clearIdentity(): void {
  version += 1
  http.invalidate()
  currentUser.value = null
  restorePromise = null
}
export function restoreIdentity(): Promise<User | null> {
  if (currentUser.value) return Promise.resolve(currentUser.value)
  if (restorePromise) return restorePromise
  const expected = version
  restorePromise = getCurrentUser()
    .then((user) => {
      if (expected === version) currentUser.value = user
      return expected === version ? user : null
    })
    .catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 401) return null
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
  if (expected === version && !signal.aborted) currentUser.value = user
}
export async function logout(): Promise<void> {
  clearIdentity()
  await endSession()
}
