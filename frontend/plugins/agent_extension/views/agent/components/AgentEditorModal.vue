<template>
  <a-modal :open="modelValue" class="agent-editor-modal" centered width="min(880px, calc(100vw - 32px))" :body-style="{ maxHeight: 'calc(100dvh - 240px)', overflowY: 'auto', paddingRight: '6px' }" :closable="!busy" :mask-closable="false" :keyboard="!busy" @cancel="close">
    <template #title><span class="agent-editor-title">{{ saved ? saved.name : '新建智能体' }}</span></template>
    <WorkspaceState v-if="loading" kind="loading" />
    <WorkspaceState v-else-if="loadError" kind="error" :message="loadError" @retry="load" />
    <template v-else>
      <div v-if="saved" class="editor-status"><a-tag :color="saved.status === 'published' ? 'green' : undefined">{{ agentStatusLabel(saved) }}</a-tag><span>草稿 v{{ saved.version }}</span><span v-if="dirty">未保存</span></div>
      <a-alert v-if="error" class="editor-alert" type="error" :message="error" show-icon />
      <a-alert v-if="optionsError" class="editor-alert" type="warning" :message="optionsError" show-icon />
      <a-form ref="formRef" class="editor-form" :model="form" :rules="rules" layout="vertical" :disabled="!canEdit || busy">
        <a-form-item label="名称" name="name"><a-input v-model:value="form.name" :maxlength="100" /></a-form-item>
        <a-form-item label="模型" name="modelId"><a-select v-model:value="form.modelId" show-search option-filter-prop="label" :options="modelOptions" :loading="optionsLoading" :disabled="!canEdit || busy || !can('ai:model:list')" placeholder="选择模型" /></a-form-item>
        <a-form-item class="editor-full-width" label="描述" name="description"><a-textarea v-model:value="form.description" :rows="2" :maxlength="500" /></a-form-item>
        <a-form-item class="editor-full-width" label="欢迎语" name="welcomeMessage" extra="开启对话时展示的开场白，可留空。"><a-textarea v-model:value="form.welcomeMessage" :rows="2" :maxlength="2000" placeholder="你好，我可以帮你解答什么问题？" /></a-form-item>
        <a-form-item class="editor-full-width" label="系统提示词" name="systemPrompt"><a-textarea v-model:value="form.systemPrompt" :rows="5" :maxlength="32000" /></a-form-item>
        <a-form-item label="技能" name="skillSlugs"><a-select v-model:value="form.skillSlugs" mode="multiple" option-filter-prop="label" :options="skillOptions" :loading="optionsLoading" :disabled="!canEdit || busy || !can('agent_extension:skill:use')" allow-clear /></a-form-item>
        <a-form-item label="知识库" name="knowledgeBaseIds">
          <a-select v-model:value="form.knowledgeBaseIds" mode="multiple" option-filter-prop="label" :options="knowledgeOptions" :loading="optionsLoading" :disabled="!canEdit || busy || !can('ai:knowledge:query')" allow-clear placeholder="选择回答时引用的知识库" />
          <template #extra><a-button v-if="knowledgeError && can('ai:knowledge:query')" type="link" size="small" :loading="knowledgeLoading" @click="reloadKnowledge">知识库加载失败，点击重试</a-button><span v-else-if="!can('ai:knowledge:query')">需要知识库检索权限</span></template>
        </a-form-item>
        <a-form-item class="editor-full-width" label="API 应用" name="applicationId">
          <div class="application-field">
            <a-select v-model:value="form.applicationId" show-search allow-clear option-filter-prop="label" :options="applicationOptions" :loading="optionsLoading" :disabled="!canEdit || busy || !can('ai:application:list')" placeholder="选择应用" />
            <a-tooltip v-if="canEdit && can('ai:application:add')" title="新建 API 应用"><a-button :disabled="busy" aria-label="新建 API 应用" @click="openApplication"><template #icon><PlusOutlined /></template></a-button></a-tooltip>
          </div>
        </a-form-item>
        <div class="sampling-fields editor-full-width">
          <a-form-item label="温度" name="temperature"><a-input-number v-model:value="form.temperature" :min="0" :max="2" :step="0.1" /></a-form-item>
          <a-form-item label="最大输出 Token" name="maxTokens"><a-input-number v-model:value="form.maxTokens" :min="1" :max="131072" :precision="0" placeholder="2048" /></a-form-item>
        </div>
      </a-form>
    </template>
    <template #footer>
      <div class="editor-footer">
        <div class="editor-footer__secondary">
          <a-tooltip v-if="saved && can('agent_extension:agent:remove')" :title="saved.status === 'published' ? '取消发布后可删除' : '删除智能体'"><a-button danger :disabled="busy || saved.status === 'published'" aria-label="删除智能体" @click="remove"><template #icon><DeleteOutlined /></template></a-button></a-tooltip>
          <a-button v-if="saved?.status === 'published' && can('agent_extension:agent:publish')" :disabled="busy" @click="unpublish"><StopOutlined />取消发布</a-button>
        </div>
        <div class="editor-footer__primary">
          <a-tooltip v-if="saved && can('agent_extension:agent:test')" :title="dirty ? '保存后试聊' : '试聊已保存草稿'"><a-button :disabled="busy || dirty" @click="$emit('preview', saved)"><MessageOutlined />试聊</a-button></a-tooltip>
          <a-button v-if="canEdit" :loading="saving" :disabled="busy && !saving || loading || !!loadError" @click="save"><SaveOutlined />保存</a-button>
          <a-tooltip v-if="saved && can('agent_extension:agent:publish')" :title="dirty ? '保存后发布' : !saved.applicationId ? '绑定 API 应用后发布' : '发布当前草稿'"><a-button type="primary" :disabled="busy || dirty || !saved.applicationId || (saved.status === 'published' && saved.version === saved.publishedVersion)" @click="publish"><CloudUploadOutlined />发布</a-button></a-tooltip>
        </div>
      </div>
    </template>
  </a-modal>
  <a-modal v-model:open="applicationOpen" title="新建 API 应用" :z-index="1100" :confirm-loading="applicationSaving" :closable="!applicationSaving" :mask-closable="false" @ok="saveApplication">
    <a-alert v-if="applicationError" class="editor-alert" type="error" :message="applicationError" show-icon />
    <a-form ref="applicationFormRef" :model="applicationForm" :rules="applicationRules" layout="vertical" :disabled="applicationSaving">
      <a-form-item label="应用名称" name="applicationName"><a-input v-model:value="applicationForm.applicationName" :maxlength="100" /></a-form-item>
      <a-form-item label="应用编码" name="applicationCode"><a-input v-model:value="applicationForm.applicationCode" :maxlength="100" /></a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { CloudUploadOutlined, DeleteOutlined, MessageOutlined, PlusOutlined, SaveOutlined, StopOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import { createAgent, deleteAgent, getAgent, publishAgent, unpublishAgent, updateAgent } from '../../../api/agent.js'
import { listAvailableSkills } from '../../../api/skill.js'
import { agentStatusLabel, buildAgentPayload, createAgentForm, normalizeAgent } from '../../../utils/agent.js'
import { hasSkillPermission, normalizeAvailableSkills } from '../../../utils/skill.js'
import {
  listPlatformModels, listAvailableKnowledgeBases, normalizeKnowledgeDatabases,
  isCallablePlatformModel, normalizeApplicationModelPage, collectAllPages,
} from '../../../../ai/public/resourceSelection.js'
import { createApplication, listApplications, normalizeApplication, normalizeApplicationPage } from '../../../../ai/public/applications.js'

const props = defineProps({ modelValue: Boolean, agentId: { type: String, default: '' }, permissions: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue', 'changed', 'deleted', 'preview'])
const can = (permission) => hasSkillPermission(props.permissions, permission)
const saved = ref(null)
const form = reactive(createAgentForm())
const formRef = ref()
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const actionBusy = ref(false)
const error = ref('')
const optionsLoading = ref(false)
const optionsError = ref('')
const models = ref([])
const skills = ref([])
const applications = ref([])
const knowledgeBases = ref([])
const knowledgeError = ref(false)
const knowledgeLoading = ref(false)
const applicationOpen = ref(false)
const applicationSaving = ref(false)
const applicationError = ref('')
const applicationFormRef = ref()
const applicationForm = reactive({ applicationName: '', applicationCode: '' })
const busy = computed(() => loading.value || saving.value || actionBusy.value || applicationSaving.value)
const canEdit = computed(() => can(saved.value ? 'agent_extension:agent:edit' : 'agent_extension:agent:add'))
const dirty = computed(() => JSON.stringify(buildAgentPayload(form)) !== JSON.stringify(buildAgentPayload(saved.value || {})))
const modelOptions = computed(() => withCurrent(models.value.filter((model) => (model.modelType ?? model.model_type) === 'chat' && isCallablePlatformModel(model)).map((model) => ({ value: model.modelId ?? model.model_id, label: model.displayName || model.display_name || model.title || model.modelName || model.model_name || model.modelCode || model.model_code || model.modelId || model.model_id })), form.modelId))
const skillOptions = computed(() => withCurrent(skills.value.map((skill) => ({ value: skill.skillSlug, label: skill.skillName })), form.skillSlugs))
const knowledgeOptions = computed(() => withCurrent(knowledgeBases.value.map((item) => ({ value: item.db_id, label: item.name })), form.knowledgeBaseIds))
const applicationOptions = computed(() => withCurrent(applications.value.filter((app) => app.status === 'active').map((app) => ({ value: app.applicationId, label: app.applicationName })), form.applicationId))
const rules = {
  name: [{ required: true, whitespace: true, message: '请输入名称' }],
  modelId: [{ required: true, message: '请选择模型' }],
  skillSlugs: [{ type: 'array', max: 5, message: '最多选择 5 个技能' }],
  knowledgeBaseIds: [{ type: 'array', max: 20, message: '最多绑定 20 个知识库' }],
}
const applicationRules = {
  applicationName: [{ required: true, whitespace: true, message: '请输入应用名称' }],
  applicationCode: [{ required: true, message: '请输入应用编码' }, { pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/, message: '仅支持字母、数字、点、下划线和短横线' }],
}
let generation = 0

function withCurrent(options, current) {
  const values = Array.isArray(current) ? current : current ? [current] : []
  return [...options, ...values.filter((value) => !options.some((item) => item.value === value)).map((value) => ({ value, label: value, disabled: !Array.isArray(current) }))]
}

async function load() {
  const requestId = ++generation
  saved.value = null
  Object.assign(form, createAgentForm())
  error.value = ''; loadError.value = ''; optionsError.value = ''
  loading.value = true
  optionsLoading.value = true
  models.value = []; skills.value = []; applications.value = []; knowledgeBases.value = []; knowledgeError.value = false
  const detail = props.agentId ? getAgent(props.agentId) : Promise.resolve(null)
  const requests = [
    detail,
    can('ai:model:list') ? collectAllPages(async (page) => normalizeApplicationModelPage(await listPlatformModels(page))) : Promise.resolve([]),
    can('agent_extension:skill:use') ? listAvailableSkills().then(normalizeAvailableSkills) : Promise.resolve([]),
    can('ai:application:list') ? collectAllPages(async (page) => normalizeApplicationPage(await listApplications(page))) : Promise.resolve([]),
    can('ai:knowledge:query') ? listAvailableKnowledgeBases().then(normalizeKnowledgeDatabases) : Promise.resolve([]),
  ]
  const results = await Promise.allSettled(requests)
  if (requestId !== generation) return
  if (results[0].status === 'fulfilled') {
    if (results[0].value) saved.value = normalizeAgent(results[0].value.data)
    Object.assign(form, createAgentForm(saved.value || {}))
  } else loadError.value = normalizeErrorMessage(results[0].reason, '智能体加载失败')
  const targets = [models, skills, applications, knowledgeBases]
  const labels = ['模型', '技能', '应用', '知识库']
  const failures = []
  results.slice(1).forEach((result, index) => {
    if (result.status === 'fulfilled') targets[index].value = result.value
    else failures.push(`${labels[index]}加载失败`)
  })
  optionsError.value = failures.join('，')
  knowledgeError.value = results[4].status === 'rejected'
  loading.value = false; optionsLoading.value = false
}

async function reloadKnowledge() {
  if (knowledgeLoading.value || !can('ai:knowledge:query')) return
  const requestId = generation
  knowledgeLoading.value = true
  try {
    const rows = normalizeKnowledgeDatabases(await listAvailableKnowledgeBases())
    if (requestId !== generation) return
    knowledgeBases.value = rows
    knowledgeError.value = false
    optionsError.value = optionsError.value.split('，').filter((item) => item !== '知识库加载失败').join('，')
  } catch {
    if (requestId === generation) knowledgeError.value = true
  } finally { knowledgeLoading.value = false }
}

function acceptAgent(response) {
  saved.value = normalizeAgent(response.data)
  Object.assign(form, createAgentForm(saved.value))
  emit('changed', saved.value)
}

async function save() {
  if (!canEdit.value || busy.value || !await formRef.value?.validate().then(() => true).catch(() => false)) return
  saving.value = true; error.value = ''
  try {
    const payload = buildAgentPayload(form, saved.value?.version)
    acceptAgent(await (saved.value ? updateAgent(saved.value.agentId, payload) : createAgent(payload)))
    feedback.msgSuccess('草稿已保存')
  } catch (reason) { error.value = normalizeErrorMessage(reason, '保存失败') }
  finally { saving.value = false }
}

async function publish() {
  if (!saved.value || dirty.value || busy.value || !can('agent_extension:agent:publish')) return
  actionBusy.value = true; error.value = ''
  try { acceptAgent(await publishAgent(saved.value.agentId, saved.value.version)); feedback.msgSuccess('已发布') }
  catch (reason) { error.value = normalizeErrorMessage(reason, '发布失败') }
  finally { actionBusy.value = false }
}

async function unpublish() {
  if (!saved.value || busy.value || !can('agent_extension:agent:publish')) return
  if (!await feedback.confirm('取消发布后，新的 API 调用将立即停止。继续取消发布？').then(() => true).catch(() => false)) return
  actionBusy.value = true; error.value = ''
  try {
    const next = normalizeAgent((await unpublishAgent(saved.value.agentId, saved.value.version)).data)
    saved.value = next
    emit('changed', next)
    feedback.msgSuccess('已取消发布')
  } catch (reason) { error.value = normalizeErrorMessage(reason, '取消发布失败') }
  finally { actionBusy.value = false }
}

async function remove() {
  if (!saved.value || busy.value || saved.value.status === 'published' || !can('agent_extension:agent:remove')) return
  if (!await feedback.confirm(`删除智能体「${saved.value.name}」？`).then(() => true).catch(() => false)) return
  actionBusy.value = true; error.value = ''
  try { await deleteAgent(saved.value.agentId); emit('deleted', saved.value.agentId); emit('update:modelValue', false) }
  catch (reason) { error.value = normalizeErrorMessage(reason, '删除失败') }
  finally { actionBusy.value = false }
}

async function close() {
  if (busy.value) return
  if (dirty.value && !await feedback.confirm('尚有未保存更改，确定关闭？').then(() => true).catch(() => false)) return
  emit('update:modelValue', false)
}

function openApplication() {
  applicationForm.applicationName = form.name
  applicationForm.applicationCode = ''
  applicationError.value = ''
  applicationOpen.value = true
}

async function saveApplication() {
  if (!can('ai:application:add') || !await applicationFormRef.value?.validate().then(() => true).catch(() => false)) return
  applicationSaving.value = true; applicationError.value = ''
  try {
    const application = normalizeApplication((await createApplication({ ...applicationForm })).data)
    applications.value.push(application)
    form.applicationId = application.applicationId
    applicationOpen.value = false
  } catch (reason) { applicationError.value = normalizeErrorMessage(reason, '应用创建失败') }
  finally { applicationSaving.value = false }
}

watch(() => props.modelValue, (open) => { if (open) load(); else { generation += 1; applicationOpen.value = false } })
onBeforeUnmount(() => { generation += 1 })
</script>

<style scoped>
.agent-editor-title { display: block; padding-right: 24px; overflow-wrap: anywhere; }
.editor-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 20px; }
.editor-form > * { min-width: 0; }
.editor-full-width { grid-column: 1 / -1; }
.editor-status { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 20px; color: var(--color-text-secondary); font-size: 12px; }
.editor-status :deep(.ant-tag) { margin: 0; white-space: normal; }
.editor-alert { margin-bottom: 16px; }
.application-field { display: flex; align-items: center; gap: 8px; }
.application-field :deep(.ant-select) { min-width: 0; flex: 1; }
.sampling-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.sampling-fields :deep(.ant-input-number) { width: 100%; }
.editor-footer, .editor-footer__secondary, .editor-footer__primary { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.editor-footer { justify-content: space-between; padding-top: 8px; border-top: 1px solid var(--gray-150); }
@media (max-width: 520px) { .editor-form { grid-template-columns: minmax(0, 1fr); } .editor-footer__primary { margin-left: auto; } .sampling-fields { gap: 10px; } }
</style>
