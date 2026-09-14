<template>
  <section class="chat-empty-state">
    <h2>有什么可以帮你？</h2>
    <WorkspaceModeSwitch model-value="chat" :can-work="canWork" @update:model-value="emit('mode', $event)" />
    <p>从一个问题开始，也可以选择技能来帮忙。</p>
    <div class="chat-starters">
      <a-button @click="emit('prompt', '帮我梳理这段内容的关键观点：')">总结内容</a-button>
      <a-button @click="emit('prompt', '帮我解释这段代码：')">解释代码</a-button>
      <a-button @click="emit('prompt', '帮我整理思路并给出几个可行方案：')">整理思路</a-button>
    </div>
  </section>
</template>

<script setup>
import WorkspaceModeSwitch from '@/components/Workspace/WorkspaceModeSwitch.vue'
defineProps({ canWork: { type: Boolean, default: false } })
const emit = defineEmits(['mode', 'prompt'])
</script>

<style scoped>
.chat-empty-state { display: flex; min-height: 100%; flex-direction: column; align-items: center; justify-content: center; gap: 24px; padding: 24px 0; text-align: center; }
.chat-empty-state h2 { margin: 0; color: var(--color-text); font-size: clamp(24px, 3vw, 32px); font-weight: 600; }
.chat-empty-state p { margin: 0; color: var(--color-text-secondary); font-size: 14px; }
.chat-starters { display: flex; justify-content: center; flex-wrap: wrap; gap: 10px; }
.chat-starters :deep(.ant-btn) { border-radius: 18px; color: var(--color-text-secondary); }
@media (max-width: 600px) { .chat-empty-state { gap: 18px; } .chat-empty-state p { font-size: 13px; } }
</style>
