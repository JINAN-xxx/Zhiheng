<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { message as feedback, Tooltip as ATooltip } from 'ant-design-vue'
import {
  CopyOutlined,
  CheckOutlined,
  RedoOutlined,
  LikeOutlined,
  DislikeOutlined,
  SoundOutlined,
  ShareAltOutlined
} from '@ant-design/icons-vue'
const props = withDefaults(defineProps<{ content: string; role?: 'user' | 'assistant' }>(), {
  role: 'assistant'
})
const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
const unsupported = [
  { label: '重新生成', icon: RedoOutlined },
  { label: '赞同', icon: LikeOutlined },
  { label: '不赞同', icon: DislikeOutlined },
  { label: '朗读', icon: SoundOutlined },
  { label: '分享', icon: ShareAltOutlined }
]
async function copy(): Promise<void> {
  if (!props.content) return
  try {
    if (!navigator.clipboard?.writeText) throw new Error('当前浏览器无法直接复制，请选中文本后复制')
    await navigator.clipboard.writeText(props.content)
    copied.value = true
    clearTimeout(timer)
    timer = setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch (reason) {
    feedback.error(reason instanceof Error ? reason.message : '复制失败，请允许剪贴板访问后重试')
  }
}
onBeforeUnmount(() => clearTimeout(timer))
</script>
<template>
  <div
    class="message-actions"
    role="toolbar"
    :aria-label="role === 'user' ? '提问操作' : '回复操作'"
  >
    <ATooltip :title="copied ? '已复制' : '复制'"
      ><button
        type="button"
        :aria-label="role === 'user' ? '复制提问' : '复制回复'"
        :disabled="!content"
        :class="{ copied }"
        @click="copy"
      >
        <CheckOutlined v-if="copied" /><CopyOutlined v-else /></button
    ></ATooltip>
    <template v-if="role === 'assistant'"
      ><ATooltip
        v-for="action in unsupported"
        :key="action.label"
        :title="'暂不支持' + action.label"
        ><span class="message-actions__disabled"
          ><button
            type="button"
            :aria-label="action.label"
            :title="'暂不支持' + action.label"
            disabled
          >
            <component :is="action.icon" /></button></span></ATooltip
    ></template>
    <span class="sr-only" aria-live="polite">{{ copied ? '已复制' : '' }}</span>
  </div>
</template>
<style scoped>
.message-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}
.message-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-tertiary);
  font: inherit;
  font-size: 18px;
  cursor: pointer;
}
.message-actions button:not(:disabled):hover {
  color: var(--gray-700);
  background: var(--gray-50);
}
.message-actions button.copied {
  color: var(--main-color);
}
.message-actions button:disabled {
  opacity: 0.55;
  cursor: default;
}
.message-actions__disabled {
  display: inline-flex;
}
.message-actions button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
</style>
