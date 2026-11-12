<!-- Adapted from original WorkspacePage, MIT licensed. -->
<template>
  <section class="antd-workspace-page">
    <header v-if="title || subtitle || $slots.actions" class="antd-workspace-page__header">
      <div class="antd-workspace-page__heading">
        <h1 v-if="title">{{ title }}</h1>
        <p v-if="subtitle">{{ subtitle }}</p>
      </div>
      <div v-if="$slots.actions" class="antd-workspace-page__actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="antd-workspace-page__body">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' }
})
</script>

<style scoped lang="scss">
.antd-workspace-page {
  width: min(100%, var(--workspace-content-max-width));
  min-width: 0;
  margin: 0 auto;
  padding: 28px 32px;
}

.antd-workspace-page__header {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;
}

.antd-workspace-page__heading {
  min-width: 0;

  h1 {
    margin: 0;
    color: var(--workspace-text);
    font-size: var(--workspace-font-size-title);
    font-weight: var(--workspace-font-weight-medium);
    line-height: 1.35;
  }

  p {
    margin: 5px 0 0;
    color: var(--workspace-text-secondary);
    font-size: var(--workspace-font-size-helper);
    line-height: var(--workspace-line-height);
  }
}

.antd-workspace-page__actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}

.antd-workspace-page__body {
  min-width: 0;
}

@media (max-width: 767px) {
  .antd-workspace-page {
    padding: 16px;
  }
  .antd-workspace-page__header {
    align-items: stretch;
    flex-direction: column;
  }
  .antd-workspace-page__actions {
    flex-wrap: wrap;
  }
}
</style>
