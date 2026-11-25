import { onBeforeUnmount, watch } from 'vue'
import type { Ref } from 'vue'
import { waitForBackgroundObservation } from '../../../shared/backgroundObservation.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { ContextTicket } from '../../../shared/http.ts'
import { getJob } from '../api/index.ts'
import type { KnowledgeBase, KnowledgeJob } from '../api/index.ts'

export const jobFinished = (job: KnowledgeJob) =>
  ['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)

interface KnowledgeJobObservationOptions {
  baseId: Readonly<Ref<string>>
  selected: Ref<KnowledgeBase | null>
  jobs: Ref<KnowledgeJob[]>
  observationError: Ref<string>
  syncDetail: (observer: ContextTicket) => Promise<boolean>
  syncOverview: (observer: ContextTicket) => Promise<boolean>
  permissionDenied: (error: unknown) => boolean
  onDenied: (error: unknown) => void
  onCompleted: ((job: KnowledgeJob) => void) | undefined
}

export function useKnowledgeJobObservation(options: KnowledgeJobObservationOptions) {
  const { baseId, selected, jobs, observationError } = options
  const scope = new RequestScope()
  const notifiedJobs = new Set<string>()
  function notifyCompleted(job: KnowledgeJob): void {
    if (job.base_id !== baseId.value || job.status !== 'completed' || notifiedJobs.has(job.id))
      return
    notifiedJobs.add(job.id)
    options.onCompleted?.(job)
  }
  watch(
    jobs,
    (next, previous) => {
      for (const job of next) {
        if (previous.some((item) => item.id === job.id && !jobFinished(item))) notifyCompleted(job)
      }
    },
    { flush: 'sync' }
  )

  async function observe(): Promise<void> {
    const ticket = scope.replace()
    const id = baseId.value
    if (!ticket.isCurrent() || !id || !selected.value?.can_manage) return
    const isCurrent = () => ticket.isCurrent() && id === baseId.value
    const pendingIds = () => {
      const ids = jobs.value.filter((job) => !jobFinished(job)).map((job) => job.id)
      const activeId = selected.value?.active_job_id
      if (activeId && !jobs.value.some((job) => job.id === activeId)) ids.push(activeId)
      return [...new Set(ids)]
    }
    let failures = 0
    let needsDetailSync = false
    let needsOverviewSync = false
    try {
      while (isCurrent() && (needsDetailSync || needsOverviewSync || pendingIds().length)) {
        await waitForBackgroundObservation(ticket.signal, Math.min(1500 * 2 ** failures, 30_000))
        try {
          if (!needsDetailSync && !needsOverviewSync) {
            const updates = await Promise.all(
              pendingIds().map((jobId) => getJob(jobId, ticket.signal))
            )
            if (!isCurrent()) return
            for (const job of updates) {
              const current = jobs.value.find((item) => item.id === job.id)
              if (
                current &&
                (current.version > job.version || (jobFinished(current) && !jobFinished(job)))
              )
                continue
              if (!current) jobs.value = [job, ...jobs.value]
              else if (JSON.stringify(current) !== JSON.stringify(job))
                jobs.value = jobs.value.map((item) => (item.id === job.id ? job : item))
              if (jobFinished(job)) needsDetailSync = needsOverviewSync = true
            }
          }
          if (needsDetailSync) {
            const synced = await options.syncDetail(ticket)
            if (!isCurrent()) return
            if (!synced) {
              failures = Math.min(failures + 1, 5)
              continue
            }
            needsDetailSync = false
          }
          if (needsOverviewSync) {
            const synced = await options.syncOverview(ticket)
            if (!isCurrent()) return
            if (!synced) {
              failures = Math.min(failures + 1, 5)
              continue
            }
            needsOverviewSync = false
          }
          failures = 0
          observationError.value = ''
        } catch (error) {
          if (!isCurrent() || isAborted(error)) return
          if (options.permissionDenied(error)) {
            options.onDenied(error)
            return
          }
          failures = Math.min(failures + 1, 5)
          observationError.value = errorMessage(error)
        }
      }
    } catch (error) {
      if (isCurrent() && !isAborted(error)) observationError.value = errorMessage(error)
    }
  }

  function stop(): ContextTicket {
    return scope.replace()
  }

  function reset(): void {
    stop()
    notifiedJobs.clear()
  }

  onBeforeUnmount(() => scope.dispose())
  return { observe, stop, reset, notifyCompleted }
}
