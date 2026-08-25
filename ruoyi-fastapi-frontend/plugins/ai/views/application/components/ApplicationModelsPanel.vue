<template>
  <section class="models-panel">
    <div class="models-summary"><strong>模型授权</strong><span>汇总当前应用所有访问密钥中的有效授权</span></div>
    <WorkspaceState v-if="!canList" kind="forbidden" title="无权查看模型授权" message="当前账号缺少访问密钥列表权限。" />
    <WorkspaceState v-else-if="loading && !rows.length" kind="loading" />
    <WorkspaceState v-else-if="error && !rows.length" kind="error" :message="error" @retry="$emit('retry')" />
    <WorkspaceState v-else-if="!rows.length" kind="empty" title="暂无模型授权" message="创建访问密钥并选择模型后，授权关系会展示在这里。" />
    <ResourceTable v-else :columns="columns" :rows="rows" :pagination="false" row-key="modelId">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'model'">
          <div class="model-identity"><strong>{{ record.modelName }}</strong><code>{{ record.modelCode }}</code></div>
        </template>
        <template v-else-if="column.key === 'activeKeyCount'">{{ record.activeKeyCount }} / {{ record.keyCount }}</template>
      </template>
    </ResourceTable>
  </section>
</template>

<script setup>
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import { aggregateGrantedModels } from '../../../utils/application.js'

const props = defineProps({
  keys: { type: Array, default: () => [] },
  models: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  canList: { type: Boolean, default: false },
})
defineEmits(['retry'])

const rows = computed(() => aggregateGrantedModels(props.keys, props.models))
const columns = [
  { title: '模型', key: 'model' },
  { title: '有效密钥 / 授权密钥', key: 'activeKeyCount', width: 180 },
]
</script>

<style scoped lang="scss">
.models-summary {
  display: grid;
  gap: 3px;
  min-height: 64px;
  align-content: center;
  padding: 12px 18px;
  border-bottom: 1px solid var(--gray-150);
}

.models-summary span,
.model-identity code {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.model-identity {
  display: grid;
  gap: 4px;
}

.model-identity code {
  font-family: var(--workspace-font-mono);
}
</style>
