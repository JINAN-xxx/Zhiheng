<!-- Adapted from the original platform UI, MIT licensed. -->
<template>
  <span class="workspace-status" :class="[`is-${status}`, { 'is-label': !showDot }]">
    <span v-if="showDot" class="workspace-status__dot" aria-hidden="true" />
    <span>{{ label || workspaceStateLabel(status) }}</span>
  </span>
</template>

<script setup lang="ts">
defineProps({
  status: { type: String, default: 'ready' },
  label: { type: String, default: '' },
  showDot: { type: Boolean, default: true }
})
const workspaceStateLabel = (status: string) =>
  ({ ready: '就绪', active: '已启用', disabled: '已停用', failed: '失败', warning: '待核对' })[
    status
  ] ?? status
</script>

<style scoped lang="scss">
.workspace-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
  white-space: nowrap;

  &.is-label {
    width: fit-content;
    max-width: 100%;
    padding: 2px 8px;
    border-radius: 4px;
    background: var(--gray-50);
    line-height: 20px;
  }

  &__dot {
    flex: 0 0 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--workspace-text-muted);
  }

  &.is-ready .workspace-status__dot,
  &.is-active .workspace-status__dot {
    background: var(--workspace-success);
  }

  &.is-loading .workspace-status__dot,
  &.is-connecting .workspace-status__dot,
  &.is-streaming .workspace-status__dot {
    background: var(--workspace-main);
  }

  &.is-warning .workspace-status__dot,
  &.is-stale .workspace-status__dot {
    background: var(--workspace-warning);
  }

  &.is-error .workspace-status__dot,
  &.is-failed .workspace-status__dot {
    background: var(--workspace-danger);
  }
}
</style>
