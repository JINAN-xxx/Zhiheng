<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Select as ASelect
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { CopyOutlined, PlusOutlined } from '@ant-design/icons-vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { copyText } from '../../../shared/clipboard.ts'
import { errorMessage, RequestScope } from '../../../shared/http.ts'
import { canUseCapability } from '../../identity/public.ts'
import type { Application } from '../api/index.ts'
import { listMcpPublications, listMcpSources, publishMcpTool, withdrawMcpTool } from '../api/mcp.ts'
import type { McpPublication, McpSource } from '../api/mcp.ts'

const props = defineProps<{ application: Application }>()
const { confirm, cancel: cancelConfirmation } = useConfirmation()
const readScope = new RequestScope()
const sourceScope = new RequestScope()
const writeScope = new RequestScope()
const rows = ref<McpPublication[]>([])
let loadedAt = 0
const sources = ref<McpSource[]>([])
const loading = ref(false)
const sourceLoading = ref(false)
const saving = ref(false)
const open = ref(false)
const error = ref('')
const formError = ref('')
const toolId = ref<string>()
const name = ref('')
const readOnly = ref(false)
const copied = ref(false)
const address = window.location.origin + '/v1/mcp'
const canPublish = computed(
  () => canUseCapability('mcp', 'publish') && canUseCapability('developer', 'grant')
)
const canCreate = computed(
  () =>
    canPublish.value &&
    props.application.status === 'active' &&
    props.application.platformEnabled !== false
)
const sourceId = (source: McpSource) => source.serverId + ':' + source.name
const sourceOptions = computed(() =>
  sources.value.map((source) => ({
    value: sourceId(source),
    label: source.serverName + ' · ' + source.name
  }))
)
const columns: TableColumnsType<McpPublication> = [
  { key: 'name', title: '发布名称', dataIndex: 'name' },
  { key: 'source', title: '上游工具' },
  { key: 'available', title: '状态', width: 118 },
  { key: 'action', title: '操作', width: 100, align: 'right' }
]

async function reload() {
  const ticket = readScope.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await listMcpPublications(props.application.applicationId, ticket.signal)
    if (ticket.isCurrent()) {
      rows.value = result
      loadedAt = Date.now()
    }
  } catch (failure) {
    if (ticket.isCurrent()) error.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}

async function prepare() {
  if (!canCreate.value) return
  const ticket = sourceScope.replace()
  open.value = true
  sourceLoading.value = true
  formError.value = ''
  toolId.value = undefined
  name.value = ''
  readOnly.value = false
  sources.value = []
  try {
    const result = await listMcpSources(ticket.signal)
    if (ticket.isCurrent()) sources.value = result
  } catch (failure) {
    if (ticket.isCurrent()) formError.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) sourceLoading.value = false
  }
}

function selectSource(value: unknown) {
  toolId.value = typeof value === 'string' ? value : undefined
  name.value = sources.value.find((source) => sourceId(source) === toolId.value)?.name ?? ''
  readOnly.value = false
}

async function save() {
  const tool = sources.value.find((source) => sourceId(source) === toolId.value)
  if (!canCreate.value || !tool || !readOnly.value || saving.value) return
  if (!/^[a-zA-Z0-9_.-]{1,120}$/.test(name.value.trim())) {
    formError.value = '发布名称需为 1–120 个字母、数字、点、下划线或连字符'
    return
  }
  const ticket = writeScope.replace()
  saving.value = true
  formError.value = ''
  try {
    await publishMcpTool(props.application.applicationId, tool, name.value.trim(), ticket.signal)
    if (ticket.isCurrent()) {
      open.value = false
      await reload()
    }
  } catch (failure) {
    if (ticket.isCurrent()) formError.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}

async function withdraw(row: McpPublication) {
  if (!canPublish.value || saving.value || !(await confirm('撤回工具「' + row.name + '」？')))
    return
  if (!canPublish.value || saving.value) return
  const ticket = writeScope.replace()
  saving.value = true
  error.value = ''
  try {
    await withdrawMcpTool(props.application.applicationId, row.id, ticket.signal)
    if (ticket.isCurrent()) await reload()
  } catch (failure) {
    if (ticket.isCurrent()) error.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}

async function copyAddress() {
  try {
    await copyText(address)
    copied.value = true
  } catch (failure) {
    error.value = errorMessage(failure)
  }
}

onActivated(() => {
  if (!loading.value && !saving.value && Date.now() - loadedAt >= 30_000) void reload()
})
onDeactivated(() => {
  cancelConfirmation()
  open.value = false
  sourceScope.replace()
})
onBeforeUnmount(() => {
  readScope.dispose()
  sourceScope.dispose()
  writeScope.dispose()
})
</script>

<template>
  <section class="mcp-publications" aria-label="MCP 发布">
    <div class="publication-toolbar">
      <div class="publication-address">
        <span>MCP 地址</span>
        <code>{{ address }}</code>
        <AButton
          type="text"
          :aria-label="copied ? '已复制 MCP 地址' : '复制 MCP 地址'"
          title="复制 MCP 地址"
          @click="copyAddress"
        >
          <CopyOutlined />
        </AButton>
      </div>
      <div class="publication-actions">
        <RefreshButton label="刷新发布列表" :loading="loading" @click="reload" />
        <AButton v-if="canCreate" type="primary" :disabled="saving" @click="prepare">
          <PlusOutlined aria-hidden="true" />发布工具
        </AButton>
      </div>
    </div>
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <ResourceTable
      :columns="columns"
      :rows="rows"
      :loading="loading"
      scroll-x="560"
      empty-text="暂无已发布工具"
    >
      <template #bodyCell="{ column, record }">
        <span v-if="column.key === 'source'">{{ record.serverName }} · {{ record.toolName }}</span>
        <span v-else-if="column.key === 'available'">{{
          record.available ? '已发布' : '待重新发布'
        }}</span>
        <AButton
          v-else-if="column.key === 'action' && canPublish"
          type="link"
          danger
          :disabled="saving"
          @click="withdraw(record)"
          >撤回</AButton
        >
      </template>
    </ResourceTable>
    <AModal
      :open="open && canCreate"
      title="发布 MCP 工具"
      ok-text="发布"
      cancel-text="取消"
      :confirm-loading="saving"
      :mask-closable="!saving"
      :ok-button-props="{ disabled: !toolId || !name.trim() || !readOnly }"
      @ok="save"
      @cancel="open = false"
    >
      <AAlert v-if="formError" :message="formError" type="error" show-icon />
      <AForm layout="vertical">
        <AFormItem label="上游工具" required>
          <ASelect
            :value="toolId"
            aria-label="上游工具"
            :options="sourceOptions"
            :loading="sourceLoading"
            show-search
            option-filter-prop="label"
            placeholder="选择已同步的工具"
            @change="selectSource"
          />
        </AFormItem>
        <AFormItem label="发布名称" required
          ><AInput v-model:value="name" aria-label="发布名称" :maxlength="120"
        /></AFormItem>
        <ACheckbox v-model:checked="readOnly">我已确认此工具仅执行只读操作</ACheckbox>
      </AForm>
    </AModal>
  </section>
</template>

<style scoped>
.mcp-publications {
  padding: 16px 22px;
  min-width: 0;
}
.publication-toolbar,
.publication-address,
.publication-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}
.publication-toolbar {
  justify-content: space-between;
  margin-bottom: 12px;
}
.publication-address code {
  overflow-wrap: anywhere;
}
.mcp-publications :deep(.ant-table-cell) {
  overflow-wrap: anywhere;
}
</style>
