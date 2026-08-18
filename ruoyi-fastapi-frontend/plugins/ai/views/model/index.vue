<template>
  <main class="app-container ai-model-center">
    <WorkspaceToolbar title="模型中心" subtitle="Model Service">
      <template #actions>
        <el-tooltip content="刷新当前视图" placement="bottom">
          <el-button circle :icon="Refresh" aria-label="刷新当前视图" @click="refreshCurrent" />
        </el-tooltip>
        <el-button v-if="canOnboard" type="primary" :icon="Plus" @click="onboardingOpen = true">接入模型</el-button>
      </template>
    </WorkspaceToolbar>

    <section class="model-workspace">
      <el-tabs v-model="activeTab" class="workspace-tabs">
        <el-tab-pane v-if="can('ai:model:list')" label="平台模型" name="platform">
          <PlatformModelsPanel ref="platformPanel" :permissions="permissions" :mobile="isMobile" />
        </el-tab-pane>
        <el-tab-pane v-if="can('ai:engine:deployment:list')" label="引擎部署" name="deployments" lazy>
          <EngineDeploymentsPanel ref="deploymentPanel" :permissions="permissions" :mobile="isMobile" />
        </el-tab-pane>
        <el-tab-pane v-if="can('ai:engine:credential:list')" label="Provider 凭据" name="credentials" lazy>
          <CredentialsPanel ref="credentialPanel" :permissions="permissions" :mobile="isMobile" />
        </el-tab-pane>
        <el-tab-pane v-if="can('ai:runtime:query')" label="运行状态" name="status" lazy>
          <RuntimeStatusPanel ref="statusPanel" />
        </el-tab-pane>
      </el-tabs>
    </section>

    <ModelOnboardingDrawer v-model="onboardingOpen" @completed="refreshAfterOnboarding" />
  </main>
</template>

<script setup name="AiModel">
import { Plus, Refresh } from '@element-plus/icons-vue'
import useUserStore from '@/store/modules/user'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import { hasAllPermissions, hasPermission } from '../../utils/modelEngine.js'
import CredentialsPanel from './components/CredentialsPanel.vue'
import EngineDeploymentsPanel from './components/EngineDeploymentsPanel.vue'
import ModelOnboardingDrawer from './components/ModelOnboardingDrawer.vue'
import PlatformModelsPanel from './components/PlatformModelsPanel.vue'
import RuntimeStatusPanel from './components/RuntimeStatusPanel.vue'

const activeTab = ref('platform')
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
const deploymentPanel = ref()
const credentialPanel = ref()
const statusPanel = ref()

const availableTabs = computed(() => [
  can('ai:model:list') && 'platform',
  can('ai:engine:deployment:list') && 'deployments',
  can('ai:engine:credential:list') && 'credentials',
  can('ai:runtime:query') && 'status',
].filter(Boolean))

watch(availableTabs, (tabs) => {
  if (!tabs.includes(activeTab.value)) activeTab.value = tabs[0] || ''
}, { immediate: true })

onMounted(() => { updateMobile(); window.addEventListener('resize', updateMobile) })
onBeforeUnmount(() => window.removeEventListener('resize', updateMobile))

function refreshCurrent() {
  const panels = {
    platform: platformPanel,
    deployments: deploymentPanel,
    credentials: credentialPanel,
    status: statusPanel,
  }
  panels[activeTab.value]?.value?.refresh()
}

function refreshAfterOnboarding() {
  platformPanel.value?.refresh()
  deploymentPanel.value?.refresh()
  credentialPanel.value?.refresh()
  statusPanel.value?.refresh()
}
</script>

<style scoped>
.ai-model-center {
  --ai-font-body: system-ui, "Noto Sans SC", "HarmonyOS Sans SC", MiSans, "PingFang SC", "Microsoft YaHei", sans-serif;
  --ai-font-mono: ui-monospace, SFMono-Regular, Consolas, monospace;
  min-width: 0;
  color: var(--el-text-color-primary);
  font-family: var(--ai-font-body);
  font-size: 13px;
  letter-spacing: 0;
}
.header-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; }
.model-workspace {
  min-width: 0;
  margin-top: var(--workspace-space-4);
  border: 1px solid var(--workspace-border);
  border-radius: var(--workspace-radius-md);
  background: var(--workspace-surface);
  padding: 0 18px 18px;
}
.workspace-tabs :deep(.el-tabs__header) { margin: 0 0 12px; }
.workspace-tabs :deep(.el-tabs__item) { height: 48px; padding: 0 18px; font-size: 13px; font-weight: 500; }
.workspace-tabs :deep(.el-table) { font-size: 12px; }
.workspace-tabs :deep(.el-table th.el-table__cell) { height: 42px; font-size: 12px; font-weight: 500; }
.workspace-tabs :deep(.el-table td.el-table__cell) { height: 46px; }
.workspace-tabs :deep(.el-tag) { border-radius: 4px; }
.workspace-tabs :deep(.el-alert), :global(.model-onboarding-drawer .el-alert) { border-radius: 6px; }
:global(.model-onboarding-drawer) {
  --ai-font-body: system-ui, "Noto Sans SC", "HarmonyOS Sans SC", MiSans, "PingFang SC", "Microsoft YaHei", sans-serif;
  --ai-font-mono: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-family: var(--ai-font-body);
  font-size: 13px;
  letter-spacing: 0;
}
:global(.model-onboarding-drawer .el-drawer__header) { margin-bottom: 16px; border-bottom: 1px solid var(--el-border-color-lighter); padding-bottom: 16px; }
:global(.model-onboarding-drawer .el-drawer__title) { font-size: 16px; font-weight: 600; }
:global(.model-onboarding-drawer .el-segmented), :global(.model-onboarding-drawer .el-button) { border-radius: 6px; }
@media (max-width: 760px) {
  .ai-model-center { padding: 12px; }
  .model-workspace { padding: 0 8px 12px; }
  .workspace-tabs :deep(.el-tabs__nav-wrap) { overflow-x: auto; }
  .workspace-tabs :deep(.el-tabs__nav) { min-width: max-content; }
  .workspace-tabs :deep(.el-tabs__item) { padding: 0 12px; }
  :global(.model-onboarding-drawer) { width: 100% !important; }
  :global(.model-onboarding-drawer .el-drawer__body) { padding: 14px; }
}
</style>
