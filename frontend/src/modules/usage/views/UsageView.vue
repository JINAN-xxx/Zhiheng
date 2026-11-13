<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { TabPane as ATabPane, Tabs as ATabs } from 'ant-design-vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { useRoute } from 'vue-router'
import { identity } from '../../identity/public.ts'
import { creditAccess } from '../../billing/public.ts'
import { CreditsPanel, CreditBillingControl } from '../../billing/public.ts'
import type { CreditState } from '../../billing/public.ts'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import TokenUsageView from './TokenUsageView.vue'
const route = useRoute()
const tab = ref<string | number>(route.query.tab === 'history' ? 'history' : 'mine')
watch(
  () => route.query.tab,
  (value) => {
    if (value === 'history') tab.value = 'history'
  }
)
const access = computed(() => creditAccess(identity.value))
interface CreditPanelHandle {
  reload: () => Promise<void>
}
interface BillingControlHandle {
  refresh: () => Promise<void>
}
const minePanel = ref<CreditPanelHandle | null>(null),
  accountsPanel = ref<CreditPanelHandle | null>(null)
const billingControl = ref<BillingControlHandle | null>(null),
  billingState = ref<CreditState | null>(null)
const mineLoading = ref(false),
  accountsLoading = ref(false),
  modeLoading = ref(false)
const activePanel = computed(() =>
  tab.value === 'accounts' ? accountsPanel.value : minePanel.value
)
const refreshing = computed(
  () => modeLoading.value || (tab.value === 'accounts' ? accountsLoading.value : mineLoading.value)
)
async function refreshCredits(): Promise<void> {
  await Promise.all([activePanel.value?.reload(), billingControl.value?.refresh()])
}
function modeChanged(): void {
  void activePanel.value?.reload()
}
watch(
  () => access.value.read,
  (allowed) => {
    if (!allowed && tab.value === 'accounts') tab.value = 'mine'
  }
)
</script>
<template>
  <main class="usage-page" aria-label="用量与调用">
    <WorkspaceToolbar class="usage-toolbar" title="用量与调用"
      ><template #actions>
        <div v-if="tab !== 'history'" class="usage-credit-actions">
          <CreditBillingControl
            ref="billingControl"
            @state="billingState = $event"
            @loading="modeLoading = $event"
            @changed="modeChanged"
          />
          <RefreshButton
            label="刷新积分"
            :loading="refreshing"
            :disabled="!activePanel"
            @click="refreshCredits"
          />
        </div> </template
    ></WorkspaceToolbar>
    <ATabs v-model:active-key="tab" class="usage-tabs" :destroy-inactive-tab-pane="true">
      <ATabPane key="mine" tab="我的积分"
        ><CreditsPanel
          ref="minePanel"
          :billing-state="billingState"
          @loading="mineLoading = $event"
      /></ATabPane>
      <ATabPane v-if="access.read" key="accounts" tab="用户积分"
        ><CreditsPanel
          ref="accountsPanel"
          management
          :billing-state="billingState"
          @loading="accountsLoading = $event"
      /></ATabPane>
      <ATabPane key="history" tab="Token 用量"
        ><TokenUsageView
          :account-id="
            access.read && typeof route.query.userId === 'string' ? route.query.userId : undefined
          "
          :account-name="
            typeof route.query.userName === 'string' ? route.query.userName : undefined
          "
      /></ATabPane>
    </ATabs>
  </main>
</template>
<style scoped>
.usage-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  min-height: 0;
  color: var(--color-text);
  background: var(--bg-container);
  font-family: var(--workspace-font-family);
}
.usage-toolbar {
  padding: 24px 28px 14px;
}
.usage-toolbar :deep(.workspace-toolbar__title) {
  font-size: 24px;
  font-weight: 600;
}
.usage-credit-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.usage-tabs {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.usage-tabs > :deep(.ant-tabs-nav) {
  flex-shrink: 0;
  margin: 0 28px 22px;
}
.usage-tabs > :deep(.ant-tabs-content-holder) {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  scrollbar-gutter: stable;
}
@media (max-width: 640px) {
  .usage-toolbar {
    padding: 20px 16px 16px;
  }
  .usage-tabs > :deep(.ant-tabs-nav) {
    margin: 0 16px 18px;
  }
}
</style>
