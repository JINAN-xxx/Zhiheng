<template>
  <span class="workspace-status" :class="`is-${status}`">
    <span class="workspace-status__dot" aria-hidden="true" />
    <span>{{ label || workspaceStateLabel(status) }}</span>
  </span>
</template>

<script setup>
import { workspaceStateLabel } from './workspaceTransforms.js'

defineProps({
  status: { type: String, default: 'ready' },
  label: { type: String, default: '' },
})
</script>

<style scoped lang="scss">
.workspace-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--workspace-text-secondary);
  font-size: 12px;

  &__dot {
    width: 6px;
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
