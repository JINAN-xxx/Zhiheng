<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Alert as AAlert, Button as AButton, Select as ASelect } from 'ant-design-vue'
import {
  AppstoreOutlined,
  FileTextOutlined,
  BarChartOutlined,
  EditOutlined
} from '@ant-design/icons-vue'
import ConversationComposer from '../../../shared/components/ConversationComposer.vue'
import AttachmentButton from '../../../shared/components/AttachmentButton.vue'
import { ChatEmptyState } from '../../chat/public.ts'
import type { WorkDraft } from '../presentation.ts'
import { validateWorkGoal } from '../presentation.ts'
import type { WorkOptions } from '../api/index.ts'
import { canUseCapability } from '../../identity/public.ts'
import { SkillPicker } from '../../skills/public.ts'
import WorkSelectedSkills from './WorkSelectedSkills.vue'

const props = defineProps<{
  canCreate: boolean
  options: WorkOptions | null
  optionsLoading: boolean
  optionsError: string
  creating: boolean
  uncertain: boolean
  initialDraft: WorkDraft
}>()
const emit = defineEmits<{
  retryOptions: []
  start: [draft: WorkDraft]
  upload: [draft: WorkDraft, files: File[]]
}>()
const draft = ref<WorkDraft>({
  ...props.initialDraft,
  skillSlugs: [...props.initialDraft.skillSlugs]
})
const fileInput = ref<HTMLInputElement | null>(null)
const composerSurface = ref<InstanceType<typeof ConversationComposer> | null>(null)
const skillsOpen = ref(false)
const canUseSkills = computed(() => canUseCapability('skills', 'use'))
const blocked = computed(() => props.creating || props.uncertain)
const hasModel = computed(
  () =>
    !!draft.value.modelId && props.options?.models.some((model) => model.id === draft.value.modelId)
)
const canAddFiles = computed(() => !blocked.value && !!hasModel.value)
const canStart = computed(
  () => !props.creating && !!hasModel.value && validateWorkGoal(draft.value.goal).valid
)
const modelOptions = computed(() =>
  (props.options?.models ?? []).map((model) => ({ value: model.id, label: model.name }))
)
const suggestions = [
  { label: '汇总表格', prompt: '核对多份表格，生成汇总与异常清单', icon: BarChartOutlined },
  { label: '制作报告', prompt: '整理上传资料，制作一份 Word 报告', icon: FileTextOutlined },
  { label: '分析数据', prompt: '分析数据并制作图表，说明计算口径', icon: EditOutlined }
]
watch(
  () => props.options,
  (options) => {
    if (!draft.value.modelId && options?.models.length) draft.value.modelId = options.models[0]!.id
  },
  { immediate: true }
)
watch(
  () => props.initialDraft,
  (saved) => {
    if (props.uncertain) draft.value = { ...saved, skillSlugs: [...saved.skillSlugs] }
  }
)
function start(): void {
  if (canStart.value) emit('start', { ...draft.value, skillSlugs: [...draft.value.skillSlugs] })
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
    start()
  }
}
function chooseFiles(): void {
  if (canAddFiles.value) fileInput.value?.click()
}
function picked(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = [...(input.files ?? [])]
  input.value = ''
  if (files.length && !blocked.value)
    emit('upload', { ...draft.value, skillSlugs: [...draft.value.skillSlugs] }, files)
}
function selectModel(value: unknown): void {
  if (typeof value === 'string') draft.value.modelId = value
}
</script>

<template>
  <section class="work-empty-state">
    <div class="work-empty-state__welcome">
      <ChatEmptyState
        title="今天要完成什么工作？"
        description="上传资料或描述目标，一起把工作做成成果。"
      >
        <template #starters>
          <AButton
            v-for="suggestion in suggestions"
            :key="suggestion.label"
            :disabled="!canCreate || blocked"
            @click="draft.goal = suggestion.prompt"
            ><component :is="suggestion.icon" />{{ suggestion.label }}</AButton
          >
        </template>
      </ChatEmptyState>
    </div>
    <ConversationComposer
      v-if="canCreate"
      ref="composerSurface"
      v-model="draft.goal"
      input-label="工作目标"
      :shortcuts="{ skills: canUseSkills && !blocked, attachments: canAddFiles }"
      :max-length="32000"
      :max-rows="9"
      :disabled="blocked"
      :sending="creating"
      :selecting-skills="skillsOpen"
      :submit-disabled="!canStart"
      submit-label="开始工作"
      @keydown="keydown"
      @select-skill="skillsOpen = true"
      @add-attachment="chooseFiles"
      @send="start"
    >
      <template #notice>
        <AAlert
          v-if="uncertain"
          class="work-create-notice"
          type="warning"
          show-icon
          message="创建结果待确认，再次操作将确认原请求"
        />
        <AAlert
          v-if="optionsError"
          class="work-create-notice"
          type="error"
          show-icon
          :message="optionsError"
          ><template #action
            ><AButton size="small" @click="emit('retryOptions')">重试</AButton></template
          ></AAlert
        >
      </template>
      <template #before-surface
        ><input
          ref="fileInput"
          class="work-file-input"
          type="file"
          multiple
          aria-label="选择工作资料"
          @change="picked"
      /></template>
      <template #overlay>
        <SkillPicker
          v-if="canUseSkills"
          v-model:open="skillsOpen"
          v-model:slugs="draft.skillSlugs"
          :disabled="blocked"
          @dismiss="composerSurface?.focus()"
        />
      </template>
      <template #prefix>
        <WorkSelectedSkills
          :slugs="draft.skillSlugs"
          :options="options"
          :disabled="blocked"
          @remove="draft.skillSlugs = draft.skillSlugs.filter((slug) => slug !== $event)"
        />
      </template>
      <template #tools>
        <AttachmentButton aria-label="添加附件" :disabled="!canAddFiles" @click="chooseFiles" />
        <AButton
          v-if="canUseSkills"
          type="text"
          aria-label="选择技能"
          :disabled="blocked"
          :aria-expanded="skillsOpen"
          @click="skillsOpen = !skillsOpen"
          ><AppstoreOutlined /><span class="composer-tool-label">技能</span></AButton
        >
      </template>
      <template #controls
        ><ASelect
          class="model-select"
          :value="draft.modelId ?? undefined"
          :options="modelOptions"
          show-search
          option-filter-prop="label"
          aria-label="选择工作模型"
          placeholder="选择工作模型"
          :loading="optionsLoading"
          :disabled="blocked || optionsLoading"
          @update:value="selectModel"
      /></template>
    </ConversationComposer>
    <AAlert
      v-else
      class="work-readonly-notice"
      type="info"
      show-icon
      message="当前账号可查看已有任务"
      description="请从工作记录中打开任务。"
    />
  </section>
</template>

<style scoped>
.work-empty-state {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
}
.work-empty-state__welcome {
  display: flex;
  flex: 1;
  min-height: 180px;
  align-items: center;
  justify-content: center;
  padding: 24px;
  overflow: auto;
}
.work-empty-state__welcome :deep(.chat-starters) {
  max-width: 900px;
}
.work-file-input {
  display: none;
}
.work-create-notice {
  margin: 0 0 10px;
  border-radius: 12px;
}
.work-readonly-notice {
  margin: 12px 24px;
}
@media (max-width: 600px) {
  .work-empty-state__welcome {
    padding: 18px 14px;
  }
}
</style>
