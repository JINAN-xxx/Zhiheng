<!-- Adapted from original FileRetentionPolicyDrawer, MIT licensed. -->
<template>
  <ResourceDrawer title="文件保留策略" :open="visible" :width="900" @close="close">
    <a-alert v-if="error" :message="error" type="error" show-icon class="mb8" />
    <a-alert
      message="策略仅应用于后续新建或重建的业务引用；未配置或已停用的业务类型永久保留，既有引用不会被追溯修改。"
      type="info"
      :closable="false"
      show-icon
      class="mb8"
    />
    <div v-if="canWrite" class="mb8">
      <a-button type="primary" aria-label="新增策略" :disabled="saving" @click="handleAdd"
        ><PlusOutlined />
        新增
      </a-button>
    </div>
    <ResourceTable
      :columns="visibleColumns"
      :rows="policyList"
      :loading="loading"
      row-key="resourceType"
    >
      <template #bodyCell="{ column, record: row }">
        <template v-if="column.key === 'status'"
          ><a-tag :color="row.enabled ? 'green' : 'default'">{{
            row.enabled ? '启用' : '停用'
          }}</a-tag></template
        >
        <template v-else-if="column.key === 'remark'">{{ row.remark || '-' }}</template>
        <template v-else-if="column.key === 'updateTime'">{{
          formatTime(row.updatedAt) || '-'
        }}</template>
        <a-space v-else-if="column.key === 'actions' && canWrite"
          ><a-tooltip title="修改"
            ><a-button type="link" :disabled="saving" aria-label="修改策略" @click="handleEdit(row)"
              ><EditOutlined /></a-button></a-tooltip
          ><a-tooltip title="删除"
            ><a-button
              type="link"
              danger
              :disabled="saving"
              aria-label="删除策略"
              @click="handleDelete(row)"
              ><DeleteOutlined /></a-button></a-tooltip
        ></a-space>
      </template>
    </ResourceTable>
  </ResourceDrawer>

  <a-modal :title="formTitle" :open="formOpen && canWrite" :width="520" @cancel="closeForm">
    <a-alert v-if="formError" :message="formError" type="error" show-icon />
    <a-form :model="form" :disabled="saving" layout="vertical">
      <a-form-item label="业务类型" name="resourceType">
        <a-input
          v-model:value="form.resourceType"
          :disabled="editing !== null"
          :maxlength="32"
          placeholder="请输入业务类型"
        />
      </a-form-item>
      <a-form-item label="保留天数" name="retentionDays">
        <a-input-number
          :value="form.retentionDays"
          :min="1"
          :max="36500"
          :precision="0"
          style="width: 100%"
          @update:value="setDays"
        />
      </a-form-item>
      <a-form-item label="状态" name="enabled"
        ><a-radio-group v-model:value="form.enabled" :options="statusOptions"
      /></a-form-item>
      <a-form-item label="备注" name="remark">
        <a-textarea
          v-model:value="form.remark"
          :rows="3"
          :maxlength="1000"
          show-count
          placeholder="请输入备注"
        />
      </a-form-item>
    </a-form>
    <template #footer>
      <div class="dialog-footer">
        <a-button type="primary" :loading="saving" @click="submit"> 确 定 </a-button>
        <a-button @click="closeForm">取 消</a-button>
      </div>
    </template>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  Modal as AModal,
  RadioGroup as ARadioGroup,
  Space as ASpace,
  Tag as ATag,
  Textarea as ATextarea,
  Tooltip as ATooltip
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getRetentionPolicies, saveRetentionPolicy, deleteRetentionPolicy } from '../api/index.ts'
import type { RetentionPolicy } from '../api/index.ts'
import { formatTime } from '../presentation.ts'
const props = defineProps<{ canRead: boolean; canWrite: boolean }>()
const visible = ref(false),
  loading = ref(false),
  saving = ref(false),
  error = ref(''),
  formError = ref(''),
  formOpen = ref(false)
const policyList = ref<RetentionPolicy[]>([]),
  editing = ref<RetentionPolicy | null>(null)
const form = reactive({ resourceType: '', retentionDays: 30, enabled: true, remark: '' })
const scope = new RequestScope(),
  writes = new RequestScope()
const { confirm } = useConfirmation()
const formTitle = computed(() => (editing.value ? '修改文件保留策略' : '新增文件保留策略'))
const statusOptions = [
  { label: '启用', value: true },
  { label: '停用', value: false }
]
const columns: TableColumnsType<RetentionPolicy> = [
  { title: '业务类型', dataIndex: 'resourceType', key: 'resourceType' },
  { title: '保留天数', dataIndex: 'retentionDays', key: 'retentionDays', width: 100 },
  { title: '状态', key: 'status', width: 90 },
  { title: '备注', key: 'remark', ellipsis: true },
  { title: '更新时间', key: 'updateTime', width: 180 },
  { title: '操作', key: 'actions', width: 100 }
]
const visibleColumns = computed(() =>
  columns.filter((column) => column.key !== 'actions' || props.canWrite)
)
function closeForm(): void {
  writes.replace()
  formOpen.value = false
  saving.value = false
}
function close(): void {
  scope.replace()
  closeForm()
  visible.value = false
  loading.value = false
  policyList.value = []
}
function open(): void {
  if (!props.canRead) return
  close()
  error.value = ''
  visible.value = true
  void getList()
}
async function getList(): Promise<void> {
  if (!props.canRead) return
  const context = scope.replace()
  loading.value = true
  try {
    const rows = await getRetentionPolicies(context.signal)
    if (context.isCurrent() && visible.value) policyList.value = rows
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function handleAdd(): void {
  if (!props.canWrite) return
  editing.value = null
  Object.assign(form, { resourceType: '', retentionDays: 30, enabled: true, remark: '' })
  formError.value = ''
  formOpen.value = true
}
function handleEdit(row: RetentionPolicy): void {
  if (!props.canWrite) return
  editing.value = { ...row }
  Object.assign(form, row)
  formError.value = ''
  formOpen.value = true
}
function setDays(value: unknown): void {
  form.retentionDays = typeof value === 'number' ? value : 0
}
async function submit(): Promise<void> {
  if (saving.value || !props.canWrite) return
  if (
    !/^[a-z][a-z0-9_]{0,31}$/.test(form.resourceType) ||
    !Number.isInteger(form.retentionDays) ||
    form.retentionDays < 1 ||
    form.retentionDays > 36500 ||
    [...form.remark].length > 500
  ) {
    formError.value =
      '业务类型应为小写字母开头的32字以内标识，保留天数为1–36500整数，备注不超过500字'
    return
  }
  const context = writes.replace(),
    target = editing.value ? { ...editing.value } : null,
    body = {
      resource_type: form.resourceType,
      retention_days: form.retentionDays,
      enabled: form.enabled,
      remark: form.remark
    }
  saving.value = true
  formError.value = ''
  try {
    await saveRetentionPolicy(target, body, context.signal)
    if (!context.isCurrent()) return
    formOpen.value = false
    await getList()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      formError.value = `${errorMessage(reason)}；请关闭后刷新核对，避免重复新增。`
      formOpen.value = false
      error.value = formError.value
      await getList()
    }
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
async function handleDelete(row: RetentionPolicy): Promise<void> {
  if (saving.value || !props.canWrite) return
  const context = writes.replace(),
    target = { ...row }
  saving.value = true
  try {
    if (
      !(await confirm(`是否删除业务类型“${target.resourceType}”的保留策略？`)) ||
      !context.isCurrent() ||
      !visible.value ||
      !props.canWrite
    )
      return
    await deleteRetentionPolicy(target, context.signal)
    if (context.isCurrent()) await getList()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
onBeforeUnmount(() => {
  scope.dispose()
  writes.dispose()
})
defineExpose({ open })
</script>
