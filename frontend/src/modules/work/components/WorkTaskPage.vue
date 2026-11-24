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
  AppstoreOutlined
} from '@ant-design/icons-vue'
import type {
  UpdateWorkBody,
  WorkArtifact,
  WorkInput,
  WorkOptions,
  WorkStatus,
  WorkTask
} from '../api/index.ts'
import type { WorkTurnInput } from '../composables/turns.ts'
import {
  pendingWorkInputs,
  validateWorkGoal,
  validateWorkMessage,
  validateWorkTitle,
  workExecutionLabel
} from '../presentation.ts'
import ConversationComposer from '../../../shared/components/ConversationComposer.vue'
import AttachmentButton from '../../../shared/components/AttachmentButton.vue'
import AttachmentCard from '../../../shared/components/AttachmentCard.vue'
import { formatFileSize } from '../../files/public.ts'
import WorkMessageTimeline from './WorkMessageTimeline.vue'
import WorkExecutionPanel from './WorkExecutionPanel.vue'
import WorkFilesPanel from './WorkFilesPanel.vue'
import WorkArtifactPreview from './WorkArtifactPreview.vue'
import { canUseCapability } from '../../identity/public.ts'
import { errorMessage } from '../../../shared/http.ts'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { SkillPicker } from '../../skills/public.ts'
import WorkSelectedSkills from './WorkSelectedSkills.vue'

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
  detachInput: (fileId: string) => Promise<boolean>
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
const composerSurface = ref<InstanceType<typeof ConversationComposer> | null>(null)
const editorOpen = ref(false),
  skillsOpen = ref(false),
  modelOpen = ref(false),
  filesOpen = ref(false),
  infoOpen = ref(false)
const editingTitle = ref(''),
  editingGoal = ref('')
const previewArtifact = ref<WorkArtifact | null>(null)
const confirmation = useConfirmation()
const removingInputId = ref('')
const inputError = ref('')
watch(
  () => props.task.taskId,
  () => {
    previewArtifact.value = null
    filesOpen.value = false
  },
  { flush: 'sync' }
)
const isAgent = computed(() => props.task.mode === 'agent')
const pendingInputs = computed(() => pendingWorkInputs(props.task))
const pendingInputIds = computed(() => new Set(pendingInputs.value.map((file) => file.fileId)))
const canRemoveInputs = computed(() => props.canEdit && props.task.status === 'active')
const showComposer = computed(
  () => isAgent.value && props.task.status === 'active' && (props.canRun || props.stoppable)
)
const canAddFiles = computed(() => props.canRun && !props.locked)
const canUseSkills = computed(() => props.canEdit && canUseCapability('skills', 'use'))
const skillSlugs = computed(() => props.task.configuration?.skillSlugs ?? [])
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
  emit('save', { model_id: value }, () => {
    modelOpen.value = false
  })
}
function selectSkills(slugs: string[]): void {
  if (props.locked || props.mutating || !props.canEdit || slugs.length > 5) return
  emit('save', { skill_slugs: [...slugs] }, () => {})
}
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
function picked(event: Event): void {
  const input = event.target as HTMLInputElement,
    files = [...(input.files ?? [])]
  input.value = ''
  if (files.length && props.canRun && !props.locked) emit('upload', files)
}
async function removeInput(file: WorkInput): Promise<void> {
  if (!canRemoveInputs.value || props.locked || removingInputId.value) return
  const name =
    props.task.inputs.filter((item) => item.name === file.name).length > 1
      ? `${file.name}（v${file.version}）`
      : file.name
  if (!(await confirmation.confirm(`从任务中移除“${name}”？`))) return
  if (!canRemoveInputs.value || props.locked || !pendingInputIds.value.has(file.fileId)) return
  removingInputId.value = file.fileId
  inputError.value = ''
  try {
    await props.detachInput(file.fileId)
  } catch (reason) {
    inputError.value = errorMessage(reason)
  } finally {
    removingInputId.value = ''
  }
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
          v-if="isAgent"
          type="text"
          class="work-task-header__files"
          aria-label="查看资料与成果"
          @click="filesOpen = true"
          ><FileTextOutlined /><span>资料与成果</span
          ><span v-if="artifactCount" class="work-file-count">{{ artifactCount }}</span></AButton
        >
        <ADropdown :trigger="['click']"
          ><AButton type="text" aria-label="更多任务操作"><MoreOutlined /></AButton
          ><template #overlay
            ><AMenu
              ><AMenuItem @click="infoOpen = true">查看完整目标</AMenuItem
              ><AMenuItem v-if="isAgent && canEdit" :disabled="locked" @click="openEditor"
                ><EditOutlined />编辑任务信息</AMenuItem
              ><AMenuItem
                v-if="isAgent && canEdit && !showComposer"
                :disabled="locked || mutating"
                @click="modelOpen = true"
                >选择模型</AMenuItem
              ><AMenuItem
                v-if="isAgent && canEdit && !showComposer && canUseCapability('skills', 'use')"
                :disabled="locked || mutating"
                @click="skillsOpen = true"
                >选择技能</AMenuItem
              ><AMenuItem
                v-if="isAgent && canEdit && task.status === 'active'"
                :disabled="locked"
                @click="emit('status', 'completed')"
                >结束任务</AMenuItem
              ><AMenuItem
                v-if="isAgent && canEdit && task.status === 'completed'"
                :disabled="locked"
                @click="emit('status', 'active')"
                >重新打开</AMenuItem
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
                      : '继续执行'
              }}</AButton
            ></template
          ></AAlert
        >
        <WorkMessageTimeline
          :task-id="task.taskId"
          :messages="task.messages"
          :inputs="task.inputs"
          :artifacts="task.artifacts"
          :legacy="!isAgent"
          :show-progress="showProgress"
          @preview="previewArtifact = $event"
          @open-input="filesOpen = true"
        >
          <template #progress
            ><WorkExecutionPanel
              :plan="task.plan"
              :steps="runSteps"
              :state="task.executionState"
              :busy="stoppable"
          /></template>
        </WorkMessageTimeline>
        <ConversationComposer
          v-if="showComposer"
          ref="composerSurface"
          v-model="message"
          class="work-composer"
          input-label="工作消息"
          :placeholder="task.executionState === 'waiting_input' ? '补充所需信息后继续' : undefined"
          :shortcuts="{ skills: canUseSkills && !locked && !mutating, attachments: canAddFiles }"
          :max-length="32000"
          :max-rows="7"
          :disabled="locked || !canRun"
          :running="stoppable"
          :selecting-skills="skillsOpen"
          :stop-disabled="stopDisabled"
          stop-label="停止"
          :submit-disabled="!canSend"
          :submit-label="task.executionState === 'paused' && !message.trim() ? '继续执行' : '发送'"
          @keydown="keydown"
          @select-skill="skillsOpen = true"
          @add-attachment="fileInput?.click()"
          @send="send"
          @stop="emit('cancel')"
        >
          <template #notice>
            <AAlert v-if="optionsError" type="error" show-icon :message="optionsError">
              <template #action
                ><AButton size="small" @click="emit('retryOptions')">重试</AButton></template
              >
            </AAlert>
          </template>
          <template #before-surface
            ><input
              ref="fileInput"
              class="work-composer__input"
              type="file"
              multiple
              aria-label="选择工作资料"
              @change="picked"
          /></template>
          <template #overlay>
            <SkillPicker
              v-if="canUseSkills"
              v-model:open="skillsOpen"
              :slugs="skillSlugs"
              :disabled="(locked && !mutating) || !canEdit"
              :busy="mutating"
              @update:slugs="selectSkills"
              @dismiss="composerSurface?.focus()"
            />
          </template>
          <template #prefix>
            <div v-if="pendingInputs.length" class="work-inputs" aria-label="待发送附件">
              <AttachmentCard
                v-for="file in pendingInputs"
                :key="file.fileId"
                :filename="file.name"
                :size-label="formatFileSize(file.size)"
                :mime="file.mime"
                :removable="canRemoveInputs"
                :disabled="locked"
                openable
                @open="filesOpen = true"
                @remove="removeInput(file)"
              />
            </div>
            <WorkSelectedSkills
              :slugs="skillSlugs"
              :options="options"
              :disabled="locked || mutating || !canEdit"
              @remove="selectSkills(skillSlugs.filter((slug) => slug !== $event))"
            />
          </template>
          <template #tools>
            <AttachmentButton
              aria-label="添加附件"
              :disabled="!canAddFiles"
              :loading="uploading"
              @click="fileInput?.click()"
            />
            <AButton
              v-if="canUseSkills"
              type="text"
              aria-label="选择技能"
              :disabled="locked || mutating"
              :aria-expanded="skillsOpen"
              @click="skillsOpen = !skillsOpen"
              ><AppstoreOutlined /><span class="composer-tool-label">技能</span></AButton
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
        :can-remove="canRemoveInputs"
        :locked="locked"
        :pending-input-ids="pendingInputIds"
        :removing-input-id="removingInputId"
        :remove-error="inputError"
        @upload="emit('upload', $event)"
        @remove="removeInput"
        @dismiss-remove-error="inputError = ''"
        @preview="previewArtifact = $event"
    /></ADrawer>
    <WorkArtifactPreview
      :task-id="task.taskId"
      :artifact="previewArtifact"
      :artifacts="task.artifacts"
      @close="previewArtifact = null"
      @select="previewArtifact = $event"
    />
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
    <AModal v-model:open="modelOpen" title="选择模型" :footer="null"
      ><ASelect
        style="width: 100%"
        :value="modelId"
        :options="modelOptions"
        aria-label="选择工作模型"
        show-search
        option-filter-prop="label"
        :loading="optionsLoading"
        :disabled="locked || mutating || !canEdit || optionsLoading"
        @update:value="selectModel"
    /></AModal>
    <AModal v-if="!showComposer" v-model:open="skillsOpen" title="技能" :footer="null">
      <WorkSelectedSkills
        :slugs="skillSlugs"
        :options="options"
        :disabled="locked || mutating || !canEdit"
        @remove="selectSkills(skillSlugs.filter((slug) => slug !== $event))"
      />
      <SkillPicker
        v-model:open="skillsOpen"
        :slugs="skillSlugs"
        :disabled="(locked && !mutating) || !canEdit"
        :busy="mutating"
        @update:slugs="selectSkills"
      />
    </AModal>
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
  min-width: 0;
  padding: 2px;
  margin: 0 0 12px;
  overflow-x: auto;
  scrollbar-width: none;
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
