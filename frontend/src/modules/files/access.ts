import type { User } from '../identity/public.ts'
import type { ManagedFile } from './api/index.ts'
import { hasPermission } from '../../shared/platformAccess.ts'

type FileUser = Pick<User, 'id' | 'is_admin' | 'permissions'> | null | undefined
export type FileAction = 'delete' | 'maintain' | 'acl' | 'transfer'

export function canManageFile(
  user: Pick<User, 'id' | 'is_admin' | 'permissions'> | null | undefined,
  file: Pick<ManagedFile, 'ownerUserId'>
): boolean {
  return Boolean(user && user.id === file.ownerUserId)
}

export function canFileAction(
  user: FileUser,
  file: Pick<ManagedFile, 'ownerUserId'>,
  action: FileAction
): boolean {
  return canManageFile(user, file) && hasPermission(user, `ai:storage:${action}`)
}

export function canSelectFile(
  user: FileUser,
  file: Pick<ManagedFile, 'ownerUserId' | 'status' | 'sourceFileId'>
): boolean {
  if (file.sourceFileId || file.status === 'purged') return false
  const actions: FileAction[] =
    file.status === 'active'
      ? ['delete', 'acl', 'transfer']
      : file.status === 'deleted'
        ? ['delete', 'maintain']
        : ['delete']
  return actions.some((action) => canFileAction(user, file, action))
}

export function canLookupFileUsers(user: FileUser): boolean {
  return hasPermission(user, 'ai:storage:read') && hasPermission(user, 'system:users:read')
}
