<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Drawer as ADrawer,
  Dropdown as ADropdown,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Modal as AModal,
  Select as ASelect,
  Textarea as ATextarea
} from 'ant-design-vue'
import {
  EditOutlined,
  FileTextOutlined,
  MoreOutlined,
  AppstoreOutlined,
  PlusOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import type { UpdateWorkBody, WorkOptions, WorkStatus, WorkTask } from '../api/index.ts'
import type { WorkDraft } from '../presentation.ts'
import type { WorkTurnInput } from '../composables/turns.ts'
import {
  validateWorkGoal,
  validateWorkMessage,
  validateWorkTitle,
  workExecutionLabel
} from '../presentation.ts'
import ConversationComposer from '../../../shared/components/ConversationComposer.vue'
import WorkMessageTimeline from './WorkMessageTimeline.vue'
import WorkExecutionPanel from './WorkExecutionPanel.vue'
import WorkFilesPanel from './WorkFilesPanel.vue'
import WorkResourcesForm from './WorkResourcesForm.vue'

const props = defineProps<{
  task: WorkTask
  locked: boolean
  stoppable: boolean
  stopDisabled: boolean
  canRecover: boolean
  runError: string
  retryKind: string
  canEdit: boolean
  canRun: boolean
  options: WorkOptions | null
  optionsLoading: boolean
  optionsError: string
  uploading: boolean
  mutating: boolean
  initialMessage?: string
}>()
const emit = defineEmits<{
  cancel: []
  retry: []
  retryOptions: []
  upload: [files: File[]]
  save: [changes: Omit<UpdateWorkBody, 'expected_version'>, done: () => void]
  send: [payload: WorkTurnInput, restore: () => void]
  status: [status: WorkStatus]
}>()
const message = ref(props.initialMessage ?? ''),
  fileInput = ref<HTMLInputElement | null>(null)
const editorOpen = ref(false),
  resourcesOpen = ref(false),
  filesOpen = ref(false),
  infoOpen = ref(false)
const editingTitle = ref(''),
  editingGoal = ref(''),
  resourceDraft = ref<WorkDraft>({
    goal: '',
    modelId: null,
    skillSlugs: [],
    allowCodeExecution: false
  })
const isAgent = computed(() => props.task.mode === 'agent')
const legacyMembers = computed(() => [
  ...new Set(props.task.members.map((member) => member.agentNameSnapshot))
])
const canSend = computed(
  () =>
    props.canRun &&
    isAgent.value &&
    !props.locked &&
    !props.mutating &&
    (validateWorkMessage(message.value).valid ||
      (!message.value.trim() && props.task.executionState === 'paused'))
)
const stateLabel = computed(() =>
  !isAgent.value
    ? '历史任务'
    : props.task.status === 'archived'
      ? '已归档'
      : props.task.status === 'completed'
        ? '已结束'
        : workExecutionLabel[props.task.executionState]
)
const latestRunId = computed(
  () =>
    props.task.currentRunId ??
    [...props.task.messages].reverse().find((item) => item.role === 'assistant' && item.runId)
      ?.runId
)
const runSteps = computed(() =>
  latestRunId.value
    ? props.task.steps.filter((step) => step.runId === latestRunId.value)
    : props.task.steps
)
const modelId = computed(() => props.task.configuration?.modelId ?? props.task.modelId ?? undefined)
const modelOptions = computed(() => {
  const options = (props.options?.models ?? []).map((model) => ({
    value: model.id,
    label: model.name
  }))
  return modelId.value && !options.some((item) => item.value === modelId.value)
    ? [
        {
          value: modelId.value,
          label: props.optionsLoading ? '加载模型…' : '当前模型不可用',
          disabled: true
        },
        ...options
      ]
    : options
})
const artifactCount = computed(
  () => new Set(props.task.artifacts.map((item) => item.logicalPath)).size
)
const showProgress = computed(
  () => isAgent.value && !!(props.task.plan.length || runSteps.value.length || props.stoppable)
)
function selectModel(value: unknown): void {
  if (
    typeof value !== 'string' ||
    value === modelId.value ||
    props.locked ||
    props.mutating ||
    !props.canEdit ||
    !props.options?.models.some((model) => model.id === value)
  )
    return
  emit('save', { model_id: value }, () => {})
}
const canSaveResources = computed(
  () =>
    !!resourceDraft.value.modelId &&
    !!props.options?.models.some((model) => model.id === resourceDraft.value.modelId) &&
    resourceDraft.value.skillSlugs.every((slug) =>
      props.options?.skills.some((skill) => skill.slug === slug)
    )
)
watch(
  () => props.initialMessage,
  (value) => {
    if (!message.value && value) message.value = value
  }
)
function send(): void {
  if (!canSend.value) return
  if (!message.value.trim() && props.task.executionState === 'paused') {
    emit('retry')
    return
  }
  const content = message.value.trim()
  message.value = ''
  emit('send', { message: content }, () => {
    if (!message.value) message.value = content
  })
}
function keydown(event: KeyboardEvent): void {
  if (
    event.key === 'Enter' &&
    !event.isComposing &&
    event.keyCode !== 229 &&
    !event.shiftKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey
  ) {
    event.preventDefault()
    send()
  }
}
function openEditor(): void {
  editingTitle.value = props.task.title
  editingGoal.value = props.task.goal
  editorOpen.value = true
}
function saveEditor(): void {
  const goal = editingGoal.value.trim()
  if (
    props.locked ||
    !validateWorkTitle(editingTitle.value).valid ||
    (goal ? !validateWorkGoal(goal).valid : !!props.task.goal.trim())
  )
    return
  emit('save', { title: editingTitle.value.trim(), ...(goal ? { goal } : {}) }, () => {
    editorOpen.value = false
  })
}
function openResources(): void {
  const config = props.task.configuration
  resourceDraft.value = {
    goal: props.task.goal,
    modelId: config?.modelId ?? props.task.modelId,
    skillSlugs: [...(config?.skillSlugs ?? [])],
    allowCodeExecution: config?.allowCodeExecution ?? false
  }
  resourcesOpen.value = true
  emit('retryOptions')
}
function saveResources(): void {
  if (props.locked || !canSaveResources.value || !resourceDraft.value.modelId) return
  emit(
    'save',
    {
      model_id: resourceDraft.value.modelId,
      skill_slugs: [...resourceDraft.value.skillSlugs],
      allow_code_execution: resourceDraft.value.allowCodeExecution
    },
    () => {
      resourcesOpen.value = false
    }
  )
}
function picked(event: Event): void {
  const input = event.target as HTMLInputElement,
    files = [...(input.files ?? [])]
  input.value = ''
  if (files.length && props.canRun && !props.locked) emit('upload', files)
}
</script>
<template>
  <section class="work-task-page">
    <header class="work-task-header">
      <div class="work-task-header__copy">
        <h1>{{ task.title || '新任务' }}</h1>
        <span v-if="!isAgent || task.status !== 'active'" class="work-task-status">{{
          stateLabel
        }}</span>
      </div>
      <div class="work-task-header__actions">
        <AButton
          v-if="isAgent && canEdit && (task.status !== 'active' || !canRun)"
          type="text"
          aria-label="资源与能力"
          :disabled="locked || mutating"
          @click="openResources"
          ><SettingOutlined /></AButton
        ><AButton
          v-if="isAgent"
          type="text"
          class="work-task-header__files"
          aria-label="查看资料与成果"
          @click="filesOpen = true"
          ><FileTextOutlined /><span>资料与成果</span
          ><span v-if="artifactCount" class="work-file-count">{{ artifactCount }}</span></AButton
        ><ADropdown :trigger="['click']"
          ><AButton type="text" aria-label="更多任务操作"><MoreOutlined /></AButton
          ><template #overlay
            ><AMenu
              ><AMenuItem @click="infoOpen = true">查看完整目标</AMenuItem
              ><AMenuItem v-if="isAgent && canEdit" :disabled="locked" @click="openEditor"
                ><EditOutlined />编辑任务信息</AMenuItem
              ><AMenuItem
                v-if="isAgent && canEdit && task.status === 'active'"
                :disabled="locked"
                @click="emit('status', 'completed')"
                >结束任务</AMenuItem
              ><AMenuItem
                v-if="isAgent && canEdit && task.status !== 'active'"
                :disabled="locked"
                @click="emit('status', 'active')"
                >重新打开</AMenuItem
              ><AMenuItem
                v-if="isAgent && canEdit && task.status !== 'archived'"
                :disabled="locked"
                @click="emit('status', 'archived')"
                >归档任务</AMenuItem
              ></AMenu
            ></template
          ></ADropdown
        >
      </div>
    </header>
    <div class="work-task-layout">
      <div class="work-task-conversation">
        <AAlert
          v-if="!isAgent"
          class="work-task-notice"
          type="info"
          show-icon
          message="历史任务仅供查看，新建任务可自动规划并处理资料。"
        />
        <AAlert
          v-else-if="task.status !== 'active'"
          class="work-task-notice"
          :type="task.status === 'archived' ? 'info' : 'success'"
          show-icon
          :message="
            task.status === 'archived'
              ? '任务已归档，资料和成果继续保留。'
              : '任务已结束，重新打开后可以继续修改。'
          "
        />
        <AAlert
          v-if="runError"
          class="work-task-notice"
          :type="
            retryKind === 'unstarted' ||
            retryKind === 'uncertain' ||
            task.executionState === 'paused'
              ? 'info'
              : 'error'
          "
          show-icon
          :message="runError"
          ><template #action
            ><AButton
              v-if="
                isAgent &&
                task.status === 'active' &&
                (canRecover ||
                  (canRun &&
                    (retryKind === 'uncertain' ||
                      (!!retryKind && !locked && task.executionState !== 'paused'))))
              "
              size="small"
              @click="emit('retry')"
              >{{
                canRecover
                  ? '恢复查看'
                  : retryKind === 'uncertain'
                    ? '确认原请求'
                    : retryKind === 'unstarted'
                      ? '继续执行'
                      : '重新执行'
              }}</AButton
            ></template
          ></AAlert
        >
        <WorkMessageTimeline
          :messages="task.messages"
          :legacy="!isAgent"
          :show-progress="showProgress"
        >
          <template #progress
            ><WorkExecutionPanel
              :plan="task.plan"
              :steps="runSteps"
              :state="task.executionState"
              :busy="stoppable"
          /></template>
          <template #artifacts
            ><button
              v-if="isAgent && artifactCount"
              type="button"
              class="work-artifacts-link"
              @click="filesOpen = true"
            >
              <FileTextOutlined />查看成果 · {{ artifactCount }}
            </button></template
          >
        </WorkMessageTimeline>
        <ConversationComposer
          v-if="isAgent && task.status === 'active' && (canRun || stoppable)"
          v-model="message"
          class="work-composer"
          input-label="工作消息"
          :placeholder="
            task.executionState === 'waiting_input'
              ? '补充所需信息，或上传资料后继续'
              : '描述目标，或继续修改已有成果'
          "
          :max-length="32000"
          :max-rows="7"
          :disabled="locked || !canRun"
          :running="stoppable"
          :stop-disabled="stopDisabled"
          stop-label="停止"
          :submit-disabled="!canSend"
          :submit-label="task.executionState === 'paused' && !message.trim() ? '继续执行' : '发送'"
          @keydown="keydown"
          @send="send"
          @stop="emit('cancel')"
        >
          <template #before-surface
            ><input
              ref="fileInput"
              class="work-composer__input"
              type="file"
              multiple
              aria-label="选择工作资料"
              @change="picked"
          /></template>
          <template #prefix>
            <div v-if="task.inputs.length" class="work-inputs" aria-label="任务资料">
              <button
                v-for="file in task.inputs.slice(0, 3)"
                :key="file.fileId"
                type="button"
                class="work-input-chip"
                @click="filesOpen = true"
              >
                <FileTextOutlined /><span :title="file.name">{{ file.name }}</span
                ><small>v{{ file.version }}</small>
              </button>
              <button
                v-if="task.inputs.length > 3"
                type="button"
                class="work-input-chip"
                @click="filesOpen = true"
              >
                另 {{ task.inputs.length - 3 }} 份资料
              </button>
            </div>
          </template>
          <template #tools>
            <AButton
              type="text"
              aria-label="上传资料"
              :disabled="locked || !canRun"
              :loading="uploading"
              @click="fileInput?.click()"
              ><PlusOutlined /><span class="composer-tool-label">添加资料</span></AButton
            >
            <AButton
              v-if="canEdit"
              type="text"
              aria-label="资源与能力"
              :disabled="locked || mutating"
              @click="openResources"
              ><AppstoreOutlined /><span class="composer-tool-label">资源与能力</span></AButton
            >
          </template>
          <template #controls
            ><ASelect
              class="model-select"
              :value="modelId"
              :options="modelOptions"
              aria-label="选择工作模型"
              placeholder="选择工作模型"
              show-search
              option-filter-prop="label"
              :loading="optionsLoading"
              :disabled="locked || mutating || !canEdit || optionsLoading"
              @update:value="selectModel"
          /></template>
        </ConversationComposer>
        <AAlert
          v-else-if="isAgent && task.status === 'active'"
          class="work-task-notice"
          type="info"
          show-icon
          message="当前账号可以查看任务，但没有执行权限。"
        />
      </div>
    </div>
    <ADrawer
      v-if="isAgent"
      v-model:open="filesOpen"
      title="资料与成果"
      placement="right"
      :width="420"
      :body-style="{ padding: 0 }"
      ><WorkFilesPanel
        :task-id="task.taskId"
        :inputs="task.inputs"
        :artifacts="task.artifacts"
        :uploading="uploading"
        :can-upload="canRun && !locked && task.status === 'active'"
        @upload="emit('upload', $event)"
    /></ADrawer>
    <AModal
      v-model:open="editorOpen"
      title="编辑任务信息"
      :ok-button-props="{ disabled: locked }"
      @ok="saveEditor"
      ><AForm layout="vertical"
        ><AFormItem label="标题" name="title"
          ><AInput v-model:value="editingTitle" :maxlength="100" /></AFormItem
        ><AFormItem label="工作目标" name="goal"
          ><ATextarea v-model:value="editingGoal" :maxlength="32000" :rows="5" /></AFormItem></AForm
    ></AModal>
    <AModal
      v-model:open="resourcesOpen"
      title="资源与能力"
      :ok-button-props="{ disabled: locked || mutating || !canSaveResources }"
      @ok="saveResources"
      ><WorkResourcesForm
        v-model="resourceDraft"
        :options="options"
        :loading="optionsLoading"
        :error="optionsError"
        :disabled="locked"
        @retry="emit('retryOptions')"
    /></AModal>
    <AModal v-model:open="infoOpen" title="工作目标" :footer="null"
      ><p class="work-info-goal">{{ task.goal || '上传资料并发送目标后开始工作。' }}</p>
      <div v-if="!isAgent && legacyMembers.length" class="work-info-members">
        <strong>历史参与助手</strong>
        <p>{{ legacyMembers.join('、') }}</p>
      </div></AModal
    >
  </section>
</template>
<style scoped>
.work-task-page {
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-container);
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}
.work-task-header {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 38px;
  padding: 0 24px 4px 28px;
}
.work-task-header__copy {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.work-task-header h1 {
  min-width: 0;
  overflow: hidden;
  margin: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
  font-weight: 400;
  line-height: 1.6;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.work-task-status {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.work-task-header__actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 2px;
}
.work-task-header__actions :deep(.ant-btn) {
  height: 30px;
  padding: 0 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.work-file-count {
  display: inline-grid;
  min-width: 18px;
  height: 18px;
  margin-left: 4px;
  padding: 0 4px;
  place-items: center;
  border-radius: 6px;
  background: var(--gray-50);
  font-size: 11px;
}
.work-task-layout,
.work-task-conversation {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
}
.work-task-conversation {
  flex-direction: column;
  overflow: hidden;
}
.work-task-notice {
  width: min(920px, calc(100% - var(--chat-inset) * 2));
  flex-shrink: 0;
  align-self: center;
  margin: 10px 0 0;
  border-radius: 10px;
}
.work-composer__input {
  display: none;
}
.work-inputs {
  display: flex;
  gap: 8px;
  margin: 0 0 12px;
  overflow-x: auto;
  scrollbar-width: none;
}
.work-input-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  max-width: 240px;
  flex-shrink: 0;
  padding: 8px 10px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.work-input-chip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.work-input-chip small {
  color: var(--color-text-tertiary);
}
.work-artifacts-link {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-top: 14px;
  padding: 7px 11px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.work-artifacts-link:hover,
.work-input-chip:hover {
  color: var(--main-color);
  border-color: var(--main-200);
}
.work-info-goal {
  max-height: 60vh;
  overflow: auto;
  margin: 0;
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.work-info-members {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--gray-150);
  font-size: 13px;
  line-height: 1.8;
  color: var(--color-text-secondary);
}
.work-info-members strong {
  color: var(--color-text);
  font-weight: 500;
}
.work-info-members p {
  margin: 5px 0 0;
  overflow-wrap: anywhere;
}
@media (max-width: 640px) {
  .work-task-header {
    padding: 0 14px 4px;
    gap: 6px;
  }
  .work-task-header__actions :deep(.ant-btn) {
    padding: 0 6px;
  }
  .work-task-notice {
    width: calc(100% - 28px);
  }
}
</style>
