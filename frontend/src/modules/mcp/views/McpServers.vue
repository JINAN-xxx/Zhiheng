<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Select as ASelect,
  Space as ASpace,
  Tag as ATag
} from 'ant-design-vue'
import { PlusOutlined } from '@ant-design/icons-vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import { errorMessage, RequestScope } from '../../../shared/http.ts'
import { canUseCapability } from '../../identity/public.ts'
import {
  createMcpServer,
  grantMcpTool,
  listMcpCalls,
  listMcpRoles,
  listMcpServers,
  syncMcpServer,
  updateMcpServer
} from '../api/index.ts'
import type { ManagedTool, McpServer } from '../api/index.ts'
import McpPage from '../components/McpPage.vue'

const readScope = new RequestScope()
const actionScope = new RequestScope()
const callsScope = new RequestScope()
const rows = ref<McpServer[]>([])
const roles = ref<{ value: string; label: string }[]>([])
const calls = ref<Awaited<ReturnType<typeof listMcpCalls>>>([])
const selected = ref<McpServer | null>(null)
const editing = ref<McpServer | null>(null)
const granting = ref<ManagedTool | null>(null)
const loading = ref(false)
const busy = ref(false)
const callsLoading = ref(false)
const editOpen = ref(false)
const error = ref('')
const notice = ref('')
const formError = ref('')
const detailError = ref('')
const form = reactive({ name: '', endpoint: '', token: '', clearToken: false })
const grant = reactive({ enabled: false, roleIds: [] as string[] })
const canConfigure = computed(() => canUseCapability('mcp', 'configure'))
const canGrant = computed(() => canUseCapability('mcp', 'grant'))
const canAudit = computed(() => canUseCapability('mcp', 'audit'))
const canViewTools = computed(() => canGrant.value || canConfigure.value)
const columns = computed(() => [
  { key: 'name', title: '服务', dataIndex: 'name', width: 160 },
  ...(canConfigure.value
    ? [{ key: 'endpoint', title: '服务地址', dataIndex: 'endpoint', ellipsis: true }]
    : []),
  { key: 'state', title: '状态', width: 100 },
  { key: 'synced', title: '最近同步', width: 190 },
  { key: 'actions', title: '操作', width: canConfigure.value ? 245 : 90 }
])
const toolColumns = [
  { key: 'name', title: '工具', dataIndex: 'name', width: 170 },
  { key: 'definition', title: '用途', dataIndex: 'description', ellipsis: true },
  { key: 'grants', title: '授权', width: 120 }
]
const callColumns = [
  { key: 'name', title: '工具', dataIndex: 'name', width: 170 },
  { key: 'status', title: '结果', width: 185 },
  { key: 'duration', title: '耗时', width: 95 },
  { key: 'time', title: '时间', width: 175 }
]
const formatDate = (value: string | null) => (value ? new Date(value).toLocaleString() : '—')

async function reload() {
  const ticket = readScope.replace()
  loading.value = true
  error.value = ''
  try {
    const [servers, roleOptions] = await Promise.all([
      listMcpServers(ticket.signal),
      canGrant.value ? listMcpRoles(ticket.signal) : Promise.resolve([])
    ])
    if (!ticket.isCurrent()) return
    rows.value = servers
    roles.value = roleOptions.map((role) => ({ value: role.id, label: role.name }))
    if (selected.value)
      selected.value = servers.find((row) => row.id === selected.value?.id) ?? null
  } catch (failure) {
    if (ticket.isCurrent()) error.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}

function upsert(server: McpServer) {
  const index = rows.value.findIndex((row) => row.id === server.id)
  if (index < 0) rows.value.push(server)
  else rows.value.splice(index, 1, server)
  if (selected.value?.id === server.id) selected.value = server
}

async function perform(action: (signal: AbortSignal) => Promise<McpServer>, destination = error) {
  if (busy.value || loading.value) return false
  const ticket = actionScope.replace()
  busy.value = true
  notice.value = ''
  destination.value = ''
  try {
    const result = await action(ticket.signal)
    if (!ticket.isCurrent()) return false
    upsert(result)
    return true
  } catch (failure) {
    if (ticket.isCurrent()) destination.value = errorMessage(failure)
    return false
  } finally {
    if (ticket.isCurrent()) busy.value = false
  }
}

function edit(server: McpServer | null) {
  if (!canConfigure.value) return
  editing.value = server
  Object.assign(form, {
    name: server?.name ?? '',
    endpoint: server?.endpoint ?? '',
    token: '',
    clearToken: false
  })
  formError.value = ''
  editOpen.value = true
}

async function save() {
  if (!canConfigure.value) return
  const current = editing.value
  const body = {
    name: form.name.trim(),
    endpoint: form.endpoint.trim(),
    ...(form.token ? { token: form.token.trim() } : {})
  }
  if (!body.name || !body.endpoint) {
    formError.value = '请填写服务名称和地址'
    return
  }
  const saved = await perform((signal) => {
    if (!current) return createMcpServer(body, signal)
    const changed = body.endpoint !== current.endpoint || !!body.token || form.clearToken
    return updateMcpServer(
      current.id,
      {
        ...body,
        expected_version: current.version,
        enabled: current.enabled && !changed,
        clear_token: form.clearToken
      },
      signal
    )
  }, formError)
  if (saved) closeEdit()
}

function closeEdit() {
  editOpen.value = false
  form.token = ''
}

async function sync(server: McpServer) {
  if (!canConfigure.value) return
  if (await perform((signal) => syncMcpServer(server.id, server.version, signal))) {
    notice.value = '连接成功，工具清单已更新'
  }
}

async function toggle(server: McpServer) {
  const endpoint = server.endpoint
  if (!canConfigure.value || endpoint === null) return
  await perform((signal) =>
    updateMcpServer(
      server.id,
      {
        name: server.name,
        endpoint,
        enabled: !server.enabled,
        expected_version: server.version
      },
      signal
    )
  )
}

async function view(server: McpServer) {
  const ticket = callsScope.replace()
  selected.value = server
  calls.value = []
  detailError.value = ''
  if (!canAudit.value) return
  callsLoading.value = true
  try {
    const result = await listMcpCalls(server.id, ticket.signal)
    if (ticket.isCurrent()) calls.value = result
  } catch (failure) {
    if (ticket.isCurrent()) detailError.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) callsLoading.value = false
  }
}

function closeDetails() {
  selected.value = null
  callsScope.replace()
  callsLoading.value = false
}

function editGrant(tool: ManagedTool) {
  if (!canGrant.value) return
  granting.value = tool
  grant.enabled = tool.enabled
  const options = new Set(roles.value.map((role) => role.value))
  grant.roleIds = tool.roleIds.filter((id) => options.has(id))
  formError.value = ''
}

async function saveGrant() {
  const server = selected.value
  const tool = granting.value
  if (!canGrant.value || !server || !tool) return
  if (
    await perform(
      (signal) =>
        grantMcpTool(server.id, tool.name, server.version, grant.enabled, grant.roleIds, signal),
      formError
    )
  )
    granting.value = null
}

onMounted(() => void reload())
onBeforeUnmount(() => {
  readScope.dispose()
  actionScope.dispose()
  callsScope.dispose()
})
</script>

<template>
  <McpPage active="servers">
    <template #actions>
      <RefreshButton label="刷新 MCP 服务" :disabled="busy" :loading="loading" @click="reload" />
      <AButton v-if="canConfigure" type="primary" :disabled="busy || loading" @click="edit(null)"
        ><PlusOutlined />添加服务</AButton
      >
    </template>
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <AAlert
      v-else-if="notice"
      :message="notice"
      type="success"
      show-icon
      closable
      @close="notice = ''"
    />
    <ResourceTable
      :rows="rows"
      :columns="columns"
      row-key="id"
      :loading="loading"
      :scroll-x="canConfigure ? 960 : 540"
      empty-text="尚未添加 MCP 服务"
    >
      <template #bodyCell="{ column, record }">
        <ATag v-if="column.key === 'state'" :color="record.enabled ? 'green' : 'default'">
          {{ record.enabled ? '已启用' : '已停用' }}
        </ATag>
        <span v-else-if="column.key === 'synced'">{{ formatDate(record.syncedAt) }}</span>
        <ASpace v-else-if="column.key === 'actions'" :size="0">
          <AButton type="link" size="small" :disabled="busy" @click="view(record)">详情</AButton>
          <AButton
            v-if="canConfigure"
            type="link"
            size="small"
            :disabled="busy"
            @click="edit(record)"
            >编辑</AButton
          >
          <AButton
            v-if="canConfigure"
            type="link"
            size="small"
            :disabled="busy"
            @click="sync(record)"
            >测试并同步</AButton
          >
          <AButton
            v-if="canConfigure"
            type="link"
            size="small"
            :disabled="busy || (!record.enabled && !record.syncedAt)"
            @click="toggle(record)"
          >
            {{ record.enabled ? '停用' : '启用' }}
          </AButton>
        </ASpace>
      </template>
    </ResourceTable>

    <AModal
      :open="canConfigure && editOpen"
      :title="editing ? '编辑 MCP 服务' : '添加 MCP 服务'"
      :confirm-loading="busy"
      :closable="!busy"
      :mask-closable="!busy"
      ok-text="保存"
      cancel-text="取消"
      @ok="save"
      @cancel="closeEdit"
    >
      <AAlert v-if="formError" :message="formError" type="error" show-icon />
      <AForm layout="vertical">
        <AFormItem label="服务名称"
          ><AInput v-model:value="form.name" :maxlength="100"
        /></AFormItem>
        <AFormItem label="Streamable HTTP 地址"
          ><AInput
            v-model:value="form.endpoint"
            :maxlength="512"
            placeholder="https://tools.example.com/mcp"
        /></AFormItem>
        <AFormItem label="Bearer Token">
          <AInput.Password
            v-model:value="form.token"
            autocomplete="off"
            :disabled="form.clearToken"
            :maxlength="4096"
            :placeholder="editing?.hasToken ? '已保存，留空保留' : '选填'"
          />
          <ACheckbox v-if="editing?.hasToken" v-model:checked="form.clearToken"
            >清除已保存凭据</ACheckbox
          >
        </AFormItem>
      </AForm>
    </AModal>

    <ResourceDrawer
      :open="selected !== null"
      :title="selected?.name ?? '服务详情'"
      :width="760"
      @close="closeDetails"
    >
      <h3 v-if="canViewTools">{{ canGrant ? '工具授权' : '工具清单' }}</h3>
      <ResourceTable
        v-if="canViewTools"
        :rows="selected?.tools ?? []"
        :columns="toolColumns"
        row-key="name"
        :scroll-x="590"
        empty-text="暂无同步工具"
      >
        <template #bodyCell="{ column, record }">
          <AButton
            v-if="column.key === 'grants' && record.readOnly && canGrant"
            type="link"
            size="small"
            :disabled="busy"
            @click="editGrant(record)"
          >
            {{ record.enabled ? '编辑授权' : '启用与授权' }}
          </AButton>
          <span v-else-if="column.key === 'grants'">
            {{ !record.readOnly ? '仅支持只读工具' : record.enabled ? '已启用' : '已停用' }}
          </span>
        </template>
      </ResourceTable>
      <h3 v-if="canAudit">最近调用</h3>
      <AAlert v-if="canAudit && detailError" :message="detailError" type="error" show-icon />
      <ResourceTable
        v-if="canAudit"
        :rows="calls"
        :columns="callColumns"
        row-key="id"
        :loading="callsLoading"
        :scroll-x="640"
        empty-text="暂无调用记录"
      >
        <template #bodyCell="{ column, record }">
          <span v-if="column.key === 'status'" :title="record.code ?? undefined">
            {{
              record.status === 'succeeded'
                ? '成功'
                : record.status === 'cancelled'
                  ? '已取消'
                  : '失败'
            }}
          </span>
          <span v-else-if="column.key === 'duration'">{{ record.duration }} ms</span>
          <span v-else-if="column.key === 'time'">{{ formatDate(record.time) }}</span>
        </template>
      </ResourceTable>
    </ResourceDrawer>

    <AModal
      :open="canGrant && granting !== null"
      :title="granting?.name"
      :confirm-loading="busy"
      :closable="!busy"
      :mask-closable="!busy"
      ok-text="保存授权"
      cancel-text="取消"
      @ok="saveGrant"
      @cancel="granting = null"
    >
      <AAlert v-if="formError" :message="formError" type="error" show-icon />
      <AForm layout="vertical">
        <AFormItem><ACheckbox v-model:checked="grant.enabled">启用此工具</ACheckbox></AFormItem>
        <AFormItem label="授权角色">
          <ASelect
            :value="grant.roleIds"
            :options="roles"
            mode="multiple"
            option-filter-prop="label"
            placeholder="请选择可使用此工具的角色"
            @update:value="
              (value) =>
                (grant.roleIds = Array.isArray(value)
                  ? value.filter((item) => typeof item === 'string')
                  : [])
            "
          />
        </AFormItem>
      </AForm>
    </AModal>
  </McpPage>
</template>
