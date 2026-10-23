<!-- Adapted from original frontend, MIT licensed. -->
<template>
  <div class="workspace-state" :class="`is-${kind}`">
    <div class="workspace-state__icon" aria-hidden="true">
      <LoadingOutlined v-if="kind === 'loading'" class="is-loading" />
      <WarningOutlined v-else-if="kind === 'error' || kind === 'stale'" />
      <FileTextOutlined v-else />
    </div>
    <strong>{{ title || workspaceStateLabel(kind) }}</strong>
    <p v-if="message">{{ message }}</p>
    <a-button v-if="kind === 'error' || kind === 'stale'" type="link" @click="emit('retry')"
      >重试</a-button
    >
  </div>
</template>

<script setup lang="ts">
import { FileTextOutlined, LoadingOutlined, WarningOutlined } from '@ant-design/icons-vue'
import { Button as AButton } from 'ant-design-vue'
const workspaceStateLabel = (kind: string): string =>
  ({ loading: '正在加载', error: '加载失败', stale: '数据已过期', empty: '暂无数据' })[
    kind as 'loading'
  ] ?? '暂无数据'

defineProps({
  kind: { type: String, default: 'empty' },
  title: { type: String, default: '' },
  message: { type: String, default: '' }
})

const emit = defineEmits(['retry'])
</script>

<style scoped lang="scss">
.workspace-state {
  display: grid;
  min-height: 180px;
  place-content: center;
  justify-items: center;
  gap: var(--workspace-space-2);
  padding: var(--workspace-space-6);
  color: var(--color-text-secondary);
  text-align: center;

  &__icon {
    display: grid;
    width: 36px;
    height: 36px;
    place-items: center;
    border-radius: 50%;
    background: var(--gray-10);
    color: var(--main-color);
    font-size: 18px;
  }

  strong {
    color: var(--color-text);
    font-size: 14px;
  }

  p {
    max-width: 360px;
    margin: 0;
    color: var(--color-text-secondary);
    font-size: 12px;
  }

  &.is-loading .workspace-state__icon,
  &.is-empty .workspace-state__icon {
    background: var(--main-50);
    color: var(--main-color);
  }

  &.is-error .workspace-state__icon {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }

  &.is-stale .workspace-state__icon {
    background: var(--color-warning-50);
    color: var(--color-warning-900);
  }

  &.is-warning .workspace-state__icon {
    background: var(--color-warning-50);
    color: var(--color-warning-900);
  }

  &.is-forbidden .workspace-state__icon {
    background: var(--gray-100);
    color: var(--gray-700);
  }

  .is-loading {
    animation: workspace-spin 1s linear infinite;
  }
}

@keyframes workspace-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
