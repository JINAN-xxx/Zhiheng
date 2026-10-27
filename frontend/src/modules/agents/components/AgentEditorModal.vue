<!-- Adapted from original agent UI, MIT licensed. -->
<template>
  <a-modal
    :open="modelValue"
    class="agent-editor-modal"
    centered
    width="min(880px, calc(100vw - 32px))"
    :body-style="{ maxHeight: 'calc(100dvh - 240px)', overflowY: 'auto', paddingRight: '6px' }"
    :closable="!busy"
    :mask-closable="false"
    :keyboard="!busy"
    @cancel="close"
  >
    <template #title
      ><span class="agent-editor-title">{{ saved ? saved.name : '新建智能体' }}</span></template
    >
    <WorkspaceState v-if="loading" kind="loading" />
    <WorkspaceState v-else-if="loadError" kind="error" :message="loadError" @retry="load" />
    <template v-else>
      <div v-if="saved" class="editor-status">
        <a-tag :color="saved.status === 'published' ? 'green' : 'default'">{{
          agentStatusLabel(saved)
        }}</a-tag
        ><span>草稿 v{{ saved.version }}</span
        ><span v-if="dirty">未保存</span>
      </div>
      <a-alert v-if="error" class="editor-alert" type="error" :message="error" show-icon />
      <a-alert
        v-if="optionsError"
        class="editor-alert"
        type="warning"
        :message="optionsError"
        show-icon
      />
      <a-form
        ref="formRef"
        class="editor-form"
        :model="form"
        :rules="rules"
        layout="vertical"
        :disabled="!canEdit || busy"
      >
        <a-form-item label="名称" name="name"
          ><a-input v-model:value="form.name" :maxlength="100"
        /></a-form-item>
        <a-form-item label="模型" name="modelId"
          ><a-select
            :value="form.modelId || undefined"
            show-search
            option-filter-prop="label"
            :options="modelOptions"
            :loading="optionsLoading"
            :disabled="!canEdit || busy"
            placeholder="选择模型"
            @update:value="modelChanged"
        /></a-form-item>
        <a-form-item class="editor-full-width" label="描述" name="description"
          ><a-textarea v-model:value="form.description" :rows="2" :maxlength="500"
        /></a-form-item>
        <a-form-item
          class="editor-full-width"
          label="欢迎语"
          name="welcomeMessage"
          extra="开启对话时展示的开场白，可留空。"
          ><a-textarea
            v-model:value="form.welcomeMessage"
            :rows="2"
            :maxlength="2000"
            placeholder="你好，我可以帮你解答什么问题？"
        /></a-form-item>
        <a-form-item class="editor-full-width" label="系统提示词" name="systemPrompt"
          ><a-textarea v-model:value="form.systemPrompt" :rows="5" :maxlength="32000"
        /></a-form-item>
        <a-form-item label="技能" name="skillSlugs"
          ><a-select
            :value="form.skillSlugs"
            mode="multiple"
            option-filter-prop="label"
            :options="skillOptions"
            :loading="optionsLoading"
            :disabled="!canEdit || busy"
            allow-clear
            @update:value="setSkillSlugs"
        /></a-form-item>
        <a-form-item label="知识库" name="knowledgeBaseIds">
          <a-select
            :value="form.knowledgeBaseIds"
            mode="multiple"
            option-filter-prop="label"
            :options="knowledgeOptions"
            :loading="optionsLoading"
            :disabled="!canEdit || busy"
            allow-clear
            placeholder="选择回答时引用的知识库"
            @update:value="setKnowledgeIds"
          />
          <template #extra
            ><span>{{
              knowledgeBases.length ? '检索同时校验知识库及原文权限。' : '暂无可选知识库'
            }}</span></template
          >
        </a-form-item>
        <a-form-item class="editor-full-width" label="API 应用" name="applicationId">
          <div class="application-field">
            <a-select
              :value="form.applicationId ?? undefined"
              show-search
              allow-clear
              option-filter-prop="label"
              :options="applicationOptions"
              :loading="optionsLoading"
              :disabled="!canEdit || busy"
              placeholder="选择应用"
              @update:value="setApplicationId"
            />
            <a-tooltip v-if="canEdit && canCreateApplication" title="新建 API 应用"
              ><a-button :disabled="busy" aria-label="新建 API 应用" @click="openApplication"
                ><template #icon><PlusOutlined /></template></a-button
            ></a-tooltip>
          </div>
        </a-form-item>
        <div class="sampling-fields editor-full-width">
          <a-form-item label="温度" name="temperature"
            ><a-input-number
              :value="form.temperature ?? ''"
              :min="0"
              :max="2"
              :step="0.1"
              @update:value="setTemperature"
          /></a-form-item>
          <a-form-item label="最大输出 Token" name="maxTokens"
            ><a-input-number
              :value="form.maxTokens ?? ''"
              :min="1"
              :max="131072"
              :precision="0"
              placeholder="2048"
              @update:value="setMaxTokens"
          /></a-form-item>
        </div>
      </a-form>
    </template>
    <template #footer>
      <div class="editor-footer">
        <div class="editor-footer__secondary">
          <a-tooltip
            v-if="saved && canEdit"
            :title="saved.status === 'published' ? '取消发布后可删除' : '删除智能体'"
            ><a-button
              danger
              :disabled="busy || saved.status === 'published'"
              aria-label="删除智能体"
              @click="remove"
              ><template #icon><DeleteOutlined /></template></a-button
          ></a-tooltip>
          <a-button
            v-if="saved?.status === 'published' && canEdit"
            :disabled="busy"
            aria-label="取消发布"
            @click="unpublish"
            ><StopOutlined />取消发布</a-button
          >
        </div>
        <div class="editor-footer__primary">
          <a-tooltip v-if="saved && canEdit" :title="dirty ? '保存后试聊' : '试聊已保存草稿'"
            ><a-button :disabled="busy || dirty" aria-label="试聊" @click="$emit('preview', saved)"
              ><MessageOutlined />试聊</a-button
            ></a-tooltip
          >
          <a-button
            v-if="canEdit"
            :loading="saving"
            :disabled="(busy && !saving) || loading || !!loadError"
            aria-label="保存"
            @click="save"
            ><SaveOutlined />保存</a-button
          >
          <a-tooltip
            v-if="saved && canEdit"
            :title="
              dirty ? '保存后发布' : !saved.applicationId ? '绑定 API 应用后发布' : '发布当前草稿'
            "
            ><a-button
              type="primary"
              :disabled="
                busy ||
                dirty ||
                !saved.applicationId ||
                (saved.status === 'published' && !hasUnpublishedAgentChanges(saved))
              "
              aria-label="发布"
              @click="publish"
              ><CloudUploadOutlined />发布</a-button
            ></a-tooltip
          >
        </div>
      </div>
    </template>
  </a-modal>
  <a-modal
    v-model:open="applicationOpen"
    title="新建 API 应用"
    :z-index="1100"
    :confirm-loading="applicationSaving"
    :closable="!applicationSaving"
    :mask-closable="false"
    @ok="saveApplication"
  >
    <a-alert
      v-if="applicationError"
      class="editor-alert"
      type="error"
      :message="applicationError"
      show-icon
    />
    <a-form
      ref="applicationFormRef"
      :model="applicationForm"
      :rules="applicationRules"
      layout="vertical"
      :disabled="applicationSaving"
    >
      <a-form-item label="应用名称" name="applicationName"
        ><a-input v-model:value="applicationForm.applicationName" :maxlength="80"
      /></a-form-item>
      <a-form-item label="应用编码" name="applicationCode"
        ><a-input v-model:value="applicationForm.applicationCode" :maxlength="80"
      /></a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  Modal as AModal,
  Select as ASelect,
  Tag as ATag,
  Textarea as ATextarea,
  Tooltip as ATooltip
} from 'ant-design-vue'
import type { FormInstance } from 'ant-design-vue'
import {
  CloudUploadOutlined,
  DeleteOutlined,
  MessageOutlined,
  PlusOutlined,
  SaveOutlined,
  StopOutlined
} from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { identity } from '../../identity/public.ts'
import { getModels } from '../../models/public.ts'
import type { ChatModel } from '../../models/public.ts'
import { listAvailableSkills } from '../../skills/public.ts'
import type { Skill } from '../../skills/public.ts'
import { createApplication, listApplications } from '../../developer/public.ts'
import type { Application } from '../../developer/public.ts'
import {
  createAgent,
  deleteAgent,
  getAgent,
  publishAgent,
  unpublishAgent,
  updateAgent
} from '../api/index.ts'
import type { Agent } from '../api/index.ts'
import { availableBases } from '../../knowledge/public.ts'
import type { KnowledgeBase } from '../../knowledge/public.ts'
import {
  agentStatusLabel,
  buildAgentPayload,
  createAgentForm,
  hasUnpublishedAgentChanges,
  withCurrent
} from '../presentation.ts'
const props = defineProps<{ modelValue: boolean; agentId: string }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  changed: [agent: Agent]
  deleted: [id: string]
  preview: [agent: Agent]
}>()
const saved = ref<Agent | null>(null),
  form = reactive(createAgentForm()),
  formRef = ref<FormInstance>()
const loading = ref(false),
  loadError = ref(''),
  saving = ref(false),
  actionBusy = ref(false),
  error = ref(''),
  optionsLoading = ref(false),
  optionsError = ref('')
const models = ref<ChatModel[]>([]),
  skills = ref<Skill[]>([]),
  applications = ref<Application[]>([])
const knowledgeBases = ref<KnowledgeBase[]>([])
const applicationOpen = ref(false),
  applicationSaving = ref(false),
  applicationError = ref(''),
  applicationFormRef = ref<FormInstance>(),
  applicationForm = reactive({ applicationName: '', applicationCode: '' })
const readScope = new RequestScope(),
  mutationScope = new RequestScope(),
  applicationScope = new RequestScope(),
  confirmation = useConfirmation()
const busy = computed(
  () => loading.value || saving.value || actionBusy.value || applicationSaving.value
)
const canEdit = computed(
  () => !saved.value || identity.value?.is_admin || identity.value?.id === saved.value.ownerId
)
const canCreateApplication = computed(
  () => !saved.value || saved.value.ownerId === identity.value?.id
)
const dirty = computed(
  () =>
    JSON.stringify(buildAgentPayload(form)) !==
    JSON.stringify(buildAgentPayload(saved.value ?? createAgentForm()))
)
const modelOptions = computed(() =>
  withCurrent(
    models.value
      .filter((model) => model.enabled && model.published && model.model_type === 'chat')
      .map((model) => ({ value: model.id, label: model.name })),
    form.modelId
  )
)
const skillOptions = computed(() =>
  withCurrent(
    skills.value.map((skill) => ({ value: skill.skillSlug, label: skill.skillName })),
    form.skillSlugs
  )
)
const knowledgeOptions = computed(() =>
  withCurrent(
    knowledgeBases.value.map((base) => ({ value: base.id, label: base.name })),
    form.knowledgeBaseIds
  )
)
const applicationOptions = computed(() =>
  withCurrent(
    applications.value
      .filter(
        (app) =>
          app.status === 'active' && app.ownerId === (saved.value?.ownerId ?? identity.value?.id)
      )
      .map((app) => ({ value: app.applicationId, label: app.applicationName })),
    form.applicationId
  )
)
const rules = {
  name: [{ required: true, whitespace: true, message: '请输入名称' }],
  modelId: [{ required: true, message: '请选择模型' }],
  skillSlugs: [{ type: 'array' as const, max: 5, message: '最多选择 5 个技能' }],
  knowledgeBaseIds: [{ type: 'array' as const, max: 20, message: '最多绑定 20 个知识库' }]
}
const applicationRules = {
  applicationName: [
    { required: true, whitespace: true, max: 80, message: '请输入80字以内的应用名称' }
  ],
  applicationCode: [
    { required: true, message: '请输入应用编码' },
    {
      pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/,
      message: '仅支持字母、数字、点、下划线和短横线'
    }
  ]
}
async function allApplications(signal: AbortSignal): Promise<Application[]> {
  const rows: Application[] = []
  let page = 1
  while (true) {
    const result = await listApplications({ page, page_size: 100, search: '' }, signal)
    rows.push(...result.rows)
    if (result.rows.length < 100 || page * 100 >= result.total) return rows
    page += 1
  }
}
async function load(): Promise<void> {
  if (!props.modelValue) return
  const context = readScope.replace()
  loading.value = true
  loadError.value = ''
  optionsLoading.value = true
  optionsError.value = ''
  const results = await Promise.allSettled([
    props.agentId ? getAgent(props.agentId, context.signal) : Promise.resolve(null),
    getModels(context.signal),
    listAvailableSkills(context.signal),
    allApplications(context.signal),
    availableBases(context.signal, props.agentId || undefined)
  ])
  if (!context.isCurrent()) return
  const [detail, modelList, skillList, applicationList, knowledgeList] = results
  if (detail.status === 'fulfilled') {
    saved.value = detail.value
    Object.assign(form, createAgentForm(detail.value ?? undefined))
  } else if (!isAborted(detail.reason)) loadError.value = errorMessage(detail.reason)
  const failures: string[] = []
  if (modelList.status === 'fulfilled') models.value = modelList.value
  else if (!isAborted(modelList.reason))
    failures.push('模型加载失败：' + errorMessage(modelList.reason))
  if (skillList.status === 'fulfilled') skills.value = skillList.value
  else if (!isAborted(skillList.reason))
    failures.push('技能加载失败：' + errorMessage(skillList.reason))
  if (applicationList.status === 'fulfilled') applications.value = applicationList.value
  else if (!isAborted(applicationList.reason))
    failures.push('应用加载失败：' + errorMessage(applicationList.reason))
  if (knowledgeList.status === 'fulfilled') knowledgeBases.value = knowledgeList.value
  else if (!isAborted(knowledgeList.reason))
    failures.push('知识库加载失败：' + errorMessage(knowledgeList.reason))
  optionsError.value = failures.join('；')
  optionsLoading.value = false
  loading.value = false
}
function acceptAgent(next: Agent): void {
  saved.value = next
  Object.assign(form, createAgentForm(next))
  emit('changed', next)
}
async function save(): Promise<void> {
  if (!canEdit.value || busy.value) return
  const context = mutationScope.replace()
  if (
    !(await formRef.value
      ?.validate()
      .then(() => true)
      .catch(() => false)) ||
    !context.isCurrent()
  )
    return
  const payload = buildAgentPayload(form),
    current = saved.value
  if (
    !Number.isFinite(payload.temperature) ||
    payload.temperature < 0 ||
    payload.temperature > 2 ||
    !Number.isInteger(payload.max_tokens) ||
    payload.max_tokens < 1 ||
    payload.max_tokens > 131072
  ) {
    error.value = '请填写有效温度和输出上限'
    return
  }
  const model = models.value.find((item) => item.id === payload.model_id)
  if (model && payload.max_tokens > model.max_output_tokens) {
    error.value = `输出上限不能超过模型配置的 ${model.max_output_tokens}`
    return
  }
  saving.value = true
  error.value = ''
  try {
    const next = await (current
      ? updateAgent(
          current.agentId,
          { ...payload, expected_version: current.version },
          context.signal
        )
      : createAgent(payload, context.signal))
    if (context.isCurrent()) {
      readScope.replace()
      acceptAgent(next)
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
async function publish(): Promise<void> {
  const item = saved.value
  if (!item || dirty.value || busy.value || !canEdit.value) return
  const context = mutationScope.replace()
  actionBusy.value = true
  error.value = ''
  try {
    const next = await publishAgent(item.agentId, item.version, context.signal)
    if (context.isCurrent()) acceptAgent(next)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) actionBusy.value = false
  }
}
async function unpublish(): Promise<void> {
  const item = saved.value
  if (!item || busy.value || !canEdit.value) return
  const context = mutationScope.replace()
  if (
    !(await confirmation.confirm('取消发布后，新的 API 调用将立即停止。继续取消发布？')) ||
    !context.isCurrent()
  )
    return
  actionBusy.value = true
  error.value = ''
  try {
    const next = await unpublishAgent(item.agentId, item.version, context.signal)
    if (context.isCurrent()) {
      saved.value = next
      emit('changed', next)
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) actionBusy.value = false
  }
}
async function remove(): Promise<void> {
  const item = saved.value
  if (!item || busy.value || item.status === 'published' || !canEdit.value) return
  const context = mutationScope.replace()
  if (!(await confirmation.confirm(`删除智能体「${item.name}」？`)) || !context.isCurrent()) return
  actionBusy.value = true
  error.value = ''
  try {
    await deleteAgent(item.agentId, context.signal)
    if (context.isCurrent()) {
      emit('deleted', item.agentId)
      emit('update:modelValue', false)
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) actionBusy.value = false
  }
}
async function close(): Promise<void> {
  if (busy.value) return
  const context = mutationScope.current()
  if (dirty.value && !(await confirmation.confirm('尚有未保存更改，确定关闭？'))) return
  if (context.isCurrent()) emit('update:modelValue', false)
}
function openApplication(): void {
  if (!canCreateApplication.value) return
  applicationScope.replace()
  applicationForm.applicationName = form.name.slice(0, 80)
  applicationForm.applicationCode = ''
  applicationError.value = ''
  applicationOpen.value = true
}
async function saveApplication(): Promise<void> {
  if (!canCreateApplication.value || applicationSaving.value) return
  const context = applicationScope.replace()
  if (
    !(await applicationFormRef.value
      ?.validate()
      .then(() => true)
      .catch(() => false)) ||
    !context.isCurrent()
  )
    return
  applicationSaving.value = true
  applicationError.value = ''
  try {
    const app = await createApplication(
      {
        name: applicationForm.applicationName.trim(),
        code: applicationForm.applicationCode.trim()
      },
      context.signal
    )
    if (context.isCurrent() && props.modelValue) {
      applications.value.push(app)
      form.applicationId = app.applicationId
      applicationOpen.value = false
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) applicationError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) applicationSaving.value = false
  }
}
function setSkillSlugs(value: unknown): void {
  form.skillSlugs = Array.isArray(value)
    ? value.filter((item: unknown): item is string => typeof item === 'string')
    : []
}
function setKnowledgeIds(value: unknown): void {
  form.knowledgeBaseIds = Array.isArray(value)
    ? value.filter((item: unknown): item is string => typeof item === 'string')
    : []
}
function setApplicationId(value: unknown): void {
  form.applicationId = typeof value === 'string' ? value : null
}
function setTemperature(value: unknown): void {
  form.temperature = typeof value === 'number' ? value : null
}
function setMaxTokens(value: unknown): void {
  form.maxTokens = typeof value === 'number' ? value : null
}
function modelChanged(value: unknown): void {
  if (typeof value !== 'string') return
  form.modelId = value
  const model = models.value.find((item) => item.id === value)
  if (model && form.maxTokens !== null)
    form.maxTokens = Math.min(form.maxTokens, model.max_output_tokens)
}
watch(
  () => [props.modelValue, props.agentId],
  () => {
    confirmation.cancel()
    readScope.replace()
    mutationScope.replace()
    applicationScope.replace()
    saved.value = null
    Object.assign(form, createAgentForm())
    error.value = ''
    loadError.value = ''
    models.value = []
    skills.value = []
    applications.value = []
    loading.value = false
    saving.value = false
    actionBusy.value = false
    applicationOpen.value = false
    applicationSaving.value = false
    if (props.modelValue) void load()
  },
  { immediate: true }
)
watch(applicationOpen, (open) => {
  if (!open) {
    applicationScope.replace()
    applicationSaving.value = false
    applicationError.value = ''
  }
})
onBeforeUnmount(() => {
  readScope.dispose()
  mutationScope.dispose()
  applicationScope.dispose()
})
</script>

<style scoped>
.agent-editor-title {
  display: block;
  padding-right: 24px;
  overflow-wrap: anywhere;
}
.editor-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 20px;
}
.editor-form > * {
  min-width: 0;
}
.editor-full-width {
  grid-column: 1 / -1;
}
.editor-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.editor-status :deep(.ant-tag) {
  margin: 0;
  white-space: normal;
}
.editor-alert {
  margin-bottom: 16px;
}
.application-field {
  display: flex;
  align-items: center;
  gap: 8px;
}
.application-field :deep(.ant-select) {
  min-width: 0;
  flex: 1;
}
.sampling-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.sampling-fields :deep(.ant-input-number) {
  width: 100%;
}
.editor-footer,
.editor-footer__secondary,
.editor-footer__primary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.editor-footer {
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid var(--gray-150);
}
@media (max-width: 520px) {
  .editor-form {
    grid-template-columns: minmax(0, 1fr);
  }
  .editor-footer__primary {
    margin-left: auto;
  }
  .sampling-fields {
    gap: 10px;
  }
}
</style>
