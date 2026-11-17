import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { createRequestId } from '../../../shared/requestId.ts'
import { identity } from '../../identity/public.ts'
import {
  ApiError,
  errorMessage,
  isAborted,
  isDefiniteRejection,
  RequestScope
} from '../../../shared/http.ts'
import { getRun, cancelRun, observeRun } from '../../../shared/runs.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { startModelTrial } from '../api/index.ts'
import type { ChatModel } from '../api/index.ts'
import { parseTrialProgress, trialPayload } from '../trialProgress.ts'
import type { TrialIntent } from '../trialProgress.ts'

interface ModelTrialOptions {
  models: ChatModel[]
  canTest: boolean
  pending: Record<string, boolean>
  connectionIssues: Record<string, string>
}
interface ModelTrialEvents {
  refresh: () => void
  denied: () => void
}
export function useModelTrial(props: ModelTrialOptions, events: ModelTrialEvents) {
  const visible = ref(false),
    modelId = ref(''),
    ownerId = ref(''),
    prompt = ref(''),
    documents = ref('')
  const run = ref<RunSnapshot | null>(null),
    intent = ref<TrialIntent | null>(null)
  const submitting = ref(false),
    observing = ref(false),
    stopping = ref(false),
    uncertain = ref(false),
    error = ref('')
  const sentDocuments = ref<string[]>([])
  let disposed = false
  const read = new RequestScope(),
    write = new RequestScope(),
    cancel = new RequestScope()
  const model = computed(() => props.models.find((item) => item.id === modelId.value) ?? null)
  const active = computed(() => !!run.value && !isTerminal(run.value))
  const working = computed(() => submitting.value || observing.value || stopping.value)
  const pendingResult = computed(() => active.value || uncertain.value)
  const unavailable = computed(
    () =>
      !props.canTest ||
      !model.value?.enabled ||
      !!props.pending[modelId.value] ||
      !!props.connectionIssues[modelId.value]
  )
  const recoverable = computed(() => props.canTest && (uncertain.value || active.value))
  const storageKey = () => `zhiheng:model-trial:${ownerId.value}:${modelId.value}`
  function forget() {
    try {
      sessionStorage.removeItem(storageKey())
    } catch {
      /* Optional browser storage. */
    }
  }
  function remember(): boolean {
    if (!intent.value) return true
    try {
      sessionStorage.setItem(
        storageKey(),
        JSON.stringify({
          ownerId: ownerId.value,
          modelId: modelId.value,
          intent: intent.value,
          runId: run.value?.id ?? null
        })
      )
      return true
    } catch {
      return false
    }
  }
  function reset() {
    read.replace()
    write.replace()
    cancel.replace()
    run.value = null
    intent.value = null
    prompt.value = ''
    documents.value = ''
    sentDocuments.value = []
    submitting.value = false
    observing.value = false
    stopping.value = false
    uncertain.value = false
    error.value = ''
  }
  function concealPending(): boolean {
    if (!intent.value) return false
    remember()
    read.replace()
    write.replace()
    cancel.replace()
    run.value = null
    prompt.value = ''
    documents.value = ''
    sentDocuments.value = []
    submitting.value = false
    observing.value = false
    stopping.value = false
    uncertain.value = true
    visible.value = false
    return true
  }
  function keep(value: RunSnapshot) {
    if (value.resource_type !== 'connection_test' || value.resource_id !== modelId.value) {
      forget()
      intent.value = null
      run.value = null
      uncertain.value = false
      throw new Error('试用结果与当前模型不匹配')
    }
    if (!acceptSnapshot(run.value, value)) return
    if (value.status === 'failed' && value.error?.code === 'forbidden') {
      forget()
      run.value = null
      intent.value = null
      uncertain.value = false
      prompt.value = ''
      documents.value = ''
      sentDocuments.value = []
      events.denied()
      events.refresh()
      return
    }
    run.value = value
    if (isTerminal(value)) {
      forget()
      intent.value = null
      uncertain.value = false
    } else remember()
  }
  function denied(reason: unknown): boolean {
    return (
      reason instanceof ApiError &&
      ([401, 404].includes(reason.status) || (reason.status === 403 && reason.code === 'forbidden'))
    )
  }
  function fail(reason: unknown) {
    error.value = errorMessage(reason)
    if (denied(reason)) {
      if (!concealPending()) {
        forget()
        run.value = null
        intent.value = null
        uncertain.value = false
        prompt.value = ''
        documents.value = ''
        sentDocuments.value = []
      }
      if (reason instanceof ApiError && reason.status === 403 && reason.code === 'forbidden')
        events.denied()
      events.refresh()
    }
  }
  async function follow(initial: RunSnapshot) {
    const ticket = read.replace()
    observing.value = true
    try {
      keep(initial)
      const latest = run.value
      if (!latest) return
      await observeRun(
        latest,
        (value) => {
          if (ticket.isCurrent()) keep(value)
        },
        ticket.signal
      )
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) fail(reason)
    } finally {
      if (ticket.isCurrent()) observing.value = false
    }
  }
  async function resume() {
    if (disposed || ownerId.value !== identity.value?.id || !run.value || working.value) return
    const ticket = read.replace(),
      id = run.value.id
    observing.value = true
    error.value = ''
    try {
      const value = await getRun(id, ticket.signal)
      if (!ticket.isCurrent()) return
      observing.value = false
      await follow(value)
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) fail(reason)
    } finally {
      if (ticket.isCurrent()) observing.value = false
    }
  }
  async function submit() {
    if (
      disposed ||
      ownerId.value !== identity.value?.id ||
      !model.value ||
      working.value ||
      (unavailable.value && !recoverable.value)
    )
      return
    if (active.value) {
      await resume()
      return
    }
    let body: TrialIntent
    try {
      body = intent.value ?? {
        ...trialPayload(prompt.value, documents.value, model.value.model_type),
        client_request_id: createRequestId()
      }
    } catch (reason) {
      error.value = errorMessage(reason)
      return
    }
    const ticket = write.replace()
    submitting.value = true
    error.value = ''
    uncertain.value = false
    const confirming = intent.value !== null
    intent.value = body
    run.value = null
    sentDocuments.value = [...body.documents]
    if (!remember() && !confirming) {
      intent.value = null
      submitting.value = false
      error.value = '无法保存试用进度，请检查浏览器存储后重试'
      return
    }
    try {
      const value = await startModelTrial(modelId.value, body, ticket.signal)
      if (!ticket.isCurrent()) return
      submitting.value = false
      await follow(value)
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        if (confirming || !isDefiniteRejection(reason)) {
          uncertain.value = true
          error.value = '提交结果尚未确认，请确认原提交，避免重复试用。'
          remember()
          if (denied(reason)) fail(reason)
        } else {
          forget()
          intent.value = null
          fail(reason)
        }
      }
    } finally {
      if (ticket.isCurrent()) submitting.value = false
    }
  }
  async function stop() {
    if (
      disposed ||
      ownerId.value !== identity.value?.id ||
      !run.value ||
      !active.value ||
      stopping.value ||
      run.value.status === 'cancel_requested'
    )
      return
    const ticket = cancel.replace()
    stopping.value = true
    try {
      const value = await cancelRun(run.value.id, ticket.signal)
      if (!ticket.isCurrent()) return
      keep(value)
      read.replace()
      observing.value = false
      stopping.value = false
      if (run.value) await follow(run.value)
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) fail(reason)
    } finally {
      if (ticket.isCurrent()) stopping.value = false
    }
  }
  function close() {
    if (working.value || pendingResult.value) return
    visible.value = false
  }
  function open(value: ChatModel): boolean {
    if (disposed || !identity.value?.id) return false
    if (
      !props.canTest ||
      !value.enabled ||
      props.pending[value.id] ||
      props.connectionIssues[value.id]
    )
      return false
    if (modelId.value !== value.id && (working.value || pendingResult.value)) {
      visible.value = true
      void message.info('请先完成或确认当前试用')
      return false
    }
    if (modelId.value !== value.id || ownerId.value !== identity.value?.id) {
      reset()
      modelId.value = value.id
      ownerId.value = identity.value?.id ?? ''
      try {
        const saved = parseTrialProgress(
          sessionStorage.getItem(storageKey()),
          ownerId.value,
          modelId.value
        )
        if (saved) {
          intent.value = saved.intent
          prompt.value = saved.intent.prompt
          documents.value = saved.intent.documents.join('\n')
          sentDocuments.value = [...saved.intent.documents]
          uncertain.value = true
          if (saved.runId) {
            run.value = {
              id: saved.runId,
              resource_type: 'connection_test',
              resource_id: modelId.value,
              status: 'pending',
              attempt_id: null,
              attempt_no: 0,
              revision: 0,
              projection_seq: 0,
              text: '',
              error: null,
              created_at: '',
              usage: { input_tokens: null, output_tokens: null, status: 'pending' }
            }
            uncertain.value = false
            void resume()
          }
        }
      } catch {
        forget()
      }
    }
    if (!visible.value && intent.value) {
      prompt.value = intent.value.prompt
      documents.value = intent.value.documents.join('\n')
      sentDocuments.value = [...intent.value.documents]
    }
    visible.value = true
    return true
  }
  watch(
    () => [identity.value, props.canTest] as const,
    ([user, allowed], [previous]) => {
      if (modelId.value && (!allowed || user?.id !== ownerId.value)) {
        if (user?.id !== ownerId.value || !concealPending()) {
          forget()
          reset()
          visible.value = false
          modelId.value = ''
          ownerId.value = ''
        }
      } else if (modelId.value && user !== previous) {
        read.replace()
        write.replace()
        cancel.replace()
        submitting.value = false
        observing.value = false
        stopping.value = false
        if (intent.value && !run.value) {
          uncertain.value = true
          remember()
        }
      }
    }
  )
  watch(model, (value) => {
    if (modelId.value && !value && !concealPending()) {
      forget()
      reset()
      visible.value = false
      modelId.value = ''
    }
  })
  onBeforeUnmount(() => {
    disposed = true
    remember()
    reset()
    read.dispose()
    write.dispose()
    cancel.dispose()
  })
  return {
    visible,
    model,
    prompt,
    documents,
    run,
    sentDocuments,
    submitting,
    observing,
    stopping,
    uncertain,
    error,
    active,
    working,
    pendingResult,
    unavailable,
    recoverable,
    open,
    close,
    submit,
    resume,
    stop
  }
}
