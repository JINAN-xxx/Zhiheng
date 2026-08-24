<template>
  <section class="ai-resource-shell">
    <div class="ai-resource-shell__content"><slot /></div>
  </section>
</template>

<style scoped lang="scss">
.ai-resource-shell { min-width: 0; min-height: 100%; padding: var(--page-padding); }
.ai-resource-shell__content { min-width: 0; max-width: var(--workspace-content-max-width); margin: 0 auto; }
.ai-resource-shell__content :deep(> .app-container) { padding: 0; }
@media (max-width: 760px) {
  .ai-resource-shell { padding: 14px; }
}
</style>
