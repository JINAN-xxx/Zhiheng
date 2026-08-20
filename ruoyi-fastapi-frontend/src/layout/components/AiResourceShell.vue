<template>
  <section class="ai-resource-shell">
    <header class="ai-resource-shell__header">
      <div>
        <span>AI RESOURCE</span>
        <h1>AI 资源</h1>
        <p>统一管理模型能力、应用接入和调用用量。</p>
      </div>
      <nav class="ai-resource-tabs" aria-label="AI 资源导航">
        <router-link
          v-for="tab in tabs"
          :key="tab.key"
          :to="tab.path"
          :aria-current="route.path === tab.path ? 'page' : undefined"
        >
          {{ tab.label }}
        </router-link>
      </nav>
    </header>
    <div class="ai-resource-shell__content"><slot /></div>
  </section>
</template>

<script setup>
import { resolveAiResourceTabs } from '@/components/Workspace/workspaceTransforms.js'
import useUserStore from '@/store/modules/user'

const route = useRoute()
const userStore = useUserStore()
const tabs = computed(() => resolveAiResourceTabs(new Set(userStore.permissions)))
</script>

<style scoped lang="scss">
.ai-resource-shell { min-width: 0; min-height: 100%; padding: 20px; }
.ai-resource-shell__header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; max-width: var(--workspace-content-max-width); margin: 0 auto 18px; }
.ai-resource-shell__header span { color: var(--workspace-main-deep); font-size: 11px; font-weight: 700; }
.ai-resource-shell__header h1 { margin: 3px 0; font-size: 24px; }
.ai-resource-shell__header p { margin: 0; color: var(--workspace-text-secondary); font-size: 13px; }
.ai-resource-tabs { display: flex; min-width: 0; gap: 4px; padding: 4px; border: 1px solid var(--workspace-border); border-radius: var(--workspace-radius-sm); background: var(--workspace-surface); }
.ai-resource-tabs a { min-height: 34px; display: inline-flex; align-items: center; padding: 0 14px; border-radius: var(--workspace-radius-sm); color: var(--workspace-text-secondary); text-decoration: none; white-space: nowrap; }
.ai-resource-tabs a[aria-current='page'] { color: var(--workspace-main-deep); background: var(--workspace-main-soft); font-weight: 600; }
.ai-resource-shell__content { min-width: 0; max-width: var(--workspace-content-max-width); margin: 0 auto; }
.ai-resource-shell__content :deep(> .app-container) { padding: 0; }
@media (max-width: 760px) {
  .ai-resource-shell { padding: 12px; }
  .ai-resource-shell__header { align-items: stretch; flex-direction: column; gap: 12px; }
  .ai-resource-tabs { width: 100%; overflow-x: auto; }
  .ai-resource-tabs a { flex: 1; justify-content: center; }
}
</style>
