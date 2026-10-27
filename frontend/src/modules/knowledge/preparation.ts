import { acceptSnapshot } from '../../shared/stream.ts'
import type { KnowledgePreparation } from './api/index.ts'

const finished = new Set(['admitted', 'completed', 'failed', 'cancelled', 'interrupted'])

/** Merge responses from overlapping observation and cancellation requests. */
export function projectPreparation(
  current: KnowledgePreparation | null,
  incoming: KnowledgePreparation
): KnowledgePreparation {
  if (!current || current.id !== incoming.id) return incoming
  if (finished.has(current.status) && incoming.status !== current.status) return current
  if (
    current.status === 'cancel_requested' &&
    !finished.has(incoming.status) &&
    incoming.status !== 'cancel_requested'
  )
    return current
  if (Date.parse(incoming.updated_at) < Date.parse(current.updated_at)) return current
  if (current.run && incoming.run && !acceptSnapshot(current.run, incoming.run))
    return { ...incoming, run: current.run }
  return incoming
}
