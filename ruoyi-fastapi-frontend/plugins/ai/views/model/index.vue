<template>
  <main class="app-container ai-model-center">
    <WorkspaceToolbar title="模型中心" subtitle="管理模型绑定、发布状态和计费规则">
      <template #actions>
        <a-tooltip title="刷新当前视图" placement="bottom">
          <a-button shape="circle" aria-label="刷新当前视图" @click="refreshCurrent">
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-button v-if="canOnboard" type="primary" @click="onboardingOpen = true">
          <template #icon><PlusOutlined /></template>
          接入模型
        </a-button>
      </template>
    </WorkspaceToolbar>

    <section class="model-workspace">
      <a-tabs v-model:active-key="activeTab" class="workspace-tabs">
        <a-tab-pane v-if="can('ai:model:list')" key="platform" tab="平台模型">
          <PlatformModelsPanel ref="platformPanel" :permissions="permissions" :mobile="isMobile" />
        </a-tab-pane>
        <a-tab-pane v-if="can('ai:engine:credential:list')" key="credentials" tab="凭据管理">
          <CredentialsPanel ref="credentialPanel" :permissions="permissions" :mobile="isMobile" />
        </a-tab-pane>
      </a-tabs>
    </section>

    <ModelOnboardingDrawer v-model="onboardingOpen" @completed="refreshAfterOnboarding" />
  </main>
</template>

<script setup name="AiModel">
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import useUserStore from '@/store/modules/user'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import { hasAllPermissions, hasPermission } from '../../utils/modelEngine.js'
import CredentialsPanel from './components/CredentialsPanel.vue'
import ModelOnboardingDrawer from './components/ModelOnboardingDrawer.vue'
import PlatformModelsPanel from './components/PlatformModelsPanel.vue'

const userStore = useUserStore()
const permissions = computed(() => userStore.permissions || [])
const can = (permission) => hasPermission(permissions.value, permission)
const onboardingPermissions = [
  'ai:engine:credential:list', 'ai:engine:credential:add', 'ai:engine:deployment:add',
  'ai:engine:deployment:list',
  'ai:model:list', 'ai:model:add', 'ai:model:query', 'ai:model:edit', 'ai:model:publish', 'ai:model:test',
]
const canOnboard = computed(() => hasAllPermissions(permissions.value, onboardingPermissions))
const isMobile = ref(false)
const updateMobile = () => { isMobile.value = window.innerWidth <= 760 }
const onboardingOpen = ref(false)
const platformPanel = ref()
const credentialPanel = ref()
const activeTab = ref('platform')
const availableTabs = computed(() => [
  can('ai:model:list') && 'platform',
  can('ai:engine:credential:list') && 'credentials',
].filter(Boolean))

watch(availableTabs, (tabs) => {
  if (!tabs.includes(activeTab.value)) activeTab.value = tabs[0] || ''
}, { immediate: true })

onMounted(() => { updateMobile(); window.addEventListener('resize', updateMobile) })
onBeforeUnmount(() => window.removeEventListener('resize', updateMobile))

function refreshCurrent() {
  const panels = { platform: platformPanel, credentials: credentialPanel }
  panels[activeTab.value]?.value?.refresh()
}

function refreshAfterOnboarding() {
  platformPanel.value?.refresh()
  credentialPanel.value?.refresh()
}
</script>

<style scoped>
.ai-model-center {
  --ai-font-body: system-ui, "Noto Sans SC", "HarmonyOS Sans SC", MiSans, "PingFang SC", "Microsoft YaHei", sans-serif;
  --ai-font-mono: ui-monospace, SFMono-Regular, Consolas, monospace;
  min-width: 0;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  color: var(--color-text);
  font-family: var(--ai-font-body);
  font-size: 13px;
  letter-spacing: 0;
}
.header-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; }
.model-workspace {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 0 24px 24px;
  background: var(--bg-container);
}
.workspace-tabs { min-height: 0; flex: 1; }
.workspace-tabs :deep(.ant-tabs-nav) { margin: 0 0 12px; border-bottom: 1px solid var(--gray-150); }
.workspace-tabs :deep(.ant-tabs-nav::before) { border-bottom: 0; }
.workspace-tabs :deep(.ant-tabs-tab) { height: 44px; margin: 0; padding: 0 16px; color: var(--color-text-secondary); font-weight: 500; }
.workspace-tabs :deep(.ant-tabs-tab-active .ant-tabs-tab-btn) { color: var(--main-700); }
.workspace-tabs :deep(.ant-tabs-ink-bar) { background: var(--main-700); }
.workspace-tabs :deep(.ant-tabs-content-holder),
.workspace-tabs :deep(.ant-tabs-content),
.workspace-tabs :deep(.ant-tabs-tabpane) { min-height: 0; }
:global(.model-onboarding-drawer .ant-alert) { border-radius: 6px; }
:global(.model-onboarding-drawer) {
  --ai-font-body: system-ui, "Noto Sans SC", "HarmonyOS Sans SC", MiSans, "PingFang SC", "Microsoft YaHei", sans-serif;
  --ai-font-mono: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-family: var(--ai-font-body);
  font-size: 13px;
  letter-spacing: 0;
}
:global(.model-onboarding-drawer .ant-drawer-content) { background: var(--bg-elevated); color: var(--color-text); }
:global(.model-onboarding-drawer .ant-drawer-header) { margin-bottom: 16px; border-bottom: 1px solid var(--gray-150); background: var(--bg-elevated); padding-bottom: 16px; }
:global(.model-onboarding-drawer .ant-drawer-title) { color: var(--color-text); font-size: 16px; font-weight: 600; }
:global(.model-onboarding-drawer .ant-segmented), :global(.model-onboarding-drawer .ant-btn) { border-radius: 6px; }
@media (max-width: 760px) {
  .ai-model-center { padding: 0; }
  .ai-model-center :deep(.workspace-toolbar) { align-items: flex-start; flex-wrap: wrap; padding: 0; }
  .ai-model-center :deep(.workspace-toolbar__leading) { width: 100%; }
  .ai-model-center :deep(.workspace-toolbar__actions) { width: 100%; flex-wrap: wrap; justify-content: flex-end; }
  .model-workspace { padding: 0 14px 12px; }
  :global(.model-onboarding-drawer) { width: 100% !important; }
  :global(.model-onboarding-drawer .ant-drawer-body) { padding: 14px; }
}
</style>
