import { restoreIdentity } from '../identity/public.ts'
import { creditAccess } from './presentation.ts'

export async function confirmCreditAccess(
  action: 'adjust' | 'configure' | 'switchMode',
  ownerId: string
): Promise<boolean> {
  const user = await restoreIdentity(true)
  return Boolean(user?.id === ownerId && creditAccess(user)[action])
}
