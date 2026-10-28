<!-- Adapted from the original platform UI, MIT licensed. -->
<template>
  <section class="models-panel">
    <div class="models-summary">
      <span>共 {{ rows.length }} 个授权模型</span>
    </div>
    <WorkspaceState
      v-if="!canView"
      kind="forbidden"
      title="无权查看模型授权"
      message="当前账号缺少访问密钥或模型目录权限。"
    />
    <WorkspaceState v-else-if="loading && !rows.length" kind="loading" />
    <WorkspaceState
      v-else-if="error && !rows.length"
      kind="error"
      :message="error"
      @retry="$emit('retry')"
    />
    <WorkspaceState
      v-else-if="!rows.length"
      kind="empty"
      title="暂无模型授权"
      message="创建访问密钥并选择模型后，授权关系会展示在这里。"
    />
    <ResourceTable v-else :columns="columns" :rows="rows" row-key="modelId">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'model'">
          <div class="model-identity">
            <strong>{{ record.modelName }}</strong
            ><code>{{ record.modelCode }}</code>
          </div>
        </template>
        <template v-else-if="column.key === 'activeKeyCount'"
          >{{ record.activeKeyCount }} / {{ record.keyCount }}</template
        >
        <template v-else-if="column.key === 'availability'"
          ><WorkspaceStatus
            :status="record.available ? 'active' : 'disabled'"
            :label="record.available ? '可用' : '不可用'"
        /></template>
      </template>
    </ResourceTable>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceStatus from '../../../shared/components/WorkspaceStatus.vue'
import { aggregateGrantedModels } from '../presentation.ts'
import type { DeveloperKey } from '../api/index.ts'
import type { ChatModel } from '../../models/public.ts'
const props = withDefaults(
  defineProps<{
    keys: DeveloperKey[]
    models: ChatModel[]
    loading?: boolean
    error?: string
    canView?: boolean
  }>(),
  { loading: false, error: '', canView: false }
)
defineEmits<{ retry: [] }>()
const rows = computed(() => aggregateGrantedModels(props.keys, props.models))
const columns = [
  { title: '模型', key: 'model' },
  { title: '状态', key: 'availability', width: 100 },
  { title: '有效密钥 / 授权密钥', key: 'activeKeyCount', width: 180 }
]
</script>

<style scoped lang="scss">
.models-summary {
  display: grid;
  gap: 3px;
  min-height: 54px;
  align-content: center;
  padding: 12px 18px;
  border-bottom: 1px solid var(--gray-150);
}

.models-summary span,
.model-identity code {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.models-summary strong {
  font-size: 16px;
  font-weight: 500;
}
.models-summary span {
  font-size: 13px;
}

.model-identity {
  display: grid;
  gap: 4px;
}

.model-identity code {
  font-family: var(--workspace-font-mono);
}
.model-identity strong {
  font-weight: 500;
}
</style>
