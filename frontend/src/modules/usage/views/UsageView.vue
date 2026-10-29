<script setup lang="ts">
import { computed, ref } from 'vue'
import { TabPane as ATabPane, Tabs as ATabs } from 'ant-design-vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { identity } from '../../identity/public.ts'
import { creditAccess } from '../../billing/presentation.ts'
import CreditsPanel from '../../billing/components/CreditsPanel.vue'
import TokenUsageView from './TokenUsageView.vue'
const tab = ref<string | number>('mine')
const access = computed(() => creditAccess(identity.value))
</script>
<template>
  <main class="usage-page" aria-label="用量与调用">
    <WorkspaceToolbar title="用量与调用" />
    <ATabs v-model:active-key="tab" class="usage-tabs" :destroy-inactive-tab-pane="true">
      <ATabPane key="mine" tab="我的积分"><CreditsPanel /></ATabPane>
      <ATabPane v-if="access.read" key="accounts" tab="用户积分"
        ><CreditsPanel management
      /></ATabPane>
      <ATabPane key="history" tab="历史 Token"><TokenUsageView /></ATabPane>
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
  background: var(--workspace-bg-page, #f7f8fc);
  font-family: var(--workspace-font-family);
}
.usage-tabs {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.usage-tabs > :deep(.ant-tabs-nav) {
  margin: 0 24px 20px;
}
.usage-tabs > :deep(.ant-tabs-content-holder) {
  min-width: 0;
}
@media (max-width: 640px) {
  .usage-tabs > :deep(.ant-tabs-nav) {
    margin: 0 14px 16px;
  }
}
</style>
