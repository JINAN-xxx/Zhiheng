<template>
  <div class="antd-async-state" :class="`is-${kind}`">
    <slot :kind="kind" :name="kind">
      <a-spin v-if="kind === 'loading'" size="large" />
      <a-alert
        v-else-if="kind === 'stale'"
        show-icon
        type="warning"
        message="数据可能已过期"
        :description="message || '刷新失败，当前继续展示最近一次成功数据。'"
      />
      <a-result
        v-else
        :status="resultStatus"
        :title="title || defaultTitle"
        :sub-title="message"
      >
        <template v-if="retryable" #extra>
          <a-button type="primary" @click="emit('retry')">重试</a-button>
        </template>
      </a-result>
    </slot>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  kind: { type: String, default: 'empty' },
  title: { type: String, default: '' },
  message: { type: String, default: '' },
})

const emit = defineEmits(['retry'])

const defaultTitle = computed(() => ({
  empty: '暂无数据',
  error: '加载失败',
  misconfigured: '服务尚未配置',
  ready: '',
}[props.kind] ?? '暂无数据'))
const resultStatus = computed(() => props.kind === 'error' ? 'error' : props.kind === 'misconfigured' ? 'warning' : 'info')
const retryable = computed(() => props.kind === 'error' || props.kind === 'misconfigured')
</script>

<style scoped>
.antd-async-state {
  display: grid;
  min-height: 180px;
  place-items: center;
}

.antd-async-state.is-stale {
  display: block;
  min-height: auto;
}
</style>
