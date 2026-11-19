import { identity, restoreIdentity } from '../identity/public.ts'
import { canFileAction } from './access.ts'
import type { FileAction } from './access.ts'
import type { ManagedFile } from './api/index.ts'

export async function confirmFileAction(
  action: FileAction,
  files: readonly ManagedFile[]
): Promise<boolean> {
  const owner = identity.value?.id
  if (!owner || !files.length) return false
  const user = await restoreIdentity(true)
  return Boolean(user?.id === owner && files.every((file) => canFileAction(user, file, action)))
}
