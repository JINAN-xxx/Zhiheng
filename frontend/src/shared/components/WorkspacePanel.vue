<!-- Adapted from the original platform UI, MIT licensed. -->
<template>
  <section class="workspace-panel" :class="{ 'is-flush': flush }">
    <div v-if="title || $slots.header" class="workspace-panel__header">
      <div>
        <h2 v-if="title" class="workspace-panel__title">{{ title }}</h2>
        <p v-if="subtitle" class="workspace-panel__subtitle">{{ subtitle }}</p>
      </div>
      <slot name="header" />
    </div>
    <div class="workspace-panel__body"><slot /></div>
  </section>
</template>

<script setup lang="ts">
defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  flush: { type: Boolean, default: false }
})
</script>

<style scoped lang="scss">
.workspace-panel {
  overflow: hidden;
  background: var(--bg-container);
  border: 1px solid var(--gray-150);
  border-radius: 8px;

  &__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--workspace-space-4);
    padding: var(--workspace-space-4) var(--workspace-space-5);
    border-bottom: 1px solid var(--gray-150);
  }

  &__title {
    margin: 0;
    color: var(--color-text);
    font-size: 16px;
    font-weight: 500;
    line-height: 1.4;
  }

  &__subtitle {
    margin: 3px 0 0;
    color: var(--color-text-secondary);
    font-size: 12px;
  }

  &__body {
    padding: var(--workspace-space-5);
  }

  &.is-flush &__body {
    padding: 0;
  }
}
</style>
