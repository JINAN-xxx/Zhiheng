<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Input as AInput,
  Modal as AModal
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { SearchOutlined } from '@ant-design/icons-vue'
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
  { key: 'name', title: '工具', dataIndex: 'name', width: 200 },
  { key: 'server', title: '服务', dataIndex: 'serverName', width: 180 },
  { key: 'description', title: '用途', dataIndex: 'description', ellipsis: true },
  { key: 'parameters', title: '参数', width: 92 }
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
    <div class="catalog-search">
      <AInput
        v-model:value="filter"
        allow-clear
        placeholder="搜索工具或服务"
        aria-label="搜索 MCP 工具"
        ><template #prefix><SearchOutlined /></template
      ></AInput>
    </div>
    <ResourceTable
      :rows="filteredTools"
      :columns="columns"
      :row-key="(row: CatalogTool) => row.serverId + ':' + row.name"
      :loading="loading"
      :scroll-x="790"
      :empty-text="rows.length ? '暂无匹配工具' : canReview ? '暂无同步工具' : '暂无可用工具'"
    >
      <template #bodyCell="{ column, record }">
        <AButton
          v-if="column.key === 'parameters'"
          type="link"
          size="small"
          @click="selected = record"
        >
          查看
        </AButton>
      </template>
    </ResourceTable>
    <AModal
      :open="selected !== null"
      :title="selected?.name"
      :footer="null"
      @cancel="selected = null"
    >
      <pre class="schema-preview">{{ JSON.stringify(selected?.inputSchema, null, 2) }}</pre>
    </AModal>
  </McpPage>
</template>
<style scoped>
.catalog-search {
  max-width: 320px;
  margin: 12px 0;
}
.schema-preview {
  max-height: 55vh;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
