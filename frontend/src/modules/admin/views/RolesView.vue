<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { Button as AButton, Modal, Tag as ATag } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { decodeRole, getPermissions } from '../api/index.ts'
import type { Permission, Role, RolePayload } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { activeLabel } from '../presentation.ts'
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<Role, RolePayload>('roles', decodeRole)
const writable = computed(() => hasAdminPermission(identity.value, 'roles', 'write'))
const canManage = (role: Role): boolean =>
  Boolean(
    identity.value?.is_admin ||
      role.permissions.every((code) => identity.value?.permissions?.includes(code))
  )
const open = ref(false),
  editing = ref<Role | null>(null),
  formError = ref(''),
  permissions = ref<Permission[]>([]),
  permissionError = ref('')
const controller = new AbortController()
const form = reactive({
  code: '',
  name: '',
  permissions: [] as string[],
  is_active: true,
  remark: ''
})
const groups = computed(() =>
  [...new Set(permissions.value.map((item) => item.group))].map((group) => ({
    name: group,
    items: permissions.value.filter((item) => item.group === group)
  }))
)
async function loadPermissions(): Promise<void> {
  permissionError.value = ''
  try {
    permissions.value = await getPermissions(controller.signal)
  } catch (failure) {
    if (!controller.signal.aborted) permissionError.value = adminError(failure)
  }
}
function refresh(): void {
  void load()
  void loadPermissions()
}
onMounted(loadPermissions)
onBeforeUnmount(() => controller.abort())
function edit(role: Role | null = null): void {
  if (role && !canManage(role)) return
  editing.value = role
  Object.assign(
    form,
    role
      ? {
          code: role.code,
          name: role.name,
          permissions: [...role.permissions],
          is_active: role.is_active,
          remark: role.remark
        }
      : { code: '', name: '', permissions: [], is_active: true, remark: '' }
  )
  formError.value = ''
  open.value = true
}
async function submit(): Promise<void> {
  if (saving.value) return
  if (!form.code.trim() || !form.name.trim()) {
    formError.value = '请填写角色编码和名称'
    return
  }
  if (permissionError.value) {
    formError.value = '权限目录加载失败，请重试后保存'
    return
  }
  const body = {
    name: form.name.trim(),
    permissions: [...form.permissions],
    is_active: form.is_active,
    remark: form.remark,
    ...(!editing.value ? { code: form.code.trim() } : {})
  }
  try {
    if (await save(editing.value, body)) open.value = false
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(role: Role): void {
  if (saving.value || !canManage(role)) return
  Modal.confirm({
    title: `删除角色“${role.name}”？`,
    content: '已分配给用户的角色无法删除，请先调整用户授权。',
    okText: '删除角色',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => remove(role)
  })
}
const columns = [
  { title: '角色', key: 'role', width: 220 },
  { title: '权限', key: 'permissions', width: 300 },
  { title: '状态', key: 'status', width: 100 },
  { title: '备注', dataIndex: 'remark', key: 'remark', width: 180 },
  { title: '操作', key: 'actions', width: 120 }
]
</script>
<template>
  <AdminPage
    title="角色权限"
    subtitle="角色授权直接控制后台接口访问"
    :loading="loading"
    :error="error || permissionError"
    :total="total"
    :page="page"
    :search="search"
    :can-create="writable"
    create-label="新建角色"
    @refresh="refresh"
    @create="edit()"
    @search="find"
    @page="changePage"
  >
    <ResourceTable :columns="columns" :rows="rows" :loading="loading" empty-text="暂无角色"
      ><template #bodyCell="{ column, record }">
        <div v-if="column.key === 'role'" class="admin-primary">
          <strong>{{ record.name }}</strong
          ><small class="admin-code">{{ record.code }}</small>
        </div>
        <span v-else-if="column.key === 'permissions'">{{
          record.permissions
            .map((code) => permissions.find((item) => item.code === code)?.label || code)
            .join('、') || '暂无授权'
        }}</span>
        <ATag v-else-if="column.key === 'status'" :color="record.is_active ? 'green' : 'default'">{{
          activeLabel(record.is_active)
        }}</ATag>
        <div v-else-if="column.key === 'actions' && writable" class="admin-actions">
          <AButton type="link" :disabled="!canManage(record)" @click="edit(record)">编辑</AButton
          ><AButton
            type="link"
            danger
            :disabled="saving || !canManage(record)"
            @click="confirmDelete(record)"
            >删除</AButton
          >
        </div>
      </template></ResourceTable
    >
    <AdminDialog
      :open="open"
      :title="editing ? '编辑角色' : '新建角色'"
      :saving="saving"
      :error="formError"
      :width="660"
      @close="open = false"
      @save="submit"
    >
      <div class="admin-form__grid">
        <label
          >角色编码<input
            v-model="form.code"
            aria-label="角色编码"
            :disabled="Boolean(editing)"
            maxlength="100" /></label
        ><label>角色名称<input v-model="form.name" aria-label="角色名称" maxlength="100" /></label>
      </div>
      <fieldset class="permission-list">
        <legend>接口权限</legend>
        <div v-for="group in groups" :key="group.name" class="permission-group">
          <strong>{{ group.name }}</strong
          ><label v-for="item in group.items" :key="item.code" class="admin-check"
            ><input
              v-model="form.permissions"
              type="checkbox"
              :value="item.code"
              :disabled="!identity?.is_admin && !identity?.permissions?.includes(item.code)"
            />{{ item.label }}</label
          >
        </div>
        <p v-if="!groups.length" class="admin-muted">{{ permissionError || '正在加载权限目录' }}</p>
      </fieldset>
      <label class="admin-check"><input v-model="form.is_active" type="checkbox" />启用角色</label
      ><label
        >备注<textarea v-model="form.remark" aria-label="备注" rows="3" maxlength="1000" />
      </label>
    </AdminDialog>
  </AdminPage>
</template>
<style scoped>
.permission-list {
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  max-height: 380px;
  overflow: auto;
}
.permission-list legend {
  width: auto;
  padding: 0 5px;
  margin: 0;
  font-size: 13px;
}
.permission-group {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 8px 0;
}
.permission-group strong {
  width: 100%;
  font-size: 13px;
  font-weight: 500;
}
</style>
