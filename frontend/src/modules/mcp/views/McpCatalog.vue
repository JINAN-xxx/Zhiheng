<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Input as AInput,
  Modal as AModal
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { RightOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { errorMessage, RequestScope } from '../../../shared/http.ts'
import { canUseCapability } from '../../identity/public.ts'
import { listMcpServers, listMcpTools } from '../api/index.ts'
import type { CatalogTool } from '../api/index.ts'
import McpPage from '../components/McpPage.vue'

const scope = new RequestScope()
const rows = ref<CatalogTool[]>([])
const selected = ref<CatalogTool | null>(null)
const error = ref('')
const loading = ref(false)
const filter = ref('')
const canReview = computed(
  () => canUseCapability('mcp', 'configure') || canUseCapability('mcp', 'grant')
)
const filteredTools = computed(() => {
  const query = filter.value.trim().toLowerCase()
  return rows.value.filter((row) => (row.name + ' ' + row.serverName).toLowerCase().includes(query))
})
const columns: TableColumnsType<CatalogTool> = [
  { key: 'name', title: '工具', width: '31%' },
  { key: 'description', title: '用途' },
  { key: 'parameters', title: '操作', align: 'right', width: 116 }
]
async function reload() {
  const ticket = scope.replace()
  error.value = ''
  loading.value = true
  try {
    const tools = canReview.value
      ? (await listMcpServers(ticket.signal)).flatMap((server) =>
          server.tools.map(({ name, description, inputSchema, readOnly }) => ({
            name,
            description,
            inputSchema,
            readOnly,
            serverId: server.id,
            serverName: server.name,
            version: server.version
          }))
        )
      : await listMcpTools(ticket.signal)
    if (ticket.isCurrent()) rows.value = tools
  } catch (failure) {
    if (ticket.isCurrent()) error.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
watch(
  canReview,
  () => {
    rows.value = []
    selected.value = null
    void reload()
  },
  { immediate: true }
)
onBeforeUnmount(() => scope.dispose())
</script>

<template>
  <McpPage active="catalog">
    <template #actions
      ><RefreshButton label="刷新工具" :loading="loading" @click="reload"
    /></template>
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <div class="catalog-toolbar">
      <AInput
        v-model:value="filter"
        class="catalog-search"
        allow-clear
        placeholder="搜索工具或服务"
        aria-label="搜索 MCP 工具"
        ><template #prefix><SearchOutlined /></template
      ></AInput>
      <span class="catalog-count">{{ filteredTools.length }} 个工具</span>
    </div>
    <ResourceTable
      class="catalog-table"
      :rows="filteredTools"
      :columns="columns"
      :row-key="(row: CatalogTool) => row.serverId + ':' + row.name"
      :loading="loading"
      scroll-x="100%"
      :empty-text="rows.length ? '暂无匹配工具' : canReview ? '暂无同步工具' : '暂无可用工具'"
    >
      <template #bodyCell="{ column, record }">
        <div v-if="column.key === 'name'" class="catalog-tool">
          <strong>{{ record.name }}</strong>
          <span>{{ record.serverName }}</span>
        </div>
        <span
          v-else-if="column.key === 'description'"
          class="catalog-description"
          :title="record.description || undefined"
          >{{ record.description || '—' }}</span
        >
        <AButton
          v-else-if="column.key === 'parameters'"
          class="catalog-parameters"
          type="link"
          size="small"
          @click="selected = record"
        >
          查看参数 <RightOutlined aria-hidden="true" />
        </AButton>
      </template>
    </ResourceTable>
    <AModal
      :open="selected !== null"
      :title="selected?.name"
      :footer="null"
      @cancel="selected = null"
    >
      <p v-if="selected" class="catalog-tool-meta">
        {{ selected.serverName }} · {{ selected.readOnly ? '已声明只读' : '未声明只读' }}
      </p>
      <pre class="schema-preview">{{ JSON.stringify(selected?.inputSchema, null, 2) }}</pre>
    </AModal>
  </McpPage>
</template>
<style scoped>
.catalog-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--workspace-space-3);
  margin-bottom: var(--workspace-space-3);
}
.catalog-search {
  width: 292px;
  min-width: 0;
}
.catalog-count {
  flex-shrink: 0;
  color: var(--workspace-text-secondary);
  font-size: var(--workspace-font-size-helper);
}
.catalog-table :deep(.ant-table-content > table) {
  table-layout: fixed;
}
.catalog-table :deep(.ant-table-thead > tr > th) {
  padding: 6px 16px;
  border-bottom-color: var(--workspace-border);
  border-radius: 0;
  background: var(--workspace-surface);
  color: var(--workspace-text-secondary);
}
.catalog-table :deep(.ant-table-thead > tr > th::before) {
  display: none;
}
.catalog-table :deep(.ant-table-tbody > tr > td) {
  padding: 12px 16px;
  border-bottom-color: var(--workspace-border);
}
.catalog-tool {
  display: flex;
  flex-direction: column;
  gap: 3px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.catalog-tool strong {
  font-weight: var(--workspace-font-weight-semibold);
}
.catalog-tool span,
.catalog-tool-meta {
  color: var(--workspace-text-secondary);
  font-size: var(--workspace-font-size-caption);
}
.catalog-description {
  display: -webkit-box;
  overflow: hidden;
  color: var(--workspace-text-secondary);
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.catalog-parameters {
  padding-inline: 0;
}
.catalog-parameters :deep(.anticon) {
  font-size: 11px;
}
.catalog-tool-meta {
  margin: 0 0 12px;
}
.schema-preview {
  max-height: 55vh;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 620px) {
  .catalog-table :deep(.ant-table-thead > tr > th),
  .catalog-table :deep(.ant-table-tbody > tr > td) {
    padding-inline: 8px;
  }
  .catalog-parameters :deep(.anticon) {
    display: none;
  }
}
</style>
