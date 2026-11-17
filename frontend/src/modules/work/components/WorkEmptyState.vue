<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Alert as AAlert, Button as AButton, Textarea as ATextarea } from 'ant-design-vue'
import { PaperClipOutlined, PlayCircleOutlined, ThunderboltOutlined } from '@ant-design/icons-vue'
import type { WorkDraft } from '../presentation.ts'
import { validateWorkGoal } from '../presentation.ts'
import type { WorkOptions } from '../api/index.ts'
import WorkResourcesForm from './WorkResourcesForm.vue'

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
const error = ref(''),
  resourcesOpen = ref(false)
const blocked = computed(() => props.creating || props.uncertain)
const hasModel = computed(
  () =>
    !!draft.value.modelId && props.options?.models.some((model) => model.id === draft.value.modelId)
)
const canStart = computed(
  () => !props.creating && !!hasModel.value && validateWorkGoal(draft.value.goal).valid
)
const modelName = computed(
  () =>
    props.options?.models.find((model) => model.id === draft.value.modelId)?.name ?? '选择工作模型'
)
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
const suggestions = [
  '核对多份表格，生成汇总与异常清单',
  '整理上传资料，制作一份 Word 报告',
  '分析数据并制作图表，说明计算口径'
]
function start(): void {
  if (canStart.value) emit('start', { ...draft.value, skillSlugs: [...draft.value.skillSlugs] })
}
function chooseFiles(): void {
  if (!blocked.value && hasModel.value) fileInput.value?.click()
}
function picked(event: Event): void {
  const target = event.target as HTMLInputElement,
    files = [...(target.files ?? [])]
  target.value = ''
  if (files.length && !blocked.value) {
    error.value = ''
    emit('upload', { ...draft.value, skillSlugs: [...draft.value.skillSlugs] }, files)
  }
}
</script>
<template>
  <section class="work-empty-state">
    <div class="work-empty-state__heading">
      <span class="work-empty-state__symbol" aria-hidden="true"><ThunderboltOutlined /></span>
      <h1>今天要完成什么工作？</h1>
      <p>上传资料，描述目标，一起把工作做成成果。</p>
    </div>
    <div v-if="canCreate" class="work-empty-state__form">
      <ATextarea
        v-model:value="draft.goal"
        aria-label="工作目标"
        placeholder="例如：核对这几份表格，生成 Excel 汇总和 Word 分析报告"
        :maxlength="32000"
        :auto-size="{ minRows: 4, maxRows: 9 }"
        :disabled="blocked"
      />
      <AAlert
        v-if="uncertain"
        type="warning"
        show-icon
        message="创建结果待确认，再次操作将确认原请求"
      />
      <AAlert
        v-if="error || optionsError"
        type="error"
        show-icon
        :message="error || optionsError"
      />
      <div class="work-empty-state__footer">
        <div class="work-empty-state__tools">
          <AButton
            type="text"
            aria-label="上传资料"
            :disabled="blocked || !hasModel"
            @click="chooseFiles"
            ><PaperClipOutlined />上传资料</AButton
          ><AButton
            type="text"
            aria-label="资源与能力"
            :disabled="blocked"
            :aria-expanded="resourcesOpen"
            @click="resourcesOpen = !resourcesOpen"
            >{{ modelName
            }}<span v-if="draft.skillSlugs.length" class="work-empty-state__skills">
              · {{ draft.skillSlugs.length }} 项技能</span
            ></AButton
          >
        </div>
        <AButton
          type="primary"
          aria-label="开始工作"
          :loading="creating"
          :disabled="!canStart"
          @click="start"
          ><PlayCircleOutlined />{{ uncertain ? '确认创建' : '开始工作' }}</AButton
        >
      </div>
      <WorkResourcesForm
        v-if="resourcesOpen || !hasModel"
        v-model="draft"
        :options="options"
        :loading="optionsLoading"
        :error="optionsError"
        :disabled="blocked"
        @retry="emit('retryOptions')"
      />
      <input
        ref="fileInput"
        class="work-empty-state__file-input"
        type="file"
        multiple
        aria-label="选择工作资料"
        @change="picked"
      />
    </div>
    <AAlert
      v-else
      type="info"
      show-icon
      message="当前账号可查看已有任务"
      description="请从工作记录中打开任务。"
    />
    <div v-if="canCreate" class="work-empty-state__suggestions">
      <button
        v-for="suggestion in suggestions"
        :key="suggestion"
        type="button"
        :disabled="blocked"
        @click="draft.goal = suggestion"
      >
        {{ suggestion }}
      </button>
    </div>
  </section>
</template>
<style scoped>
.work-empty-state {
  width: min(800px, calc(100% - 48px));
  min-height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 24px;
  margin: 0 auto;
  padding: 36px 0;
  font-family: var(--workspace-font-family);
}
.work-empty-state__heading {
  display: grid;
  justify-items: center;
  gap: 14px;
  text-align: center;
}
.work-empty-state__symbol {
  display: grid;
  width: 48px;
  height: 48px;
  place-items: center;
  border: 1px solid var(--main-100);
  border-radius: 12px;
  background: var(--main-50);
  color: var(--main-color);
  font-size: 24px;
}
.work-empty-state h1 {
  margin: 0;
  color: var(--color-text);
  font-size: 30px;
  font-weight: 500;
  line-height: 1.4;
  letter-spacing: normal;
}
.work-empty-state__heading p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.7;
}
.work-empty-state__form {
  display: grid;
  gap: 16px;
  padding: 20px;
  border: 1px solid var(--gray-200);
  border-radius: 16px;
  background: var(--bg-container);
  box-shadow: none;
}
.work-empty-state__form:focus-within {
  border-color: var(--main-400);
  box-shadow: 0 0 0 3px var(--main-50);
}
.work-empty-state__form :deep(.ant-input) {
  padding: 0;
  border: 0;
  background: transparent;
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
  box-shadow: none;
}
.work-empty-state__footer,
.work-empty-state__tools {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.work-empty-state__footer {
  justify-content: space-between;
}
.work-empty-state__tools {
  flex-wrap: wrap;
}
.work-empty-state__tools :deep(.ant-btn) {
  padding-inline: 8px;
}
.work-empty-state__footer > :deep(.ant-btn) {
  flex-shrink: 0;
}
.work-empty-state__skills {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.work-empty-state__suggestions {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px;
}
.work-empty-state__suggestions button {
  padding: 8px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  text-align: left;
}
.work-empty-state__suggestions button:hover {
  border-color: var(--main-200);
  color: var(--main-color);
}
.work-empty-state__suggestions button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.work-empty-state__file-input {
  display: none;
}
@media (max-width: 560px) {
  .work-empty-state {
    width: calc(100% - 28px);
    padding: 24px 0;
  }
  .work-empty-state h1 {
    font-size: 26px;
  }
  .work-empty-state__form {
    padding: 16px;
  }
  .work-empty-state__footer {
    align-items: flex-end;
  }
  .work-empty-state__tools {
    gap: 0;
  }
}
</style>
