<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { message as notice, Tooltip as ATooltip } from 'ant-design-vue'
import {
  CopyOutlined,
  CheckOutlined,
  RedoOutlined,
  LikeOutlined,
  DislikeOutlined,
  SoundOutlined,
  StopOutlined,
  ShareAltOutlined
} from '@ant-design/icons-vue'
import { speechPlayback } from '../speechPlayback.ts'
import type { SpeechState } from '../speechPlayback.ts'
const props = withDefaults(
  defineProps<{
    content: string
    role?: 'user' | 'assistant'
    regenerate?: boolean
    feedbackEnabled?: boolean
    share?: boolean
    feedbackValue?: 'up' | 'down' | null
    feedbackBusy?: boolean
    regenerateReason?: string
    feedbackReason?: string
    shareReason?: string
  }>(),
  {
    role: 'assistant',
    feedbackValue: null,
    regenerateReason: '',
    feedbackReason: '',
    shareReason: ''
  }
)
const emit = defineEmits<{
  regenerate: []
  feedback: [value: 'up' | 'down' | null]
  share: []
}>()
const copied = ref(false)
const speechState = ref<SpeechState>('idle')
const speechOwner = Symbol('message-speech')
let timer: ReturnType<typeof setTimeout> | undefined
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
    notice.error(reason instanceof Error ? reason.message : '复制失败，请允许剪贴板访问后重试')
  }
}
function speak(): void {
  speechPlayback.toggle(
    speechOwner,
    props.content,
    (state) => {
      speechState.value = state
    },
    (error) => {
      notice.error(error)
    }
  )
}
function act(action: 'regenerate' | 'share', reason: string): void {
  if (reason) {
    notice.info(reason)
    return
  }
  if (action === 'regenerate') emit('regenerate')
  else emit('share')
}
function vote(value: 'up' | 'down'): void {
  if (props.feedbackBusy) return
  if (props.feedbackReason) {
    notice.info(props.feedbackReason)
    return
  }
  emit('feedback', props.feedbackValue === value ? null : value)
}
watch(
  () => props.content,
  () => {
    speechPlayback.stop(speechOwner)
    copied.value = false
  }
)
onBeforeUnmount(() => {
  clearTimeout(timer)
  speechPlayback.stop(speechOwner)
})
</script>
<template>
  <div
    class="message-actions"
    role="toolbar"
    :aria-label="role === 'user' ? '提问操作' : '回复操作'"
  >
    <ATooltip v-if="content" :title="copied ? '已复制' : '复制'">
      <button
        type="button"
        :aria-label="role === 'user' ? '复制提问' : '复制回复'"
        :disabled="!content"
        :class="{ active: copied }"
        @click="copy"
      >
        <CheckOutlined v-if="copied" /><CopyOutlined v-else />
      </button>
    </ATooltip>
    <template v-if="role === 'assistant'">
      <ATooltip v-if="regenerate" :title="regenerateReason || '重新生成'">
        <button
          type="button"
          aria-label="重新生成"
          :class="{ 'has-label': !content }"
          :aria-disabled="Boolean(regenerateReason)"
          @click="act('regenerate', regenerateReason)"
        >
          <RedoOutlined /><span v-if="!content">重新生成</span>
        </button>
      </ATooltip>
      <template v-if="feedbackEnabled && content">
        <ATooltip :title="feedbackReason || (feedbackValue === 'up' ? '取消赞同' : '赞同')">
          <button
            type="button"
            aria-label="赞同"
            :aria-pressed="feedbackValue === 'up'"
            :aria-disabled="feedbackBusy || Boolean(feedbackReason)"
            :class="{ active: feedbackValue === 'up' }"
            @click="vote('up')"
          >
            <LikeOutlined />
          </button>
        </ATooltip>
        <ATooltip :title="feedbackReason || (feedbackValue === 'down' ? '取消不赞同' : '不赞同')">
          <button
            type="button"
            aria-label="不赞同"
            :aria-pressed="feedbackValue === 'down'"
            :aria-disabled="feedbackBusy || Boolean(feedbackReason)"
            :class="{ active: feedbackValue === 'down' }"
            @click="vote('down')"
          >
            <DislikeOutlined />
          </button>
        </ATooltip>
      </template>
      <ATooltip
        v-if="content"
        :title="
          speechState === 'idle' ? '朗读' : speechState === 'starting' ? '取消朗读' : '停止朗读'
        "
      >
        <button
          type="button"
          :aria-label="speechState === 'idle' ? '朗读' : '停止朗读'"
          :aria-pressed="speechState !== 'idle'"
          :disabled="!content"
          :class="{ active: speechState !== 'idle' }"
          @click="speak"
        >
          <SoundOutlined v-if="speechState === 'idle'" /><StopOutlined v-else />
        </button>
      </ATooltip>
      <ATooltip v-if="share && content" :title="shareReason || '分享'">
        <button
          type="button"
          aria-label="分享"
          :aria-disabled="Boolean(shareReason)"
          @click="act('share', shareReason)"
        >
          <ShareAltOutlined />
        </button>
      </ATooltip>
    </template>
    <span class="sr-only" aria-live="polite">{{
      copied ? '已复制' : speechState === 'playing' ? '正在朗读' : ''
    }}</span>
  </div>
</template>
<style scoped>
.message-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
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
.message-actions button.has-label {
  width: auto;
  gap: 6px;
  padding: 6px 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.message-actions button.active {
  color: var(--main-color);
}
.message-actions button:disabled,
.message-actions button[aria-disabled='true'] {
  opacity: 0.55;
  cursor: default;
}
.message-actions button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
</style>
