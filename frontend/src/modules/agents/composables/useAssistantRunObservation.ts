import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import { errorMessage, isAborted } from '../../../shared/http.ts'
import type { ContextTicket, RequestScope } from '../../../shared/http.ts'
import { cancelRun, observeRun } from '../../../shared/runs.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import {
  cancelPreparation,
  observePreparation,
  preparationFinished,
  projectPreparation
} from '../../knowledge/public.ts'
import type { KnowledgePreparation } from '../../knowledge/public.ts'
import type { AssistantConversation, AssistantTurn } from '../api/conversations.ts'
import type { AssistantOutgoingTurn } from './useAssistantCommands.ts'

interface AssistantRunObservationOptions {
  selected: Ref<AssistantConversation | null>
  turns: Ref<AssistantTurn[]>
  outgoing: Ref<AssistantOutgoingTurn | null>
  conversationScope: RequestScope
  error: Ref<string>
  readDetail: (id: string, context: ContextTicket) => Promise<AssistantConversation | undefined>
  refreshList: () => Promise<void>
  scroll: () => Promise<void>
}

export function useAssistantRunObservation(options: AssistantRunObservationOptions) {
  const { selected, turns, outgoing, conversationScope, error, readDetail, refreshList, scroll } =
    options
  const activeRun = ref<RunSnapshot | null>(null)
  const preparation = ref<KnowledgePreparation | null>(null)
  const disconnected = ref(false)
  const stopping = ref(false)
  let stopRequested = false
  let stopOperation: Promise<void> | null = null

  const stopAcknowledged = computed(
    () => (activeRun.value ?? preparation.value)?.status === 'cancel_requested'
  )
  const running = computed(() =>
    Boolean(
      (activeRun.value && !isTerminal(activeRun.value)) ||
        (preparation.value && !preparationFinished(preparation.value))
    )
  )
  const preparationLabel = computed(() => {
    if (preparation.value?.status === 'cancel_requested') return '正在停止检索…'
    if (preparation.value?.phase.includes('rerank')) return '正在整理相关资料…'
    return '正在检索知识库…'
  })

  function project(run: RunSnapshot): void {
    if (run.resource_type !== 'agent_chat' || run.resource_id !== selected.value?.id)
      throw new Error('助手运行与当前对话不匹配')
    if (acceptSnapshot(activeRun.value, run)) {
      activeRun.value = run
      if (run.status === 'cancel_requested' || isTerminal(run)) stopRequested = false
    }
    turns.value = turns.value.map((turn) =>
      turn.run.id === run.id && acceptSnapshot(turn.run, run) ? { ...turn, run } : turn
    )
    void scroll()
  }

  function restoreTurns(incoming: AssistantTurn[]): void {
    const current = activeRun.value
    turns.value = incoming.map((turn) =>
      current?.id === turn.run.id && acceptSnapshot(turn.run, current)
        ? { ...turn, run: current }
        : turn
    )
    const outstanding = [...turns.value].reverse().find((turn) => !isTerminal(turn.run))
    activeRun.value =
      outstanding?.run ?? (current?.resource_id === selected.value?.id ? current : null)
  }

  function projectKnowledge(value: KnowledgePreparation): KnowledgePreparation {
    if (value.resource_type !== 'agent_chat' || value.resource_id !== selected.value?.id)
      throw new Error('知识准备与当前助手对话不匹配')
    const next = projectPreparation(preparation.value, value)
    preparation.value = next
    if (
      next.status === 'cancel_requested' ||
      (preparationFinished(next) && next.status !== 'admitted')
    )
      stopRequested = false
    return next
  }

  async function follow(run: RunSnapshot, context: ContextTicket): Promise<void> {
    if (!context.isCurrent()) return
    project(run)
    run = activeRun.value ?? run
    if (stopRequested && !isTerminal(run)) {
      await stop()
      if (!context.isCurrent()) return
      run = activeRun.value ?? run
      if (stopRequested) throw new Error('停止结果待确认，请恢复查看')
    }
    await observeRun(
      run,
      (value) => {
        if (context.isCurrent()) project(value)
      },
      context.signal
    )
    if (context.isCurrent() && selected.value) {
      await readDetail(selected.value.id, context)
      if (!context.isCurrent()) return
      void refreshList()
      await scroll()
    }
  }

  async function followKnowledge(
    value: KnowledgePreparation,
    context: ContextTicket
  ): Promise<void> {
    if (!context.isCurrent()) return
    value = projectKnowledge(value)
    if (stopRequested && !preparationFinished(value)) {
      await stop()
      if (!context.isCurrent()) return
      value = preparation.value ?? value
      if (stopRequested) throw new Error('停止结果待确认，请恢复查看')
    }
    const result = await observePreparation(
      value,
      (incoming) => {
        if (context.isCurrent()) return projectKnowledge(incoming)
      },
      context.signal
    )
    if (!context.isCurrent()) return
    if (!result.run) {
      if (outgoing.value) {
        outgoing.value.stage = result.status === 'cancelled' ? 'cancelled' : 'failed'
        outgoing.value.error =
          result.status === 'cancelled' ? '' : (result.error?.message ?? '知识准备未完成')
      } else error.value = result.error?.message ?? '知识准备未完成'
      void refreshList()
      await scroll()
      return
    }
    preparation.value = null
    project(result.run)
    if (outgoing.value) {
      outgoing.value.stage = 'replying'
      outgoing.value.runId = result.run.id
    }
    await readDetail(result.resource_id, context)
    if (context.isCurrent()) await follow(result.run, context)
  }

  function stop(): Promise<void> {
    if (stopOperation) return stopOperation
    if (stopAcknowledged.value) return Promise.resolve()
    const currentPreparation = preparation.value
    const run = activeRun.value
    if (
      (!currentPreparation || preparationFinished(currentPreparation)) &&
      (!run || isTerminal(run))
    )
      return Promise.resolve()
    const context = conversationScope.current()
    stopRequested = true
    stopping.value = true
    const operation = (async () => {
      try {
        if (currentPreparation && !preparationFinished(currentPreparation)) {
          const next = await cancelPreparation(currentPreparation.id, context.signal)
          if (!context.isCurrent()) return
          const accepted = projectKnowledge(next)
          if (accepted.run) {
            project(accepted.run)
            const latest = activeRun.value
            if (latest && !isTerminal(latest) && latest.status !== 'cancel_requested') {
              const stopped = await cancelRun(latest.id, context.signal)
              if (context.isCurrent()) project(stopped)
            }
          }
        } else if (run) {
          const next = await cancelRun(run.id, context.signal)
          if (context.isCurrent()) project(next)
        }
      } catch (reason) {
        if (context.isCurrent() && !isAborted(reason)) {
          error.value = errorMessage(reason)
          disconnected.value = true
        }
      } finally {
        if (context.isCurrent()) {
          stopping.value = false
          stopOperation = null
        }
      }
    })()
    stopOperation = operation
    return operation
  }

  async function waitForStop(): Promise<void> {
    if (stopOperation) await stopOperation
  }

  function reset(): void {
    activeRun.value = null
    preparation.value = null
    disconnected.value = false
    stopping.value = false
    stopRequested = false
    stopOperation = null
  }

  return {
    activeRun,
    preparation,
    disconnected,
    stopping,
    stopAcknowledged,
    running,
    preparationLabel,
    project,
    restoreTurns,
    follow,
    followKnowledge,
    stop,
    waitForStop,
    reset
  }
}
