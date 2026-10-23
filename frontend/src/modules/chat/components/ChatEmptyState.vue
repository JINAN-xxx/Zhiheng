<!-- Adapted from original frontend, MIT licensed. -->
<template>
  <section class="chat-empty-state">
    <span class="chat-empty-state__symbol" aria-hidden="true"><CommentOutlined /></span>
    <h2>有什么可以帮你？</h2>
    <p>从一个想法开始，把思路聊清楚。</p>
    <div class="chat-starters">
      <a-button aria-label="总结内容" @click="emit('prompt', '帮我梳理这段内容的关键观点：')"
        ><FileTextOutlined />总结内容</a-button
      >
      <a-button aria-label="解释代码" @click="emit('prompt', '帮我解释这段代码：')"
        ><CodeOutlined />解释代码</a-button
      >
      <a-button aria-label="整理思路" @click="emit('prompt', '帮我整理思路并给出几个可行方案：')"
        ><BulbOutlined />整理思路</a-button
      >
    </div>
  </section>
</template>

<script setup lang="ts">
import {
  BulbOutlined,
  CodeOutlined,
  CommentOutlined,
  FileTextOutlined
} from '@ant-design/icons-vue'
import { Button as AButton } from 'ant-design-vue'
const emit = defineEmits<{ prompt: [value: string] }>()
</script>

<style scoped>
.chat-empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 0;
  text-align: center;
}
.chat-empty-state__symbol {
  display: grid;
  width: 48px;
  height: 48px;
  margin-bottom: 4px;
  place-items: center;
  border: 1px solid var(--main-100);
  border-radius: 16px;
  background: var(--main-50);
  color: var(--main-color);
  font-size: 24px;
}
.chat-empty-state h2 {
  margin: 0;
  color: var(--color-text);
  font-size: clamp(26px, 3vw, 36px);
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: -0.5px;
}
.chat-empty-state p {
  margin: -4px 0 6px;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.7;
}
.chat-starters {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 2px;
}
.chat-starters :deep(.ant-btn) {
  height: 34px;
  padding: 0 12px;
  border-color: var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container);
  color: var(--color-text-secondary);
  font-size: 13px;
  box-shadow: none;
}
.chat-starters :deep(.ant-btn:hover) {
  color: var(--main-color);
  border-color: var(--main-200);
}
@media (max-width: 600px) {
  .chat-empty-state {
    gap: 14px;
  }
  .chat-empty-state__symbol {
    width: 42px;
    height: 42px;
    border-radius: 14px;
    font-size: 22px;
  }
  .chat-empty-state p {
    font-size: 13px;
  }
  .chat-starters {
    gap: 6px;
  }
  .chat-starters :deep(.ant-btn) {
    padding: 0 10px;
  }
}
</style>
