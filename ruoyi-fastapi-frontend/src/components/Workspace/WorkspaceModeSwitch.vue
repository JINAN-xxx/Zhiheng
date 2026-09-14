<template>
  <div v-if="canChat && canWork" class="workspace-mode-switch" role="group" aria-label="选择聊天或工作">
    <button type="button" :aria-pressed="modelValue === 'chat'" @click="emit('update:modelValue', 'chat')">聊天</button>
    <button type="button" :aria-pressed="modelValue === 'work'" @click="emit('update:modelValue', 'work')">工作</button>
  </div>
</template>

<script setup>
defineProps({
  modelValue: { type: String, required: true },
  canChat: { type: Boolean, default: true },
  canWork: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue'])
</script>

<style scoped>
.workspace-mode-switch { display: flex; width: min(320px, 100%); gap: 4px; padding: 4px; background: var(--gray-50); border: 1px solid var(--gray-100); border-radius: 28px; }
.workspace-mode-switch button { flex: 1; min-width: 0; height: 44px; border: 0; border-radius: 24px; background: transparent; color: var(--color-text-secondary); font: inherit; font-size: 15px; cursor: pointer; }
.workspace-mode-switch button[aria-pressed="true"] { background: var(--bg-container); color: var(--color-text); box-shadow: 0 1px 4px rgb(0 0 0 / 6%); font-weight: 600; }
.workspace-mode-switch button:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; }
</style>
