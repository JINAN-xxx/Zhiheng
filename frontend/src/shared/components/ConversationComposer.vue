<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button as AButton, Textarea as ATextarea } from 'ant-design-vue'
import { ArrowUpOutlined, BorderOutlined } from '@ant-design/icons-vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    inputLabel?: string
    placeholder?: string | undefined
    shortcuts?: { skills: boolean; attachments: boolean } | null
    maxLength?: number
    maxRows?: number
    disabled?: boolean
    submitDisabled?: boolean
    sending?: boolean
    running?: boolean
    stopping?: boolean
    stopDisabled?: boolean
    submitLabel?: string
    stopLabel?: string
    selectingSkills?: boolean
  }>(),
  {
    inputLabel: '消息内容',
    placeholder: undefined,
    shortcuts: null,
    maxLength: 16000,
    maxRows: 6,
    disabled: false,
    submitDisabled: false,
    sending: false,
    running: false,
    stopping: false,
    stopDisabled: false,
    submitLabel: '发送消息',
    stopLabel: '停止生成',
    selectingSkills: false
  }
)
const emit = defineEmits<{
  'update:modelValue': [value: string]
  keydown: [event: KeyboardEvent]
  'select-skill': []
  'add-attachment': []
  dragover: [event: DragEvent]
  drop: [event: DragEvent]
  paste: [event: ClipboardEvent]
  send: []
  stop: []
}>()
const surface = ref<HTMLElement | null>(null)
const inputPlaceholder = computed(() => {
  if (!props.shortcuts) return props.placeholder ?? '输入消息，或选择技能开始'
  const base = props.placeholder ?? '发送消息或开始任务…'
  const skills = !props.disabled && props.shortcuts.skills ? ' / 使用技能' : ''
  const attachments = !props.disabled && props.shortcuts.attachments ? ' @ 添加资料' : ''
  return base + skills + attachments
})

function onKeydown(event: KeyboardEvent): void {
  const { key, target } = event
  const shortcutAvailable =
    key === '/' ? props.shortcuts?.skills : key === '@' && props.shortcuts?.attachments
  if (
    shortcutAvailable &&
    !props.disabled &&
    !event.defaultPrevented &&
    !event.isComposing &&
    event.keyCode !== 229 &&
    !event.repeat &&
    !event.metaKey &&
    (!(event.ctrlKey || event.altKey) || event.getModifierState('AltGraph')) &&
    target instanceof HTMLTextAreaElement
  ) {
    const { value, selectionStart, selectionEnd } = target
    if (
      selectionStart === selectionEnd &&
      (selectionStart === 0 || /\s/.test(value[selectionStart - 1] ?? '')) &&
      (selectionEnd === value.length || /\s/.test(value[selectionEnd] ?? ''))
    ) {
      event.preventDefault()
      if (key === '/') emit('select-skill')
      else emit('add-attachment')
      return
    }
  }
  emit('keydown', event)
}

defineExpose({
  focus: () => surface.value?.querySelector('textarea')?.focus(),
  contains: (node: Node) => surface.value?.contains(node) ?? false
})
</script>

<template>
  <footer
    class="composer"
    @dragover="emit('dragover', $event)"
    @drop="emit('drop', $event)"
    @paste="emit('paste', $event)"
  >
    <slot name="notice" />
    <slot name="context" />
    <slot name="before-surface" />
    <div ref="surface" class="composer-surface" :class="{ 'is-selecting-skills': selectingSkills }">
      <slot name="overlay" />
      <div class="composer-input-area">
        <slot name="prefix" />
        <ATextarea
          :value="modelValue"
          :aria-label="inputLabel"
          :placeholder="inputPlaceholder"
          :maxlength="maxLength"
          :auto-size="{ minRows: 2, maxRows }"
          :disabled="disabled"
          @update:value="emit('update:modelValue', $event)"
          @keydown="onKeydown"
        />
      </div>
      <div class="composer-actions">
        <div class="composer-tools"><slot name="tools" /></div>
        <div class="composer-controls">
          <slot name="controls" />
          <AButton
            v-if="running"
            class="composer-submit"
            :loading="stopping"
            :disabled="stopDisabled"
            :aria-label="stopLabel"
            :title="stopLabel"
            @click="emit('stop')"
            ><BorderOutlined aria-hidden="true"
          /></AButton>
          <AButton
            v-else
            class="composer-submit"
            type="primary"
            :loading="sending"
            :disabled="submitDisabled"
            :aria-label="submitLabel"
            :title="`${submitLabel}（Enter），Shift + Enter 换行`"
            @click="emit('send')"
            ><ArrowUpOutlined aria-hidden="true"
          /></AButton>
        </div>
      </div>
    </div>
  </footer>
</template>

<style scoped>
.composer {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  min-height: 0;
  max-height: calc(100% - 180px);
  width: min(calc(var(--chat-column-width, 920px) + var(--chat-inset, 45px) * 2), 100%);
  align-self: center;
  padding: 10px var(--chat-gutter, 24px) 12px;
  background: transparent;
}
.composer-surface {
  display: flex;
  min-height: 0;
  flex: 0 1 auto;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--gray-200);
  border-radius: 24px;
  background: var(--bg-container);
  box-shadow: none;
  transition:
    border-color 0.18s,
    box-shadow 0.18s;
}
.composer-surface:focus-within,
.composer-surface.is-selecting-skills {
  border-color: var(--main-400);
  box-shadow:
    0 0 0 1px var(--main-50),
    0 4px 20px rgb(0 0 0 / 3%);
}
.composer-input-area {
  min-height: 0;
  flex: 0 1 auto;
  overflow-y: auto;
  padding: 16px var(--chat-composer-padding, 20px) 0;
}
.composer-surface :deep(.ant-input) {
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}
.composer :deep(textarea) {
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
}
.composer-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  padding: 10px var(--chat-composer-padding, 20px) 12px;
}
.composer-tools,
.composer-controls {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}
.composer-tools {
  flex: 1 1 auto;
}
.composer-controls {
  flex: 0 1 260px;
  justify-content: flex-end;
}
.composer-submit {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  padding: 0;
  border-radius: 50%;
  font-size: 19px;
}
.composer-submit:not(:disabled) {
  border-color: #262626;
  background: #262626;
  color: #fff;
}
.composer-submit:not(:disabled):hover {
  border-color: #434343;
  background: #434343;
}
.composer-actions :deep(.ant-btn-text) {
  border-radius: 9px;
  color: var(--color-text-secondary);
}
.composer-controls :deep(.model-select) {
  flex: 1;
  min-width: 0;
  width: min(220px, 100%);
}
.composer-controls :deep(.ant-select-selector) {
  border-color: transparent;
  border-radius: 10px;
  background: transparent;
  box-shadow: none;
}
@media (max-width: 760px) {
  .composer {
    padding-right: 14px;
    padding-left: 14px;
  }
}
@media (max-width: 600px) {
  .composer-tools {
    flex: 0 0 auto;
    gap: 2px;
  }
  .composer-controls {
    flex: 1;
  }
  .composer-tools :deep(.composer-tool-label) {
    display: none;
  }
  .composer-tools :deep(.ant-btn-text) {
    width: 32px;
    padding: 0;
  }
  .composer-actions {
    gap: 5px;
  }
}
</style>
