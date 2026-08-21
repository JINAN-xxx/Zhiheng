<template>
  <div class="workspace-state" :class="`is-${kind}`">
    <div class="workspace-state__icon" aria-hidden="true">
      <LoadingOutlined v-if="kind === 'loading'" class="is-loading" />
      <WarningOutlined v-else-if="kind === 'error' || kind === 'stale'" />
      <FileTextOutlined v-else />
    </div>
    <strong>{{ title || workspaceStateLabel(kind) }}</strong>
    <p v-if="message">{{ message }}</p>
    <a-button v-if="kind === 'error' || kind === 'stale'" type="link" @click="emit('retry')">重试</a-button>
  </div>
</template>

<script setup>
import { FileTextOutlined, LoadingOutlined, WarningOutlined } from '@ant-design/icons-vue'
import { workspaceStateLabel } from './workspaceTransforms.js'

defineProps({
  kind: { type: String, default: 'empty' },
  title: { type: String, default: '' },
  message: { type: String, default: '' },
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
  color: var(--workspace-text-secondary);
  text-align: center;

  &__icon {
    display: grid;
    width: 36px;
    height: 36px;
    place-items: center;
    border-radius: 50%;
    background: var(--workspace-surface-muted);
    color: var(--workspace-main-deep);
    font-size: 18px;
  }

  strong {
    color: var(--workspace-text);
    font-size: 14px;
  }

  p {
    max-width: 360px;
    margin: 0;
    color: var(--workspace-text-secondary);
    font-size: 12px;
  }

  .is-loading {
    animation: workspace-spin 1s linear infinite;
  }
}

@keyframes workspace-spin {
  to { transform: rotate(360deg); }
}
</style>
