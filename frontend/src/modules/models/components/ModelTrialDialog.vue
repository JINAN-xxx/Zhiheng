<script setup lang="ts">
import { computed } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Modal as AModal,
  Tag as ATag,
  Textarea as ATextarea
} from 'ant-design-vue'
import type { ChatModel } from '../api/index.ts'
import { useModelTrial } from '../composables/useModelTrial.ts'

const props = defineProps<{
  models: ChatModel[]
  canTest: boolean
  pending: Record<string, boolean>
  connectionIssues: Record<string, string>
}>()
const emit = defineEmits<{ refresh: []; denied: [] }>()
const {
  visible,
  model,
  prompt,
  documents,
  run,
  sentDocuments,
  submitting,
  observing,
  stopping,
  uncertain,
  error,
  active,
  working,
  pendingResult,
  unavailable,
  recoverable,
  open,
  close,
  submit,
  stop
} = useModelTrial(props, { refresh: () => emit('refresh'), denied: () => emit('denied') })
const status = computed(() => {
  if (submitting.value) return '正在提交'
  if (!run.value) return uncertain.value ? '待确认提交' : '尚未开始'
  return {
    pending: '等待执行',
    running: '正在测试',
    cancel_requested: '正在停止',
    completed: '测试完成',
    failed: '测试失败',
    cancelled: '已停止',
    interrupted: '测试中断'
  }[run.value.status]
})
const primaryText = computed(() =>
  uncertain.value ? '确认原提交' : active.value ? '继续查看' : '开始测试'
)
const embeddingDimension = computed(() => {
  const data = run.value?.result?.data
  if (!Array.isArray(data) || !data[0] || typeof data[0] !== 'object') return null
  const vector: unknown = (data[0] as Record<string, unknown>).embedding
  return Array.isArray(vector) &&
    vector.length &&
    vector.every((item) => typeof item === 'number' && Number.isFinite(item))
    ? vector.length
    : null
})
const ranking = computed(() => {
  const rows = run.value?.result?.results
  if (!Array.isArray(rows)) return []
  return rows.flatMap((item: unknown) => {
    if (!item || typeof item !== 'object') return []
    const value = item as Record<string, unknown>
    if (
      typeof value.index !== 'number' ||
      !Number.isSafeInteger(value.index) ||
      value.index < 0 ||
      value.index >= sentDocuments.value.length ||
      typeof value.relevance_score !== 'number' ||
      !Number.isFinite(value.relevance_score)
    )
      return []
    return [
      { index: value.index, score: value.relevance_score, text: sentDocuments.value[value.index] }
    ]
  })
})
defineExpose({ open })
</script>
<template>
  <AModal
    :open="visible"
    :width="760"
    centered
    :mask-closable="false"
    :closable="!working && !pendingResult"
    :keyboard="!working && !pendingResult"
    wrap-class-name="model-trial-modal"
    @cancel="close"
  >
    <template #title
      ><span class="trial-title">{{ '测试模型 · ' + (model?.name ?? '') }}</span></template
    >
    <template v-if="model">
      <AAlert
        v-if="unavailable"
        type="warning"
        show-icon
        :message="connectionIssues[model.id] || '模型已停用或权限已变化，请刷新后查看'"
        class="trial-notice"
      />
      <AAlert v-if="error" type="error" show-icon :message="error" class="trial-notice" />
      <AForm layout="vertical" :disabled="working || pendingResult || unavailable">
        <AFormItem :label="model.model_type === 'embedding' ? '测试文本' : '测试输入'" required>
          <ATextarea v-model:value="prompt" aria-label="测试内容" :rows="3" :maxlength="4000" />
        </AFormItem>
        <AFormItem v-if="model.model_type === 'rerank'" label="候选资料" required>
          <ATextarea
            v-model:value="documents"
            aria-label="候选资料"
            :rows="4"
            placeholder="每行一份候选资料，最多 8 份，每份最多 1000 个字符"
          />
        </AFormItem>
      </AForm>
      <div class="trial-result-header">
        <strong>测试结果</strong><ATag>{{ status }}</ATag>
      </div>
      <div class="trial-result" aria-live="polite">
        <p v-if="!run">测试用量按模型当前费率计费。</p>
        <template v-else>
          <AAlert v-if="run.error" type="error" show-icon :message="run.error.message" />
          <pre v-if="run.text" class="trial-answer">{{ run.text }}</pre>
          <details v-if="run.reasoning">
            <summary>思考过程</summary>
            <pre>{{ run.reasoning }}</pre>
          </details>
          <p v-if="embeddingDimension">已生成 1 份向量，维度 {{ embeddingDimension }}。</p>
          <ol v-if="ranking.length" class="trial-ranking">
            <li v-for="item in ranking" :key="item.index">
              <span>{{ item.text }}</span
              ><small>相关度 {{ item.score.toFixed(4) }}</small>
            </li>
          </ol>
          <p v-if="active && !run.text && !ranking.length && !embeddingDimension">
            正在等待模型返回…
          </p>
          <p v-if="run.usage.status !== 'pending'" class="trial-usage">
            {{
              run.usage.status === 'known'
                ? `输入 ${run.usage.input_tokens ?? 0} Token · 输出 ${run.usage.output_tokens ?? 0} Token`
                : '用量待核对'
            }}
          </p>
        </template>
      </div>
    </template>
    <template #footer>
      <AButton :disabled="working || pendingResult" @click="close">关闭</AButton>
      <AButton
        v-if="active"
        :loading="stopping"
        :disabled="run?.status === 'cancel_requested'"
        @click="stop"
        >停止测试</AButton
      >
      <AButton
        type="primary"
        aria-label="提交测试"
        :loading="submitting || observing"
        :disabled="working || (unavailable && !recoverable)"
        @click="submit"
        >{{ primaryText }}</AButton
      >
    </template>
  </AModal>
</template>
<style scoped>
.trial-title {
  display: block;
  padding-right: 28px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.trial-notice {
  margin-bottom: 16px;
}
.trial-result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 13px;
}
.trial-result {
  min-height: 120px;
  max-height: 36vh;
  overflow: auto;
  padding: 16px;
  background: var(--gray-25);
  border-radius: 8px;
}
.trial-result p {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.8;
}
.trial-answer,
.trial-result pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  font-size: 13px;
  line-height: 1.8;
}
.trial-result details {
  margin-top: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.trial-result summary {
  cursor: pointer;
  margin-bottom: 8px;
}
.trial-ranking {
  padding-left: 22px;
  margin: 0;
}
.trial-ranking li {
  margin: 0 0 12px;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.trial-ranking small {
  display: block;
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.trial-usage {
  margin: 12px 0 0;
}
</style>
