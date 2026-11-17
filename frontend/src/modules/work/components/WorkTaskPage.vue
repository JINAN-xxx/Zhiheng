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
  Tag as ATag,
  Textarea as ATextarea
} from 'ant-design-vue'
import {
  EditOutlined,
  FileTextOutlined,
  MoreOutlined,
  PaperClipOutlined,
  PauseCircleOutlined,
  SendOutlined,
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
const stateColor = computed(() =>
  props.task.executionState === 'failed'
    ? 'error'
    : props.stoppable
      ? 'processing'
      : props.task.executionState === 'completed'
        ? 'success'
        : 'default'
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
        <ATag :color="stateColor">{{ stateLabel }}</ATag>
      </div>
      <div class="work-task-header__actions">
        <AButton
          v-if="isAgent && canEdit"
          type="text"
          aria-label="资源与能力"
          :disabled="locked"
          @click="openResources"
          ><SettingOutlined />资源与能力</AButton
        ><AButton
          v-if="isAgent"
          type="text"
          class="work-task-header__files"
          aria-label="查看资料与成果"
          @click="filesOpen = true"
          ><FileTextOutlined />资料与成果</AButton
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
        <WorkExecutionPanel
          v-if="isAgent"
          :plan="task.plan"
          :steps="runSteps"
          :state="task.executionState"
          :busy="stoppable"
        />
        <WorkMessageTimeline :messages="task.messages" />
        <footer
          v-if="isAgent && task.status === 'active' && (canRun || stoppable)"
          class="work-composer"
        >
          <ATextarea
            v-model:value="message"
            aria-label="工作消息"
            :placeholder="
              task.executionState === 'waiting_input'
                ? '补充所需信息，或上传资料后继续'
                : '描述目标，或继续修改已有成果'
            "
            :maxlength="32000"
            :auto-size="{ minRows: 2, maxRows: 7 }"
            :disabled="locked || !canRun"
            @keydown="keydown"
          />
          <div class="work-composer__tools">
            <AButton
              type="text"
              aria-label="上传资料"
              :disabled="locked || !canRun"
              :loading="uploading"
              @click="fileInput?.click()"
              ><PaperClipOutlined />上传资料</AButton
            ><span v-if="task.inputs.length">{{ task.inputs.length }} 份资料</span>
          </div>
          <div class="work-composer__submit">
            <AButton
              v-if="stoppable"
              aria-label="停止"
              :disabled="stopDisabled"
              danger
              @click="emit('cancel')"
              ><PauseCircleOutlined />停止</AButton
            ><AButton
              v-else
              :aria-label="
                task.executionState === 'paused' && !message.trim() ? '继续执行' : '发送'
              "
              type="primary"
              :disabled="!canSend"
              @click="send"
              ><SendOutlined />{{
                task.executionState === 'paused' && !message.trim() ? '继续执行' : '发送'
              }}</AButton
            >
          </div>
          <input
            ref="fileInput"
            class="work-composer__input"
            type="file"
            multiple
            aria-label="选择工作资料"
            @change="picked"
          />
        </footer>
        <AAlert
          v-else-if="isAgent && task.status === 'active'"
          class="work-task-notice"
          type="info"
          show-icon
          message="当前账号可以查看任务，但没有执行权限。"
        />
      </div>
      <aside v-if="isAgent" class="work-task-files">
        <WorkFilesPanel
          :task-id="task.taskId"
          :inputs="task.inputs"
          :artifacts="task.artifacts"
          :uploading="uploading"
          :can-upload="canRun && !locked && task.status === 'active'"
          @upload="emit('upload', $event)"
        />
      </aside>
    </div>
    <ADrawer
      v-model:open="filesOpen"
      title="资料与成果"
      placement="right"
      :width="360"
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
      :ok-button-props="{ disabled: locked || !canSaveResources }"
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
  min-height: 100%;
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
  gap: 16px;
  padding: 14px 24px;
  border-bottom: 1px solid var(--gray-150);
}
.work-task-header__copy {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}
.work-task-header h1 {
  min-width: 0;
  overflow: hidden;
  margin: 0;
  color: var(--color-text);
  font-size: 17px;
  font-weight: 500;
  line-height: 1.6;
  text-overflow: ellipsis;
  white-space: nowrap;
  letter-spacing: 0;
}
.work-task-header__copy :deep(.ant-tag) {
  margin: 0;
  flex-shrink: 0;
  border-radius: 6px;
  font-size: 11px;
}
.work-task-header__actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 3px;
}
.work-task-header__actions :deep(.ant-btn) {
  font-size: 13px;
}
.work-task-header__files {
  display: none;
}
.work-task-layout {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
}
.work-task-conversation {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.work-task-files {
  width: 300px;
  flex-shrink: 0;
  min-height: 0;
  border-left: 1px solid var(--gray-150);
}
.work-task-notice {
  width: min(840px, calc(100% - 40px));
  flex-shrink: 0;
  align-self: center;
  margin: 10px 0 0;
  border-radius: 10px;
}
.work-composer {
  width: min(840px, calc(100% - 40px));
  flex-shrink: 0;
  align-self: center;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  margin: 10px 0 20px;
  padding: 14px 16px;
  border: 1px solid var(--gray-200);
  border-radius: 16px;
  background: var(--bg-container);
  box-shadow: 0 4px 18px rgb(0 0 0 / 3%);
}
.work-composer:focus-within {
  border-color: var(--main-400);
  box-shadow: 0 0 0 3px var(--main-50);
}
.work-composer > :deep(.ant-input) {
  grid-column: 1 / -1;
  padding: 0;
  border: 0;
  background: transparent;
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
  box-shadow: none;
}
.work-composer__tools,
.work-composer__submit {
  display: flex;
  align-items: center;
  gap: 8px;
}
.work-composer__tools {
  min-width: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.work-composer__tools :deep(.ant-btn) {
  padding-inline: 5px;
  color: var(--color-text-secondary);
}
.work-composer__submit {
  justify-content: flex-end;
}
.work-composer__submit :deep(.ant-btn) {
  border-radius: 9px;
  min-width: 78px;
}
.work-composer__input {
  display: none;
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
@media (max-width: 1100px) {
  .work-task-files {
    width: 260px;
  }
}
@media (max-width: 940px) {
  .work-task-files {
    display: none;
  }
  .work-task-header__files {
    display: inline-flex;
  }
}
@media (max-width: 640px) {
  .work-task-header {
    padding: 12px 14px;
    gap: 6px;
  }
  .work-task-header h1 {
    font-size: 15px;
  }
  .work-task-header__copy {
    gap: 6px;
  }
  .work-task-header__actions :deep(.ant-btn) {
    padding-inline: 7px;
    font-size: 12px;
  }
  .work-composer {
    width: calc(100% - 24px);
    margin-bottom: 12px;
    padding: 12px;
  }
  .work-task-notice {
    width: calc(100% - 24px);
  }
}
</style>
