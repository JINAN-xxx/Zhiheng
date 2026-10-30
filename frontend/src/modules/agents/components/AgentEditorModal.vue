<!-- Adapted from original agent UI, MIT licensed. -->
<template>
  <component
    :is="page ? 'main' : AModal"
    :class="['agent-editor', { 'agent-editor--page': page }]"
    v-bind="
      page
        ? {}
        : {
            open: modelValue,
            width: 'min(1060px, calc(100vw - 32px))',
            footer: null,
            closable: !busy,
            maskClosable: false,
            keyboard: !busy
          }
    "
    @cancel="close"
  >
    <header class="editor-heading">
      <div class="editor-heading__title">
        <a-button
          v-if="page"
          type="text"
          class="editor-back"
          :disabled="busy"
          aria-label="返回智能体列表"
          @click="close"
          ><ArrowLeftOutlined />返回智能体</a-button
        >
        <h1>{{ saved?.name || '新建智能体' }}</h1>
        <div class="editor-status">
          <a-tag v-if="saved" :color="saved.status === 'published' ? 'green' : 'default'">{{
            agentStatusLabel(saved)
          }}</a-tag>
          <span>{{ saved ? `草稿 v${saved.version}` : '配置你的智能体' }}</span>
          <span v-if="saved?.publishedVersion">已发布 v{{ saved.publishedVersion }}</span>
          <span class="editor-save-state">{{
            dirty ? '有未保存更改' : saved ? '所有更改已保存' : ''
          }}</span>
        </div>
      </div>
      <div class="editor-actions">
        <a-tooltip v-if="saved && canEdit" title="删除智能体">
          <a-button danger type="text" :disabled="busy" aria-label="删除智能体" @click="remove"
            ><DeleteOutlined
          /></a-button>
        </a-tooltip>
        <a-button
          v-if="saved?.status === 'published'"
          :disabled="busy"
          aria-label="API 说明"
          @click="apiOpen = true"
          ><ApiOutlined />API 说明</a-button
        >
        <a-button
          v-if="saved?.status === 'published' && canEdit"
          :disabled="busy"
          aria-label="取消发布"
          @click="unpublish"
          ><StopOutlined />取消发布</a-button
        >
        <a-button
          v-if="!page && saved && canEdit"
          :disabled="busy || dirty"
          aria-label="试聊"
          @click="$emit('preview', saved)"
          ><MessageOutlined />试聊</a-button
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
        >
          <a-button
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
          >
        </a-tooltip>
      </div>
    </header>
    <WorkspaceState v-if="loading" kind="loading" />
    <WorkspaceState v-else-if="loadError" kind="error" :message="loadError" @retry="load" />
    <div v-else class="editor-layout" :class="{ 'editor-layout--page': page }">
      <div class="editor-config">
        <nav class="editor-section-nav" aria-label="配置分区">
          <button
            v-for="section in sections"
            :key="section.id"
            type="button"
            :class="{ active: activeSection === section.id }"
            @click="scrollToSection(section.id)"
          >
            {{ section.label }}
          </button>
        </nav>
        <a-alert v-if="error" class="editor-alert" type="error" :message="error" show-icon />
        <ResourceReferences :details="errorDetails" />
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
          <section id="agent-config-basic" class="config-section">
            <div class="config-heading">
              <span><RobotOutlined /></span>
              <h2>基本信息</h2>
            </div>
            <div class="config-fields">
              <a-form-item label="智能体名称" name="name"
                ><a-input v-model:value="form.name" :maxlength="100" placeholder="为智能体命名"
              /></a-form-item>
              <a-form-item label="描述" name="description"
                ><a-textarea
                  v-model:value="form.description"
                  :rows="2"
                  :maxlength="500"
                  placeholder="介绍智能体的用途"
              /></a-form-item>
              <a-form-item label="欢迎语" name="welcomeMessage" extra="开启对话时展示，可留空。"
                ><a-textarea
                  v-model:value="form.welcomeMessage"
                  :rows="2"
                  :maxlength="2000"
                  placeholder="你好，我可以帮你解答什么问题？"
              /></a-form-item>
            </div>
          </section>
          <section id="agent-config-prompt" class="config-section">
            <div class="config-heading">
              <span><MessageOutlined /></span>
              <h2>系统提示词</h2>
            </div>
            <div class="config-fields">
              <a-form-item label="系统提示词" name="systemPrompt"
                ><a-textarea
                  v-model:value="form.systemPrompt"
                  :rows="7"
                  :maxlength="32000"
                  placeholder="描述智能体的角色、任务和回答要求"
              /></a-form-item>
            </div>
          </section>
          <section id="agent-config-resources" class="config-section">
            <div class="config-heading">
              <span><BookOutlined /></span>
              <h2>技能与知识</h2>
            </div>
            <div class="config-fields">
              <a-form-item label="技能" name="skillSlugs" extra="最多选择 5 个技能。"
                ><a-select
                  :value="form.skillSlugs"
                  mode="multiple"
                  option-filter-prop="label"
                  :options="skillOptions"
                  :loading="optionsLoading"
                  :disabled="!canEdit || busy"
                  allow-clear
                  placeholder="选择技能"
                  @update:value="setSkillSlugs"
              /></a-form-item>
              <a-form-item label="知识库" name="knowledgeBaseIds"
                ><a-select
                  :value="form.knowledgeBaseIds"
                  mode="multiple"
                  option-filter-prop="label"
                  :options="knowledgeOptions"
                  :loading="optionsLoading"
                  :disabled="!canEdit || busy"
                  allow-clear
                  placeholder="选择回答时引用的知识库"
                  @update:value="setKnowledgeIds"
                /><template #extra>{{
                  knowledgeBases.length ? '检索同时校验知识库及原文权限。' : '暂无可选知识库'
                }}</template></a-form-item
              >
            </div>
          </section>
          <section id="agent-config-runtime" class="config-section">
            <div class="config-heading">
              <span><SettingOutlined /></span>
              <h2>模型与发布</h2>
            </div>
            <div class="config-fields">
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
              <div class="sampling-fields">
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
              <a-form-item label="API 应用" name="applicationId"
                ><div class="application-field">
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
                  /><a-tooltip v-if="canEdit && canCreateApplication" title="新建 API 应用"
                    ><a-button :disabled="busy" aria-label="新建 API 应用" @click="openApplication"
                      ><PlusOutlined /></a-button
                  ></a-tooltip></div
              ></a-form-item>
            </div>
          </section>
        </a-form>
      </div>
      <aside v-if="page" class="editor-preview-column">
        <AgentPreviewDrawer
          :model-value="true"
          :agent="saved"
          embedded
          :disabled="dirty || !saved || loading || !!loadError || !canEdit"
          @busy-change="previewBusy = $event"
        />
        <section class="publication-note">
          <ThunderboltOutlined />
          <div>
            <h3>发布状态</h3>
            <p>
              {{
                saved?.publishedVersion
                  ? `API 使用已发布的 v${saved.publishedVersion} 配置。`
                  : '发布后可通过 API 调用。'
              }}
            </p>
            <p>
              {{ dirty ? '保存更改后可试聊与发布。' : '保存草稿后，需发布才会更新 API 配置。' }}
            </p>
          </div>
        </section>
      </aside>
    </div>
  </component>
  <AgentApiDrawer v-model="apiOpen" :agent="saved" />
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
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
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
  ApiOutlined,
  ArrowLeftOutlined,
  BookOutlined,
  CloudUploadOutlined,
  RobotOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  DeleteOutlined,
  MessageOutlined,
  PlusOutlined,
  SaveOutlined,
  StopOutlined
} from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import ResourceReferences from '../../../shared/components/ResourceReferences.vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
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
import AgentPreviewDrawer from './AgentPreviewDrawer.vue'
import AgentApiDrawer from './AgentApiDrawer.vue'
const props = withDefaults(
  defineProps<{ modelValue: boolean; agentId: string; page?: boolean }>(),
  { page: false }
)
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  changed: [agent: Agent]
  deleted: [id: string]
  preview: [agent: Agent]
}>()
const apiOpen = ref(false),
  previewBusy = ref(false)
const sections = [
  { id: 'basic', label: '基本信息' },
  { id: 'prompt', label: '提示词' },
  { id: 'resources', label: '技能与知识' },
  { id: 'runtime', label: '模型与发布' }
]
const activeSection = ref('basic')
function scrollToSection(id: string): void {
  activeSection.value = id
  document
    .getElementById(`agent-config-${id}`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
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
const errorDetails = ref<unknown>()
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
  () =>
    loading.value ||
    saving.value ||
    actionBusy.value ||
    applicationSaving.value ||
    previewBusy.value
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
  errorDetails.value = undefined
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
  errorDetails.value = undefined
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
  errorDetails.value = undefined
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
  if (!item || busy.value || !canEdit.value) return
  const context = mutationScope.replace()
  if (
    !(await confirmation.confirm(
      `删除智能体「${item.name}」？已发布的智能体将自动下线，历史版本和运行记录仍会保留。`
    )) ||
    !context.isCurrent()
  )
    return
  actionBusy.value = true
  error.value = ''
  errorDetails.value = undefined
  try {
    await deleteAgent(item.agentId, context.signal)
    if (context.isCurrent()) {
      emit('deleted', item.agentId)
      emit('update:modelValue', false)
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      errorDetails.value = reason instanceof ApiError ? reason.details : undefined
    }
  } finally {
    if (context.isCurrent()) actionBusy.value = false
  }
}
let navigationConfirmed = false
async function canLeave(): Promise<boolean> {
  if (!props.page) return true
  if (busy.value) return false
  if (navigationConfirmed || !dirty.value) return true
  return confirmation.confirm('尚有未保存更改，确定离开？')
}
onBeforeRouteLeave(canLeave)
onBeforeRouteUpdate(canLeave)
function beforeUnload(event: BeforeUnloadEvent): void {
  if (props.page && dirty.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
async function close(): Promise<void> {
  if (busy.value) return
  const context = mutationScope.current()
  if (dirty.value && !(await confirmation.confirm('尚有未保存更改，确定关闭？'))) return
  if (context.isCurrent()) {
    navigationConfirmed = true
    emit('update:modelValue', false)
  }
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
    navigationConfirmed = false
    apiOpen.value = false
    confirmation.cancel()
    readScope.replace()
    mutationScope.replace()
    applicationScope.replace()
    saved.value = null
    Object.assign(form, createAgentForm())
    error.value = ''
    errorDetails.value = undefined
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
  window.removeEventListener('beforeunload', beforeUnload)
  readScope.dispose()
  mutationScope.dispose()
  applicationScope.dispose()
})
</script>

<style scoped>
.agent-editor {
  color: var(--color-text);
  font-size: 14px;
}
.agent-editor--page {
  height: 100%;
  min-height: 0;
  overflow: auto;
  padding: 24px 28px 36px;
  background: var(--bg-page);
}
.editor-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 22px;
}
.agent-editor--page .editor-heading {
  position: sticky;
  top: 0;
  z-index: 5;
  padding-bottom: 18px;
  margin-bottom: 4px;
  background: var(--bg-page);
  box-shadow: 0 -24px 0 var(--bg-page);
}
.editor-heading__title {
  min-width: 0;
  flex: 1;
}
.editor-heading h1 {
  margin: 0 0 10px;
  overflow-wrap: anywhere;
  font-size: 22px;
  font-weight: 500;
}
.editor-back {
  height: auto;
  margin: 0 0 14px -8px;
  padding-block: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.editor-actions,
.editor-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.editor-status {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.editor-status :deep(.ant-tag) {
  margin: 0;
}
.editor-save-state {
  color: var(--main-700);
}
.editor-layout {
  min-width: 0;
}
.editor-layout--page {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 350px;
  align-items: start;
  gap: 22px;
}
.editor-config {
  min-width: 0;
}
.editor-section-nav {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  overflow-x: auto;
}
.editor-section-nav button {
  padding: 8px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 5px;
  background: #fff;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
}
.editor-section-nav button.active {
  color: var(--main-700);
  background: var(--main-50);
  border-color: var(--main-100);
  font-weight: 600;
}
.config-section {
  margin-bottom: 18px;
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: #fff;
  scroll-margin-top: 140px;
}
.config-heading {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 18px 22px;
  border-bottom: 1px solid var(--gray-100);
}
.config-heading > span {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: 7px;
  color: var(--main-700);
  background: var(--main-50);
}
.config-heading h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 500;
}
.config-fields {
  padding: 22px 22px 2px;
}
.config-fields :deep(.ant-form-item) {
  margin-bottom: 22px;
}
.config-fields :deep(.ant-form-item-label > label) {
  font-size: 14px;
  font-weight: 400;
  color: var(--color-text);
}
.config-fields :deep(.ant-form-item-extra) {
  margin-top: 6px;
  font-size: 12px;
  color: var(--color-text-secondary);
}
.sampling-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}
.sampling-fields :deep(.ant-input-number) {
  width: 100%;
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
.editor-alert {
  margin-bottom: 16px;
}
.editor-preview-column {
  position: sticky;
  top: 130px;
  min-width: 0;
}
.publication-note {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-top: 16px;
  padding: 18px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: #fff;
  color: var(--main-700);
}
.publication-note h3 {
  margin: 0 0 10px;
  color: var(--color-text);
  font-size: 14px;
  font-weight: 500;
}
.publication-note p {
  margin: 0 0 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.8;
}
@media (max-width: 1200px) {
  .editor-layout--page {
    grid-template-columns: minmax(0, 1fr) 310px;
    gap: 16px;
  }
  .agent-editor--page {
    padding: 20px;
  }
  .editor-actions {
    width: 100%;
  }
  .editor-preview-column {
    top: 160px;
  }
  .config-section {
    scroll-margin-top: 180px;
  }
}
@media (max-width: 950px) {
  .editor-layout--page {
    grid-template-columns: minmax(0, 1fr);
  }
  .editor-preview-column {
    position: static;
  }
}
@media (max-width: 600px) {
  .agent-editor--page .editor-heading {
    position: static;
  }
  .config-section {
    scroll-margin-top: 16px;
  }
  .agent-editor--page {
    padding: 16px;
  }
  .config-heading {
    padding: 16px;
  }
  .config-fields {
    padding: 16px 16px 0;
  }
  .editor-heading h1 {
    font-size: 20px;
  }
  .sampling-fields {
    gap: 12px;
  }
}
</style>
