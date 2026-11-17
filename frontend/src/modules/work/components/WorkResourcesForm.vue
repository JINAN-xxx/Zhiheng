<script setup lang="ts">
import { computed } from 'vue'
import {
  Alert as AAlert,
  Select as ASelect,
  Spin as ASpin,
  Switch as ASwitch
} from 'ant-design-vue'
import { canUseCapability } from '../../identity/public.ts'
import type { WorkOptions } from '../api/index.ts'
import type { WorkDraft } from '../presentation.ts'

const props = defineProps<{
  modelValue: WorkDraft
  options: WorkOptions | null
  loading: boolean
  error: string
  disabled: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [draft: WorkDraft]; retry: [] }>()
const modelOptions = computed(() => {
  const items = (props.options?.models ?? []).map((model) => ({
    value: model.id,
    label: model.name
  }))
  return props.modelValue.modelId && !items.some((item) => item.value === props.modelValue.modelId)
    ? [{ value: props.modelValue.modelId, label: '当前模型不可用', disabled: true }, ...items]
    : items
})
const skillOptions = computed(() =>
  (props.options?.skills ?? []).map((skill) => ({
    value: skill.slug,
    label: skill.name,
    title: skill.description,
    disabled:
      props.modelValue.skillSlugs.length >= 5 && !props.modelValue.skillSlugs.includes(skill.slug)
  }))
)
const canExecuteCode = computed(
  () => !!props.options?.canExecuteCode && canUseCapability('work', 'execute_code')
)
const executionAvailable = computed(() => canExecuteCode.value && !!props.options?.sandboxAvailable)
function update(changes: Partial<WorkDraft>): void {
  emit('update:modelValue', {
    ...props.modelValue,
    skillSlugs: [...props.modelValue.skillSlugs],
    ...changes
  })
}
function selectModel(value: unknown): void {
  update({ modelId: typeof value === 'string' ? value : null })
}
function selectSkills(value: unknown): void {
  if (Array.isArray(value) && value.every((item) => typeof item === 'string') && value.length <= 5)
    update({ skillSlugs: value })
}
</script>
<template>
  <div class="work-resources">
    <AAlert v-if="error" type="error" show-icon :message="error"
      ><template #action
        ><button type="button" class="work-resources__retry" @click="emit('retry')">
          重试
        </button></template
      ></AAlert
    >
    <ASpin :spinning="loading">
      <div class="work-resources__fields">
        <label class="work-resources__field"
          ><span>工作模型</span
          ><ASelect
            :value="modelValue.modelId ?? undefined"
            aria-label="工作模型"
            placeholder="选择工作模型"
            :options="modelOptions"
            :disabled="disabled || loading"
            @update:value="selectModel"
        /></label>
        <label class="work-resources__field"
          ><span>技能</span
          ><ASelect
            :value="modelValue.skillSlugs"
            aria-label="工作技能"
            mode="multiple"
            max-tag-count="responsive"
            placeholder="按需选择，最多 5 项"
            :options="skillOptions"
            :disabled="disabled || loading"
            @update:value="selectSkills"
        /></label>
      </div>
      <div class="work-resources__execution">
        <div>
          <strong>文件处理与代码执行</strong>
          <p>
            {{
              !canExecuteCode
                ? '当前账号未开放此能力'
                : !options?.sandboxAvailable
                  ? '文件执行环境暂未就绪'
                  : '允许分析数据、处理文件并制作交付成果'
            }}
          </p>
        </div>
        <ASwitch
          :checked="modelValue.allowCodeExecution"
          aria-label="文件处理与代码执行"
          :disabled="disabled || loading || (!executionAvailable && !modelValue.allowCodeExecution)"
          @update:checked="update({ allowCodeExecution: $event === true })"
        />
      </div>
    </ASpin>
  </div>
</template>
<style scoped>
.work-resources {
  display: grid;
  gap: 12px;
}
.work-resources__fields {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
}
.work-resources__field {
  display: grid;
  gap: 7px;
  min-width: 0;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.work-resources__field :deep(.ant-select) {
  width: 100%;
}
.work-resources__execution {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-top: 16px;
  margin-top: 16px;
  border-top: 1px solid var(--gray-150);
}
.work-resources__execution strong {
  color: var(--color-text);
  font-weight: 500;
  font-size: 13px;
}
.work-resources__execution p {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.7;
  margin: 4px 0 0;
}
.work-resources__retry {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-color);
  font: inherit;
  cursor: pointer;
}
@media (max-width: 560px) {
  .work-resources__fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
