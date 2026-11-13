<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Button as AButton,
  Input as AInput,
  InputNumber as AInputNumber,
  Modal
} from 'ant-design-vue'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { errorMessage } from '../../../shared/http.ts'
import {
  listGovernedApplications,
  setApplicationPlatformState,
  setApplicationResourceQuota
} from '../api/applications.ts'
import type { GovernedApplication } from '../api/applications.ts'

const rows = ref<GovernedApplication[]>([]),
  total = ref(0),
  page = ref(1)
const loading = ref(false),
  saving = ref(false),
  error = ref(''),
  formError = ref('')
const columns = [
  { title: '应用', key: 'application' },
  { title: '所属用户', key: 'owner' },
  { title: '平台状态', key: 'status' },
  { title: '文件容量', key: 'capacity' },
  { title: '操作', key: 'actions' }
]
const ownerId = ref(''),
  selected = ref<GovernedApplication | null>(null)
const count = ref<number | string | null>(1000),
  space = ref<number | string | null>(1024)
let controller: AbortController | null = null
let mutation: AbortController | null = null
async function load(next = page.value): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  error.value = ''
  try {
    const result = await listGovernedApplications(next, current.signal, ownerId.value.trim())
    if (current.signal.aborted) return
    rows.value = result.items
    total.value = result.total
    page.value = next
  } catch (failure) {
    if (!current.signal.aborted) error.value = errorMessage(failure)
  } finally {
    if (!current.signal.aborted) loading.value = false
  }
}
function quota(row: GovernedApplication): void {
  selected.value = row
  count.value = row.sourceFileLimit
  space.value = row.sourceByteLimit / 1048576
  formError.value = ''
}
async function saveQuota(): Promise<void> {
  const row = selected.value
  if (!row || saving.value) return
  if (
    typeof count.value !== 'number' ||
    !Number.isSafeInteger(count.value) ||
    count.value < 1 ||
    typeof space.value !== 'number' ||
    !Number.isSafeInteger(space.value * 1048576) ||
    space.value <= 0
  ) {
    formError.value = '请输入有效的文件数量和空间上限'
    return
  }
  saving.value = true
  mutation = new AbortController()
  try {
    await setApplicationResourceQuota(row, count.value, space.value * 1048576, mutation.signal)
    selected.value = null
    await load()
  } catch (failure) {
    if (!mutation.signal.aborted) formError.value = errorMessage(failure)
  } finally {
    saving.value = false
  }
}
function toggle(row: GovernedApplication): void {
  Modal.confirm({
    title: row.platformEnabled ? '停用此应用的平台调用？' : '恢复此应用的平台调用？',
    content: '平台状态与用户自己的启停独立，已有调用及账本保留。',
    okText: '确认',
    cancelText: '取消',
    async onOk() {
      if (saving.value) return
      saving.value = true
      mutation = new AbortController()
      try {
        await setApplicationPlatformState(row, !row.platformEnabled, mutation.signal)
        await load()
      } catch (failure) {
        if (!mutation.signal.aborted) error.value = errorMessage(failure)
      } finally {
        saving.value = false
      }
    }
  })
}
onMounted(() => void load())
onBeforeUnmount(() => {
  controller?.abort()
  mutation?.abort()
})
</script>
<template>
  <AdminPage
    title="应用治理"
    subtitle="管理平台启停与资源容量"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    hide-search
    @refresh="load()"
    @page="load($event)"
  >
    <template #filters
      ><AInput
        v-model:value="ownerId"
        placeholder="按用户标识筛选"
        aria-label="所属用户标识"
        allow-clear
        @press-enter="load(1)"
      />
      <AButton :loading="loading" @click="load(1)">查询</AButton></template
    >
    <ResourceTable :columns="columns" :rows="rows" :loading="loading" empty-text="暂无开发应用">
      <template #bodyCell="{ column, record: row }">
        <template v-if="column.key === 'application'"
          ><strong>{{ row.name }}</strong
          ><small class="identifier">{{ row.id }}</small></template
        >
        <span v-else-if="column.key === 'owner'" class="identifier">{{ row.ownerId }}</span>
        <template v-else-if="column.key === 'status'"
          >{{ row.platformEnabled ? '允许调用' : '平台停用'
          }}<small v-if="!row.ownerEnabled">用户已停用</small></template
        >
        <template v-else-if="column.key === 'capacity'"
          >{{ row.sourceCount }} / {{ row.sourceFileLimit }} 个<small
            >{{ (row.sourceBytes / 1048576).toFixed(1) }} /
            {{ (row.sourceByteLimit / 1048576).toFixed(1) }} MB</small
          ></template
        >
        <template v-else-if="column.key === 'actions'"
          ><AButton type="link" :disabled="saving" @click="quota(row)">调整额度</AButton>
          <AButton
            type="link"
            :danger="row.platformEnabled"
            :disabled="saving"
            @click="toggle(row)"
            >{{ row.platformEnabled ? '平台停用' : '平台启用' }}</AButton
          ></template
        >
      </template>
    </ResourceTable>
  </AdminPage>
  <AdminDialog
    :open="!!selected"
    title="调整资源额度"
    :saving="saving"
    :error="formError"
    @close="!saving && (selected = null)"
    @save="saveQuota"
  >
    <label class="quota-field"
      >文件上限<AInputNumber
        :value="count ?? ''"
        :min="1"
        :precision="0"
        @update:value="count = $event"
    /></label>
    <label class="quota-field"
      >空间上限（MB）<AInputNumber :value="space ?? ''" :min="0.001" @update:value="space = $event"
    /></label>
  </AdminDialog>
</template>
<style scoped>
small {
  display: block;
  color: var(--color-text-secondary);
  font-size: 12px;
  margin-top: 4px;
}
.identifier {
  overflow-wrap: anywhere;
  font-size: 12px;
}
.quota-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 16px 0;
}
.quota-field :deep(.ant-input-number) {
  width: 100%;
}
</style>
