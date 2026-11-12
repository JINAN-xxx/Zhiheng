<script setup lang="ts">
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  Input as AInput,
  Textarea as ATextarea,
  Alert as AAlert,
  Checkbox as ACheckbox,
  Form as AForm,
  FormItem as AFormItem,
  Modal as AModal,
  Select as ASelect,
  Spin as ASpin
} from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { getModels } from '../../models/public.ts'
import type { ChatModel } from '../../models/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { boolean, number, object, string } from '../../../shared/validation.ts'
import { configureKnowledge, getBase, getCapabilities } from '../api/index.ts'
import type { KnowledgeBase, KnowledgeJob, KnowledgeSettingsInput } from '../api/index.ts'
import { validChunks } from '../presentation.ts'
import ChunkFields from './ChunkFields.vue'

const emit = defineEmits<{ saved: [base: KnowledgeBase, job: KnowledgeJob | null] }>()
const visible = ref(false),
  loading = ref(false),
  saving = ref(false),
  uncertain = ref(false)
const error = ref(''),
  current = ref<KnowledgeBase | null>(null),
  models = ref<ChatModel[]>([])
const defaultRanker = ref<string | null>(null),
  defaultRankerReady = ref(false)
const form = reactive({
  name: '',
  description: '',
  embed: '',
  ranker: 'default',
  size: 1000 as number | null,
  overlap: 200 as number | null
})
const confirmed = ref(false)
const read = new RequestScope(),
  write = new RequestScope()
let pending: KnowledgeSettingsInput | null = null
const key = () =>
  `zhiheng:knowledge-settings:${identity.value?.id ?? ''}:${current.value?.id ?? ''}`
const requiresRebuild = computed(
  () =>
    !!current.value?.document_count &&
    (form.embed !== current.value.embed_model_id ||
      form.size !== current.value.chunk_size ||
      form.overlap !== current.value.chunk_overlap ||
      current.value.index_status === 'reindex_required')
)
const embeddingOptions = computed(() => modelOptions('embedding', form.embed))
const rerankOptions = computed(() => [
  {
    value: 'default',
    label: defaultRankerReady.value
      ? '使用默认重排模型：' +
        (models.value.find((model) => model.id === defaultRanker.value)?.name ?? '已配置')
      : '默认重排模型未配置或不可用',
    disabled: !defaultRankerReady.value
  },
  ...modelOptions('rerank', form.ranker === 'default' ? '' : form.ranker)
])
function modelOptions(kind: string, selected: string) {
  const rows = models.value
    .filter((m) => m.model_type === kind && m.enabled)
    .map((m) => ({ value: m.id, label: m.name, disabled: false }))
  if (selected && !rows.some((m) => m.value === selected))
    rows.push({ value: selected, label: '当前模型不可用，请重新选择', disabled: true })
  return rows
}
function apply(body: KnowledgeSettingsInput) {
  if (body.name !== undefined) form.name = body.name
  if (body.description !== undefined) form.description = body.description
  form.embed = body.embed_model_id
  form.ranker = body.use_default_rerank ? 'default' : (body.rerank_model_id ?? 'default')
  form.size = body.chunk_size
  form.overlap = body.chunk_overlap
}
function restore(value: unknown): KnowledgeSettingsInput {
  const d = object(value)
  return {
    ...(d.name === undefined ? {} : { name: string(d.name) }),
    ...(d.description === undefined ? {} : { description: string(d.description) }),
    expected_version: number(d.expected_version),
    embed_model_id: string(d.embed_model_id),
    rerank_model_id: d.rerank_model_id === null ? null : string(d.rerank_model_id),
    use_default_rerank: boolean(d.use_default_rerank),
    chunk_size: number(d.chunk_size),
    chunk_overlap: number(d.chunk_overlap),
    confirm_rebuild: boolean(d.confirm_rebuild),
    client_request_id: string(d.client_request_id)
  }
}
function close() {
  if (saving.value) return
  visible.value = false
  read.replace()
  write.replace()
}
async function open(base: KnowledgeBase) {
  if (!identity.value || base.owner_id !== identity.value.id || !base.can_manage) return
  current.value = base
  visible.value = true
  loading.value = true
  uncertain.value = false
  confirmed.value = false
  error.value = ''
  pending = null
  const ticket = read.replace()
  try {
    const [fresh, available, capabilities] = await Promise.all([
      getBase(base.id, ticket.signal),
      getModels(ticket.signal),
      getCapabilities(ticket.signal)
    ])
    if (!ticket.isCurrent()) return
    current.value = fresh
    models.value = available
    defaultRanker.value = capabilities.default_rerank_model_id
    defaultRankerReady.value = capabilities.default_rerank_configured
    form.name = fresh.name
    form.description = fresh.description
    form.embed = fresh.embed_model_id
    form.ranker =
      fresh.use_default_rerank !== false ? 'default' : (fresh.rerank_model_id ?? 'default')
    form.size = fresh.chunk_size
    form.overlap = fresh.chunk_overlap
    const saved = sessionStorage.getItem(key())
    if (saved) {
      pending = restore(JSON.parse(saved))
      apply(pending)
      uncertain.value = true
      confirmed.value = pending.confirm_rebuild
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
async function save() {
  if (!current.value || loading.value || saving.value) return
  if (!form.name.trim() || !form.embed || !validChunks(form.size, form.overlap)) {
    error.value = '请选择向量模型并填写有效的切片大小与重叠大小'
    return
  }
  const validRanker =
    form.ranker === 'default'
      ? defaultRankerReady.value
      : models.value.some(
          (model) => model.id === form.ranker && model.model_type === 'rerank' && model.enabled
        )
  if (!validRanker) {
    error.value = '必须选择有效的重排模型，或联系管理员配置默认重排'
    return
  }
  if (requiresRebuild.value && !confirmed.value) {
    error.value = '请确认重新处理现有资料；处理将产生模型用量'
    return
  }
  const body: KnowledgeSettingsInput = pending ?? {
    name: form.name.trim(),
    description: form.description.trim(),
    expected_version: current.value.version,
    embed_model_id: form.embed,
    use_default_rerank: form.ranker === 'default',
    rerank_model_id: form.ranker === 'default' ? null : form.ranker,
    chunk_size: form.size!,
    chunk_overlap: form.overlap!,
    confirm_rebuild: confirmed.value,
    client_request_id: createRequestId()
  }
  const storageKey = key(),
    id = current.value.id
  try {
    sessionStorage.setItem(storageKey, JSON.stringify(body))
  } catch {
    error.value = '无法保留提交信息，请恢复浏览器会话存储后重试'
    return
  }
  pending = body
  saving.value = true
  error.value = ''
  const ticket = write.replace()
  try {
    const result = await configureKnowledge(id, body, ticket.signal)
    if (!ticket.isCurrent()) return
    sessionStorage.removeItem(storageKey)
    pending = null
    uncertain.value = false
    visible.value = false
    emit('saved', result.base, result.job)
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      uncertain.value = !(reason instanceof ApiError) || reason.status >= 500
      if (!uncertain.value) {
        sessionStorage.removeItem(storageKey)
        pending = null
      }
      error.value = errorMessage(reason)
    }
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}
watch(
  () => identity.value?.id,
  () => {
    close()
    current.value = null
  }
)
onBeforeUnmount(() => {
  read.dispose()
  write.dispose()
})
defineExpose({ open })
</script>
<template>
  <AModal
    :open="visible"
    :title="'知识库设置 · ' + (current?.name ?? '')"
    centered
    cancel-text="取消"
    :width="620"
    :mask-closable="false"
    :closable="!saving"
    :confirm-loading="saving"
    :ok-text="uncertain ? '确认原提交' : requiresRebuild ? '保存并重新处理' : '保存设置'"
    :ok-button-props="{
      disabled: loading || !!current?.active_job_id || (requiresRebuild && !confirmed)
    }"
    @cancel="close"
    @ok="save"
  >
    <ASpin :spinning="loading">
      <AAlert v-if="error" :message="error" type="error" show-icon />
      <AAlert
        v-if="uncertain"
        message="上次提交结果待确认，将使用原请求继续核对。"
        type="warning"
        show-icon
      />
      <AForm
        layout="vertical"
        :disabled="saving || uncertain || loading || !!current?.active_job_id"
      >
        <AFormItem label="名称"
          ><AInput v-model:value="form.name" :maxlength="100" aria-label="知识库名称"
        /></AFormItem>
        <AFormItem label="描述"
          ><ATextarea v-model:value="form.description" :maxlength="2000" aria-label="知识库描述"
        /></AFormItem>
        <AFormItem label="向量模型" required
          ><ASelect
            :value="form.embed"
            :options="embeddingOptions"
            aria-label="知识库向量模型"
            @update:value="form.embed = typeof $event === 'string' ? $event : ''"
        /></AFormItem>
        <AFormItem label="重排模型" required
          ><ASelect
            :value="form.ranker"
            :options="rerankOptions"
            aria-label="知识库重排模型"
            @update:value="form.ranker = typeof $event === 'string' ? $event : ''"
        /></AFormItem>
        <p class="settings-help">
          重排为必需步骤。联合查询的知识库设置不同时，使用默认重排模型；重排配置变化无需重新处理资料。
        </p>
        <ChunkFields v-model:size="form.size" v-model:overlap="form.overlap" />
      </AForm>
      <AAlert
        v-if="current?.active_job_id"
        type="info"
        message="资料仍在处理中，完成后可修改设置。"
        show-icon
      />
      <AAlert
        v-if="requiresRebuild"
        type="warning"
        message="这些变更需要重新处理现有资料。完成前保留原索引；失败时保留原可用内容。"
        show-icon
      />
      <ACheckbox v-if="requiresRebuild" v-model:checked="confirmed" :disabled="saving || uncertain"
        >确认重新处理现有资料并产生模型用量</ACheckbox
      >
    </ASpin>
  </AModal>
</template>
<style scoped>
.settings-help {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin: -12px 0 16px;
}
:deep(.ant-alert),
:deep(.ant-checkbox-wrapper) {
  margin-bottom: 16px;
}
</style>
