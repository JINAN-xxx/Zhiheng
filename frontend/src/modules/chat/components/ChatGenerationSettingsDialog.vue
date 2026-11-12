<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Modal as AModal,
  Textarea as ATextarea
} from 'ant-design-vue'
import type { ChatModel } from '../../models/public.ts'
import { generationSettingsError, generationSettingsFromDraft } from '../generationSettings.ts'
import type { ChatGenerationSettings } from '../generationSettings.ts'

const props = withDefaults(
  defineProps<{
    open: boolean
    settings: ChatGenerationSettings
    model: ChatModel | null
    disabled?: boolean
  }>(),
  { disabled: false }
)
const emit = defineEmits<{
  'update:open': [value: boolean]
  save: [settings: ChatGenerationSettings]
}>()
const draft = reactive({ system_prompt: '', temperature: '', max_output_tokens: '' })
const value = computed(() => generationSettingsFromDraft(draft))
const validationError = computed(() =>
  generationSettingsError(value.value, props.model?.max_output_tokens)
)
function reset(): void {
  draft.system_prompt = props.settings.system_prompt
  draft.temperature = props.settings.temperature == null ? '' : String(props.settings.temperature)
  draft.max_output_tokens =
    props.settings.max_output_tokens == null ? '' : String(props.settings.max_output_tokens)
}
function restoreDefaults(): void {
  if (props.disabled) return
  draft.system_prompt = ''
  draft.temperature = ''
  draft.max_output_tokens = ''
}
function close(): void {
  emit('update:open', false)
}
function save(): void {
  if (props.disabled || !props.model || validationError.value) return
  emit('save', { ...value.value })
  close()
}
watch(
  () => props.open,
  (open) => {
    if (open) reset()
    else restoreDefaults()
  },
  { immediate: true }
)
</script>

<template>
  <AModal :open="open" title="对话设置" :width="560" :footer="null" @cancel="close">
    <div class="generation-settings">
      <p class="generation-settings__description">设置仅对后续消息生效；重新生成沿用原消息参数。</p>
      <p v-if="model" class="generation-settings__model">
        {{ model.name }} · 默认不预设输出长度上限
      </p>
      <div class="generation-settings__field">
        <label for="chat-system-prompt">系统提示词</label>
        <ATextarea
          id="chat-system-prompt"
          v-model:value="draft.system_prompt"
          aria-label="系统提示词"
          :disabled="disabled"
          :rows="5"
          placeholder="例如：使用中文简洁回答，先给结论再解释原因"
        />
        <small>留空使用默认提示词，最多 32000 个字符。</small>
      </div>
      <div class="generation-settings__numbers">
        <div class="generation-settings__field">
          <label for="chat-temperature">温度</label>
          <input
            id="chat-temperature"
            v-model="draft.temperature"
            aria-label="温度"
            class="generation-settings__input"
            inputmode="decimal"
            :disabled="disabled"
            placeholder="模型默认"
          />
          <small>0–2；数值越高，回答越多样。</small>
        </div>
        <div class="generation-settings__field">
          <label for="chat-max-output-tokens">输出长度</label>
          <input
            id="chat-max-output-tokens"
            v-model="draft.max_output_tokens"
            aria-label="输出长度"
            class="generation-settings__input"
            inputmode="numeric"
            :disabled="disabled"
            placeholder="不限制"
          />
          <small>留空不向模型服务设置输出上限；填写后仅对后续消息生效。</small>
        </div>
      </div>
      <AAlert v-if="validationError" type="error" show-icon :message="validationError" />
      <AAlert v-else-if="!model" type="warning" show-icon message="请先选择可用模型" />
      <div class="generation-settings__footer">
        <AButton :disabled="disabled" @click="restoreDefaults">恢复默认</AButton>
        <div>
          <AButton aria-label="取消" @click="close">取消</AButton>
          <AButton
            type="primary"
            :disabled="disabled || !model || Boolean(validationError)"
            @click="save"
            >保存设置</AButton
          >
        </div>
      </div>
    </div>
  </AModal>
</template>

<style scoped>
.generation-settings {
  display: grid;
  gap: 20px;
  padding-top: 4px;
}
.generation-settings__description,
.generation-settings__model {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.generation-settings__model {
  margin-top: -12px;
}
.generation-settings__field {
  display: grid;
  align-content: start;
  gap: 8px;
}
.generation-settings__field label {
  color: var(--color-text);
  font-weight: 500;
}
.generation-settings__field small {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.generation-settings__input {
  width: 100%;
  min-width: 0;
  min-height: 32px;
  padding: 4px 11px;
  border: 1px solid var(--gray-300);
  border-radius: 6px;
  background: var(--bg-container);
  color: var(--color-text);
  font: inherit;
  font-size: 14px;
  line-height: 1.5;
  transition: border-color 0.2s;
}
.generation-settings__input:hover:not(:disabled) {
  border-color: var(--main-color);
}
.generation-settings__input:focus-visible {
  border-color: var(--main-color);
  outline: 2px solid var(--main-100);
}
.generation-settings__input:disabled {
  background: var(--gray-100);
  color: var(--color-text-secondary);
  cursor: not-allowed;
}
.generation-settings__input::placeholder {
  color: var(--color-text-tertiary);
}
.generation-settings__numbers {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}
.generation-settings__footer,
.generation-settings__footer > div {
  display: flex;
  align-items: center;
  gap: 8px;
}
.generation-settings__footer {
  justify-content: space-between;
  padding-top: 4px;
}
@media (max-width: 480px) {
  .generation-settings__numbers {
    grid-template-columns: 1fr;
  }
}
</style>
