<script setup lang="ts">
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Modal as AModal,
  Tag as ATag,
  Textarea as ATextarea,
  message
} from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getRun, cancelRun } from '../../../shared/runs.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { observeModelTest, startModelTrial } from '../api/index.ts'
import type { ChatModel } from '../api/index.ts'
import { parseTrialProgress, trialPayload } from '../trialProgress.ts'
import type { TrialIntent } from '../trialProgress.ts'

const props = defineProps<{
  models: ChatModel[]
  canTest: boolean
  pending: Record<string, boolean>
  connectionIssues: Record<string, string>
}>()
const emit = defineEmits<{ refresh: []; denied: [] }>()
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
const status = computed(() => {
  if (submitting.value) return '正在提交'
  if (!run.value) return uncertain.value ? '待确认提交' : '尚未开始'
  return {
    pending: '等待执行',
    running: '正在试用',
    cancel_requested: '正在停止',
    completed: '试用完成',
    failed: '试用失败',
    cancelled: '已停止',
    interrupted: '试用中断'
  }[run.value.status]
})
const primaryText = computed(() =>
  uncertain.value ? '确认原提交' : active.value ? '继续查看' : '开始试用'
)
const embeddingDimension = computed(() => {
  const data = run.value?.result?.data
  if (!Array.isArray(data) || !data[0] || typeof data[0] !== 'object') return null
  const vector: unknown = (data[0] as Record<string, unknown>).embedding
  return Array.isArray(vector) &&
    vector.length &&
    vector.every((item) => typeof item === 'number' && Number.isFinite(item))
    ? vector.length
    : null
})
const ranking = computed(() => {
  const rows = run.value?.result?.results
  if (!Array.isArray(rows)) return []
  return rows.flatMap((item: unknown) => {
    if (!item || typeof item !== 'object') return []
    const value = item as Record<string, unknown>
    if (
      typeof value.index !== 'number' ||
      !Number.isSafeInteger(value.index) ||
      value.index < 0 ||
      value.index >= sentDocuments.value.length ||
      typeof value.relevance_score !== 'number' ||
      !Number.isFinite(value.relevance_score)
    )
      return []
    return [
      { index: value.index, score: value.relevance_score, text: sentDocuments.value[value.index] }
    ]
  })
})
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
    emit('denied')
    emit('refresh')
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
    forget()
    run.value = null
    intent.value = null
    uncertain.value = false
    prompt.value = ''
    documents.value = ''
    sentDocuments.value = []
    if (reason instanceof ApiError && reason.status === 403 && reason.code === 'forbidden')
      emit('denied')
    emit('refresh')
  }
}
async function follow(initial: RunSnapshot) {
  const ticket = read.replace()
  observing.value = true
  try {
    keep(initial)
    await observeModelTest(
      initial,
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
  if (!run.value || working.value) return
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
  if (!model.value || working.value || (unavailable.value && !recoverable.value)) return
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
      if (!(reason instanceof ApiError) || reason.status >= 500) {
        uncertain.value = true
        error.value = '提交结果尚未确认，请确认原提交，避免重复试用。'
        remember()
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
  if (!run.value || !active.value || stopping.value || run.value.status === 'cancel_requested')
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
    await follow(value)
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
  visible.value = true
  return true
}
watch(
  () => [identity.value?.id, props.canTest] as const,
  ([user, allowed]) => {
    if (modelId.value && (!allowed || user !== ownerId.value)) {
      forget()
      reset()
      visible.value = false
      modelId.value = ''
      ownerId.value = ''
    }
  }
)
watch(model, (value) => {
  if (modelId.value && !value) {
    forget()
    reset()
    visible.value = false
    modelId.value = ''
  }
})
onBeforeUnmount(() => {
  read.dispose()
  write.dispose()
  cancel.dispose()
})
defineExpose({ open })
</script>
<template>
  <AModal
    :open="visible"
    :width="760"
    centered
    :mask-closable="false"
    :closable="!working && !pendingResult"
    :keyboard="!working && !pendingResult"
    wrap-class-name="model-trial-modal"
    @cancel="close"
  >
    <template #title
      ><span class="trial-title">{{ '模型试用 · ' + (model?.name ?? '') }}</span></template
    >
    <template v-if="model">
      <AAlert
        v-if="unavailable"
        type="warning"
        show-icon
        :message="connectionIssues[model.id] || '模型已停用或权限已变化，请刷新后查看'"
        class="trial-notice"
      />
      <AAlert v-if="error" type="error" show-icon :message="error" class="trial-notice" />
      <AForm layout="vertical" :disabled="working || pendingResult || unavailable">
        <AFormItem :label="model.model_type === 'embedding' ? '测试文本' : '试用问题'" required>
          <ATextarea
            v-model:value="prompt"
            aria-label="试用内容"
            :rows="3"
            :maxlength="4000"
            placeholder="输入想要试用的内容，每次试用独立处理"
          />
        </AFormItem>
        <AFormItem v-if="model.model_type === 'rerank'" label="候选资料" required>
          <ATextarea
            v-model:value="documents"
            aria-label="候选资料"
            :rows="4"
            placeholder="每行一份候选资料，最多 8 份，每份最多 1000 个字符"
          />
        </AFormItem>
      </AForm>
      <div class="trial-result-header">
        <strong>试用结果</strong><ATag>{{ status }}</ATag>
      </div>
      <div class="trial-result" aria-live="polite">
        <p v-if="!run">输入内容后点击“开始试用”，会产生模型用量并沿用当前计费规则。</p>
        <template v-else>
          <AAlert v-if="run.error" type="error" show-icon :message="run.error.message" />
          <pre v-if="run.text" class="trial-answer">{{ run.text }}</pre>
          <details v-if="run.reasoning">
            <summary>思考过程</summary>
            <pre>{{ run.reasoning }}</pre>
          </details>
          <p v-if="embeddingDimension">已生成 1 份向量，维度 {{ embeddingDimension }}。</p>
          <ol v-if="ranking.length" class="trial-ranking">
            <li v-for="item in ranking" :key="item.index">
              <span>{{ item.text }}</span
              ><small>相关度 {{ item.score.toFixed(4) }}</small>
            </li>
          </ol>
          <p v-if="active && !run.text && !ranking.length && !embeddingDimension">
            正在等待模型返回…
          </p>
          <p v-if="run.usage.status !== 'pending'" class="trial-usage">
            {{
              run.usage.status === 'known'
                ? `输入 ${run.usage.input_tokens ?? 0} Token · 输出 ${run.usage.output_tokens ?? 0} Token`
                : '用量待核对'
            }}
          </p>
        </template>
      </div>
    </template>
    <template #footer>
      <AButton :disabled="working || pendingResult" @click="close">关闭</AButton>
      <AButton
        v-if="active"
        :loading="stopping"
        :disabled="run?.status === 'cancel_requested'"
        @click="stop"
        >停止试用</AButton
      >
      <AButton
        type="primary"
        aria-label="提交试用"
        :loading="submitting || observing"
        :disabled="working || (unavailable && !recoverable)"
        @click="submit"
        >{{ primaryText }}</AButton
      >
    </template>
  </AModal>
</template>
<style scoped>
.trial-title {
  display: block;
  padding-right: 28px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.trial-notice {
  margin-bottom: 16px;
}
.trial-result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 13px;
}
.trial-result {
  min-height: 120px;
  max-height: 36vh;
  overflow: auto;
  padding: 16px;
  background: var(--gray-25);
  border-radius: 8px;
}
.trial-result p {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.8;
}
.trial-answer,
.trial-result pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  font-size: 13px;
  line-height: 1.8;
}
.trial-result details {
  margin-top: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.trial-result summary {
  cursor: pointer;
  margin-bottom: 8px;
}
.trial-ranking {
  padding-left: 22px;
  margin: 0;
}
.trial-ranking li {
  margin: 0 0 12px;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.trial-ranking small {
  display: block;
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.trial-usage {
  margin: 12px 0 0;
}
</style>
