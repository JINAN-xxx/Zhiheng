<!-- Adapted from original workspace, MIT licensed. -->
<template>
  <div
    v-if="canChat && canWork"
    class="workspace-mode-switch"
    role="group"
    aria-label="选择聊天或工作"
  >
    <button
      type="button"
      aria-label="聊天"
      :aria-pressed="modelValue === 'chat'"
      @click="emit('update:modelValue', 'chat')"
    >
      <MessageOutlined aria-hidden="true" />聊天
    </button>
    <button
      type="button"
      aria-label="工作"
      :aria-pressed="modelValue === 'work'"
      @click="emit('update:modelValue', 'work')"
    >
      <AppstoreOutlined aria-hidden="true" />工作
    </button>
  </div>
</template>

<script setup lang="ts">
import { AppstoreOutlined, MessageOutlined } from '@ant-design/icons-vue'
withDefaults(defineProps<{ modelValue: 'chat' | 'work'; canChat?: boolean; canWork?: boolean }>(), {
  canChat: true,
  canWork: true
})
const emit = defineEmits<{ 'update:modelValue': [mode: 'chat' | 'work'] }>()
</script>

<style scoped>
.workspace-mode-switch {
  display: flex;
  width: min(210px, 100%);
  gap: 4px;
  padding: 4px;
  background: var(--gray-100);
  border: 1px solid var(--gray-150);
  border-radius: 9px;
}
.workspace-mode-switch button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  height: 34px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
  transition:
    color 0.18s,
    background 0.18s,
    box-shadow 0.18s;
}
.workspace-mode-switch button:hover {
  color: var(--main-color);
}
.workspace-mode-switch button[aria-pressed='true'] {
  background: var(--bg-elevated);
  color: var(--main-color);
  box-shadow: 0 2px 6px rgb(0 0 0 / 5%);
  font-weight: 600;
}
.workspace-mode-switch button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
</style>
