<!-- Adapted from original FileAclDrawer, MIT licensed. -->
<script setup lang="ts">
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  DatePicker as ADatePicker,
  RadioGroup as ARadioGroup,
  Select as ASelect,
  Tag as ATag,
  Tooltip as ATooltip
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { findFileUsers, getFileAcl, saveFileAcl } from '../api/index.ts'
import type { FileAcl, FileAclEntry, FileUser, ManagedFile } from '../api/index.ts'
import { isoDateTime, localDateTime } from '../presentation.ts'
const props = defineProps<{
  users: FileUser[]
  file?: ManagedFile | null
  userLookup?: (search: string, signal: AbortSignal) => Promise<FileUser[]>
}>()
const emit = defineEmits<{ refresh: []; close: [] }>()
interface Entry {
  key: string
  userId: string | undefined
  effect: 'allow' | 'deny'
  expiresAt: string
  users: FileUser[]
  loading: boolean
}
const visible = ref(false),
  loading = ref(false),
  saving = ref(false),
  error = ref(''),
  uncertain = ref(false)
const acls = ref<FileAcl[]>([]),
  entries = ref<Entry[]>([]),
  access = ref<'private' | 'public'>('private')
const scope = new RequestScope(),
  writeScope = new RequestScope(),
  searches = new Map<string, RequestScope>()
const { confirm } = useConfirmation()
const batch = computed(() => acls.value.length > 1)
const title = computed(() =>
  batch.value
    ? `批量授权 - ${acls.value.length}个文件`
    : `访问权限 - ${acls.value[0]?.file.displayName ?? ''}`
)
const builtins = computed(() => acls.value[0]?.builtinPermissions ?? [])
const builtinColumns: TableColumnsType<FileAcl['builtinPermissions'][number]> = [
  { title: '权限来源', key: 'source', width: 180 },
  { title: '规则', key: 'enabled', width: 140 },
  { title: '拒绝覆盖', key: 'deny', width: 140 }
]
const entryColumns: TableColumnsType<Entry> = [
  { title: '授权用户', key: 'user', width: 230 },
  { title: '规则', key: 'effect', width: 140 },
  { title: '有效期至', key: 'expiresAt', width: 210 },
  { title: '操作', key: 'actions', width: 70 }
]
const effectOptions = [
  { label: '允许下载', value: 'allow' },
  { label: '拒绝下载', value: 'deny' }
]
const builtinLabels = { admin: '平台管理员', owner: '文件所有者', uploader: '文件上传人' }
function reset(): void {
  scope.replace()
  writeScope.replace()
  for (const search of searches.values()) search.dispose()
  searches.clear()
  visible.value = false
  loading.value = false
  saving.value = false
  entries.value = []
  acls.value = []
}
function close(): void {
  reset()
  emit('close')
}
async function open(files: ManagedFile[]): Promise<void> {
  reset()
  if (!files.length) return
  const context = scope.replace()
  visible.value = true
  loading.value = true
  error.value = ''
  uncertain.value = false
  try {
    const loaded = await Promise.all(files.map((file) => getFileAcl(file.fileId, context.signal)))
    if (!context.isCurrent() || !visible.value) return
    acls.value = loaded
    access.value = loaded[0]!.file.accessType
    entries.value =
      loaded.length === 1
        ? loaded[0]!.entries.map((entry) => ({
            key: createRequestId(),
            userId: entry.user_id,
            effect: entry.effect,
            expiresAt: localDateTime(entry.expires_at) ?? '',
            users: props.users,
            loading: false
          }))
        : []
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function addEntry(): void {
  entries.value.push({
    key: createRequestId(),
    userId: undefined,
    effect: 'allow',
    expiresAt: '',
    users: props.users,
    loading: false
  })
}
function removeEntry(key: string): void {
  searches.get(key)?.dispose()
  searches.delete(key)
  entries.value = entries.value.filter((row) => row.key !== key)
}
function setUser(row: Entry, value: unknown): void {
  row.userId = typeof value === 'string' ? value : undefined
}
function options(row: Entry) {
  const users = new Map([...props.users, ...row.users].map((user) => [user.id, user.username]))
  if (row.userId && !users.has(row.userId)) users.set(row.userId, row.userId)
  return [...users].map(([value, label]) => ({ value, label }))
}
async function searchUsers(row: Entry, keyword: string): Promise<void> {
  let search = searches.get(row.key)
  if (!search) {
    search = new RequestScope()
    searches.set(row.key, search)
  }
  const context = search.replace()
  row.loading = true
  try {
    const users = await (props.userLookup ?? findFileUsers)(keyword, context.signal)
    if (context.isCurrent() && visible.value && entries.value.some((item) => item.key === row.key))
      row.users = users
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) row.loading = false
  }
}
async function submit(): Promise<void> {
  if (loading.value || saving.value || uncertain.value || !acls.value.length) return
  if (
    entries.value.some((row) => !row.userId) ||
    new Set(entries.value.map((row) => row.userId)).size !== entries.value.length
  ) {
    error.value = '请选择完整且不重复的授权用户'
    return
  }
  let body: FileAclEntry[]
  try {
    body = entries.value.map((row) => ({
      user_id: row.userId!,
      effect: row.effect,
      expires_at: row.expiresAt ? isoDateTime(row.expiresAt) : null
    }))
  } catch {
    error.value = '有效期格式无效'
    return
  }
  const targets = [...acls.value],
    currentAccess = access.value,
    context = writeScope.replace()
  saving.value = true
  error.value = ''
  let changed = 0
  try {
    if (
      targets.length > 1 &&
      !(await confirm(`将覆盖所选${targets.length}个文件的显式授权，是否继续？`))
    )
      return
    if (!context.isCurrent() || !visible.value) return
    for (const acl of targets) {
      await saveFileAcl(
        acl.file,
        targets.length > 1 ? acl.file.accessType : currentAccess,
        body,
        context.signal
      )
      if (!context.isCurrent()) return
      changed += 1
    }
    emit('refresh')
    close()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      uncertain.value = true
      error.value = `已保存${changed}项。${errorMessage(reason)}；请关闭后重新打开核对权限。`
      emit('refresh')
    }
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
watch(
  () => props.file,
  (file) => {
    if (file) void open([file])
    else if (file === null) reset()
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  scope.dispose()
  writeScope.dispose()
  for (const search of searches.values()) search.dispose()
})
defineExpose({ open, close })
</script>
<template>
  <ResourceDrawer :open="visible" :title="title" width="70%" @close="close">
    <a-alert v-if="error" :message="error" type="error" show-icon class="mb8" />
    <a-alert
      :message="
        batch
          ? '批量操作仅覆盖所选文件的显式授权；内置权限仍按各文件规则生效。'
          : '所有者不可被拒绝；显式拒绝可覆盖公开、显式允许和上传人下载权限。'
      "
      type="info"
      show-icon
      class="mb8"
    />
    <div v-if="!batch" class="permission-section">
      <div class="permission-section__title">访问类型</div>
      <a-radio-group
        v-model:value="access"
        :disabled="saving || loading"
        :options="[
          { label: '受保护', value: 'private' },
          { label: '平台内公开', value: 'public' }
        ]"
      />
      <div class="permission-section__title">内置权限</div>
      <ResourceTable :columns="builtinColumns" :rows="builtins" :loading="loading" row-key="source">
        <template #bodyCell="{ column, record: row }"
          ><a-tag v-if="column.key === 'source'">{{ builtinLabels[row.source] }}</a-tag
          ><a-tag v-else-if="column.key === 'enabled'" :color="row.enabled ? 'green' : 'default'">{{
            row.enabled ? '允许下载' : '已移除'
          }}</a-tag
          ><a-tag
            v-else-if="column.key === 'deny' && row.enabled"
            :color="row.denyOverridable ? 'orange' : 'default'"
            >{{ row.denyOverridable ? '可以覆盖' : '不可覆盖' }}</a-tag
          ></template
        >
      </ResourceTable>
    </div>
    <div class="permission-section__header">
      <span class="permission-section__title">显式授权</span
      ><a-button
        type="primary"
        :disabled="loading || saving || uncertain"
        aria-label="添加授权"
        @click="addEntry"
        ><PlusOutlined />添加授权</a-button
      >
    </div>
    <ResourceTable :columns="entryColumns" :rows="entries" :loading="loading" row-key="key">
      <template #bodyCell="{ column, record: row }">
        <a-select
          v-if="column.key === 'user'"
          :value="row.userId"
          :disabled="saving"
          show-search
          allow-clear
          :filter-option="false"
          :loading="row.loading"
          :options="options(row)"
          placeholder="输入用户名称搜索"
          style="width: 100%"
          @update:value="(value) => setUser(row, value)"
          @search="(keyword) => searchUsers(row, keyword)"
        />
        <a-select
          v-else-if="column.key === 'effect'"
          :value="row.effect"
          :disabled="saving"
          :options="effectOptions"
          @update:value="
            (value) => {
              if (value === 'allow' || value === 'deny') row.effect = value
            }
          "
        />
        <a-date-picker
          v-else-if="column.key === 'expiresAt'"
          v-bind="row.expiresAt ? { value: row.expiresAt } : {}"
          :disabled="saving"
          value-format="YYYY-MM-DD HH:mm:ss"
          show-time
          placeholder="永久有效"
          @update:value="(value) => (row.expiresAt = typeof value === 'string' ? value : '')"
        />
        <a-tooltip v-else-if="column.key === 'actions'" title="删除"
          ><a-button
            type="link"
            danger
            :disabled="saving"
            aria-label="删除授权"
            @click="removeEntry(row.key)"
            ><DeleteOutlined /></a-button
        ></a-tooltip>
      </template>
    </ResourceTable>
    <template #footer
      ><a-button @click="close">取消</a-button
      ><a-button
        type="primary"
        :loading="saving"
        :disabled="loading || uncertain || !acls.length"
        @click="submit"
        >保存</a-button
      ></template
    >
  </ResourceDrawer>
</template>
<style scoped>
.permission-section {
  margin-bottom: 20px;
}
.permission-section__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 18px 0 8px;
}
.permission-section__title {
  color: var(--workspace-text);
  font-size: 16px;
  font-weight: 500;
}
</style>
