import type { User } from '../identity/api/index.ts'
import type { ManagedFile } from './api/index.ts'

export function canManageFile(
  user: Pick<User, 'id' | 'is_admin' | 'permissions'> | null | undefined,
  file: Pick<ManagedFile, 'ownerUserId'>
): boolean {
  return Boolean(user?.is_admin || (user && user.id === file.ownerUserId))
}
