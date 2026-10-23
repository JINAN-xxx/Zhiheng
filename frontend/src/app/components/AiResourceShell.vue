<!-- Adapted from original AiResourceShell.vue, MIT licensed. -->
<template>
  <section class="ai-resource-shell" :class="{ 'is-inset': inset }">
    <div class="ai-resource-shell__content"><slot /></div>
  </section>
</template>

<script setup lang="ts">
defineProps({ inset: { type: Boolean, default: false } })
</script>

<style scoped lang="scss">
.ai-resource-shell {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  padding: 0;
}
.ai-resource-shell__content {
  display: flex;
  min-width: 0;
  min-height: 0;
  width: 100%;
  height: 100%;
  flex: 1;
  flex-direction: column;
}
.ai-resource-shell__content :deep(> .app-container) {
  padding: 0;
}
.ai-resource-shell.is-inset {
  padding: 20px;
  background: var(--bg-container);
}
@media (min-width: 992px) {
  .ai-resource-shell.is-inset {
    padding: 0;
  }
}
@media (max-width: 760px) {
  .ai-resource-shell {
    padding: 14px;
  }
}
</style>
