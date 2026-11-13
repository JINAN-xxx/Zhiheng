<script setup lang="ts">
import AdminRowActions from '../components/AdminRowActions.vue'
import AdminState from '../components/AdminState.vue'
import AdminToggle from '../components/AdminToggle.vue'
import { inject, computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Modal as AModal,
  Modal,
  Tag as ATag,
  Switch as ASwitch,
  Checkbox as ACheckbox,
  Input as AInput,
  Textarea as ATextarea
} from 'ant-design-vue'
import { adminReadCacheKey } from '../readCache.ts'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission, isActiveAdminPermission } from '../access.ts'
import { decodeRole, getPermissions } from '../api/index.ts'
import type { Permission, Role, RolePayload } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { activeLabel } from '../presentation.ts'
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<Role, RolePayload>('roles', decodeRole)
const canManage = (role: Role): boolean =>
  role.system_key === null &&
  Boolean(
    identity.value?.is_admin ||
      role.permissions.every((code) => identity.value?.permissions?.includes(code))
  )
const readCache = inject(adminReadCacheKey, null)
const savedPermissions = readCache?.getResource<Permission[]>('permission-catalog')
const open = ref(false),
  editing = ref<Role | null>(null),
  preview = ref<Role | null>(null),
  formError = ref(''),
  permissions = ref<Permission[]>(
    (savedPermissions?.value ?? []).filter((item) => isActiveAdminPermission(item.code))
  ),
  permissionError = ref(''),
  permissionsLoading = ref(false)
const controller = new AbortController()
const form = reactive({
  code: '',
  name: '',
  permissions: [] as string[],
  is_active: true,
  remark: ''
})
interface PermissionModule {
  id: string
  title: string
  description: string
  gate: Permission
  actions: Permission[]
}
const modules = computed<PermissionModule[]>(() =>
  permissions.value
    .filter((item) => item.kind === 'module' || item.code.endsWith(':enabled'))
    .map((gate) => ({
      id: gate.module ?? gate.code.slice(0, -8),
      title: gate.label,
      description: gate.description ?? '',
      gate,
      actions: permissions.value.filter(
        (item) =>
          item.parent === gate.code ||
          (item.kind !== 'module' &&
            item.code !== gate.code &&
            item.code.startsWith(gate.code.slice(0, -7)))
      )
    }))
)
const canGrant = computed(() => hasAdminPermission(identity.value, 'roles', 'grant'))
const selectedOperationCount = computed(
  () =>
    permissions.value.filter(
      (item) => item.kind === 'action' && form.permissions.includes(item.code)
    ).length
)
function enabled(module: PermissionModule): boolean {
  return form.permissions.includes(module.gate.code)
}
function selectedCount(module: PermissionModule): number {
  return module.actions.filter((item) => form.permissions.includes(item.code)).length
}
function grantable(item: Permission): boolean {
  return (
    canGrant.value &&
    !!identity.value &&
    (identity.value.is_admin || !!identity.value.permissions?.includes(item.code))
  )
}
function toggleModule(module: PermissionModule, value: unknown): void {
  if (!grantable(module.gate)) return
  form.permissions = form.permissions.filter((code) => code !== module.gate.code)
  if (value === true) form.permissions.push(module.gate.code)
}
function toggleAction(module: PermissionModule, item: Permission, value: boolean): void {
  if (!enabled(module) || !grantable(item)) return
  const read = module.actions.find((action) => action.action === 'read')
  if (value && read && !grantable(read)) return
  if (!value && item.action === 'read') {
    const codes = new Set(module.actions.map((action) => action.code))
    form.permissions = form.permissions.filter((code) => !codes.has(code))
    return
  }
  form.permissions = form.permissions.filter((code) => code !== item.code)
  if (value) {
    form.permissions.push(item.code)
    if (read && !form.permissions.includes(read.code)) form.permissions.push(read.code)
  }
}
function selectAll(module: PermissionModule, value: boolean): void {
  if (!enabled(module) || !canGrant.value) return
  const allowed = module.actions.filter(grantable)
  const codes = new Set(allowed.map((item) => item.code))
  form.permissions = form.permissions.filter((code) => !codes.has(code))
  if (value) form.permissions.push(...codes)
}
async function loadPermissions(): Promise<void> {
  permissionError.value = ''
  permissionsLoading.value = true
  const stamp = readCache?.stamp()
  try {
    permissions.value = await getPermissions(controller.signal)
    if (stamp) readCache?.setResource('permission-catalog', permissions.value, stamp)
  } catch (failure) {
    if (!controller.signal.aborted) permissionError.value = adminError(failure)
  } finally {
    if (!controller.signal.aborted) permissionsLoading.value = false
  }
}
function refresh(): void {
  void load()
  void loadPermissions()
}
onMounted(() => {
  void loadPermissions()
  void load()
})
onBeforeUnmount(() => controller.abort())
function edit(role: Role | null = null): void {
  if (
    (!role && !hasAdminPermission(identity.value, 'roles', 'create')) ||
    (role && (!canEditRole.value || !canManage(role)))
  )
    return
  editing.value = role
  Object.assign(
    form,
    role
      ? {
          code: role.code,
          name: role.name,
          permissions: role.permissions.filter(isActiveAdminPermission),
          is_active: role.is_active,
          remark: role.remark
        }
      : { code: '', name: '', permissions: [], is_active: true, remark: '' }
  )
  formError.value = ''
  void loadPermissions()
  open.value = true
}
async function submit(): Promise<void> {
  if (saving.value) return
  if (
    editing.value
      ? !canEditRole.value || !canManage(editing.value)
      : !hasAdminPermission(identity.value, 'roles', 'create')
  )
    return
  if (!form.name.trim()) {
    formError.value = '请填写角色名称'
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
    remark: form.remark
  }
  try {
    if (await save(editing.value, body)) open.value = false
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(role: Role): void {
  if (!hasAdminPermission(identity.value, 'roles', 'delete')) return
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
function permissionSummary(role: Role): string {
  if (role.system_key === 'super_admin') return '全部功能与管理权限'
  const codes = new Set(role.permissions)
  return (
    modules.value
      .filter(
        (module) =>
          codes.has(module.gate.code) || module.actions.some((item) => codes.has(item.code))
      )
      .map(
        (module) =>
          `${module.title}（${codes.has(module.gate.code) ? module.actions.filter((item) => codes.has(item.code)).length + '项' : '已关闭'}）`
      )
      .join('、') || '暂无授权'
  )
}
const canEditRole = computed(() =>
  ['update', 'toggle', 'grant'].some((action) =>
    hasAdminPermission(identity.value, 'roles', action)
  )
)
const columns = [
  { title: '角色', key: 'role', width: 220 },
  { title: '权限', key: 'permissions', width: 300 },
  { title: '成员数', dataIndex: 'member_count', key: 'members', width: 90 },
  { title: '状态', key: 'status', width: 100 },
  { title: '备注', dataIndex: 'remark', key: 'remark', width: 180 },
  { title: '操作', key: 'actions', width: 110 }
]
</script>
<template>
  <AdminPage
    search-placeholder="搜索角色名称"
    title="角色权限"
    subtitle="角色授权控制系统与 AI 平台功能访问"
    :loading="loading"
    :error="error || permissionError"
    :total="total"
    :page="page"
    :search="search"
    :can-create="hasAdminPermission(identity, 'roles', 'create')"
    create-label="新建角色"
    @refresh="refresh"
    @create="edit()"
    @search="find"
    @page="changePage"
  >
    <ResourceTable
      :loading-delay="150"
      :columns="columns"
      :rows="rows"
      :loading="loading"
      empty-text="暂无角色"
      ><template #bodyCell="{ column, record }">
        <div v-if="column.key === 'role'" class="admin-primary">
          <strong>{{ record.name }}</strong
          ><small class="admin-code">{{ record.code }}</small>
          <ATag v-if="record.system_key" class="role-builtin" color="purple">系统内置</ATag>
        </div>
        <span v-else-if="column.key === 'permissions'">{{ permissionSummary(record) }}</span>
        <AdminState v-else-if="column.key === 'status'" :value="record.is_active" />
        <AdminRowActions
          v-else-if="column.key === 'actions' && record.system_key"
          :primary="{ key: 'preview', label: '查看' }"
          @select="preview = record"
        />
        <AdminRowActions
          v-else-if="
            column.key === 'actions' &&
            (canEditRole || hasAdminPermission(identity, 'roles', 'delete'))
          "
          :primary="
            canEditRole ? { key: 'edit', label: '编辑', disabled: !canManage(record) } : null
          "
          :more="[
            {
              key: 'delete',
              label: '删除',
              danger: true,
              disabled: !canManage(record) || !hasAdminPermission(identity, 'roles', 'delete')
            }
          ]"
          :busy="saving"
          @select="(key) => (key === 'edit' ? edit(record) : confirmDelete(record))"
        /> </template
    ></ResourceTable>
    <AdminDialog
      :open="open"
      :title="editing ? '编辑角色' : '新建角色'"
      :saving="saving"
      :loading="permissionsLoading"
      :error="formError"
      :width="640"
      @close="open = false"
      @save="submit"
    >
      <div class="role-fields">
        <label v-if="editing"
          >角色编码<AInput
            v-model:value="form.code"
            aria-label="角色编码"
            :disabled="Boolean(editing)"
            :maxlength="80" /></label
        ><label
          >角色名称<AInput
            v-model:value="form.name"
            :disabled="!!editing && !hasAdminPermission(identity, 'roles', 'update')"
            aria-label="角色名称"
            :maxlength="100"
        /></label>
      </div>
      <AdminToggle
        v-model="form.is_active"
        :disabled="!!editing && !hasAdminPermission(identity, 'roles', 'toggle')"
        label="启用角色"
      /><label
        >备注<ATextarea
          v-model:value="form.remark"
          :disabled="!!editing && !hasAdminPermission(identity, 'roles', 'update')"
          aria-label="备注"
          :rows="2"
          :maxlength="1000"
        />
      </label>
      <fieldset class="permission-list">
        <legend>功能权限 · 已选 {{ selectedOperationCount }} 项操作</legend>
        <p class="admin-muted">
          开启模块后勾选操作。“查看”为基础权限；取消查看会取消本模块其他操作。关闭模块会保留勾选，但停止生效。
        </p>
        <div class="permission-modules">
          <section
            v-for="module in modules"
            :key="module.id"
            class="permission-module"
            :aria-label="module.title"
          >
            <header class="permission-module__header">
              <div>
                <h3>{{ module.title }}</h3>
                <p v-if="module.description">{{ module.description }}</p>
              </div>
              <ASwitch
                :checked="enabled(module)"
                :aria-label="'开启' + module.title"
                :disabled="!grantable(module.gate)"
                checked-children="开"
                un-checked-children="关"
                @change="(value) => toggleModule(module, value)"
              />
            </header>
            <div v-if="enabled(module)" class="permission-module__operations">
              <div class="permission-module__selection">
                <ACheckbox
                  :checked="
                    selectedCount(module) === module.actions.length && module.actions.length > 0
                  "
                  :indeterminate="
                    selectedCount(module) > 0 && selectedCount(module) < module.actions.length
                  "
                  :disabled="!canGrant"
                  :aria-label="'全选' + module.title"
                  @change="(event) => selectAll(module, !!event.target.checked)"
                  >全选</ACheckbox
                ><span>已选 {{ selectedCount(module) }}/{{ module.actions.length }}</span>
              </div>
              <div class="permission-actions">
                <ACheckbox
                  v-for="item in module.actions"
                  :key="item.code"
                  :checked="form.permissions.includes(item.code)"
                  :disabled="!grantable(item)"
                  :aria-label="module.title + '：' + item.label"
                  @change="(event) => toggleAction(module, item, !!event.target.checked)"
                  >{{ item.label }}</ACheckbox
                >
              </div>
            </div>
            <p v-else-if="selectedCount(module)" class="permission-module__inactive">
              已关闭，保留 {{ selectedCount(module) }} 项操作设置
            </p>
          </section>
        </div>
        <p v-if="!modules.length" class="admin-muted">
          {{ permissionError || '正在加载权限目录' }}
        </p>
      </fieldset>
    </AdminDialog>
    <AModal :open="Boolean(preview)" title="查看内置角色" :footer="null" @cancel="preview = null">
      <template v-if="preview">
        <p class="admin-muted">
          系统内置角色由系统维护，身份通过用户管理中的“超级管理员”开关管理。
        </p>
        <dl class="role-details">
          <dt>角色名称</dt>
          <dd>{{ preview.name }}</dd>
          <dt>角色编码</dt>
          <dd class="admin-code">{{ preview.code }}</dd>
          <dt>权限</dt>
          <dd>{{ permissionSummary(preview) }}</dd>
          <dt>成员数</dt>
          <dd>{{ preview.member_count }}</dd>
          <dt>状态</dt>
          <dd>{{ activeLabel(preview.is_active) }}</dd>
          <dt>备注</dt>
          <dd>{{ preview.remark || '—' }}</dd>
        </dl>
      </template>
    </AModal>
  </AdminPage>
</template>
<style scoped>
.permission-list {
  padding: 0;
  border: 0;
  min-width: 0;
}
.permission-list legend {
  width: auto;
  margin: 0;
  padding: 0;
  font-size: 13px;
}
.role-fields {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}
.permission-modules {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}
.permission-module {
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.permission-module__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.permission-module__header > div {
  min-width: 0;
}
.permission-module__header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
}
.permission-module__header p,
.permission-module__inactive {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.permission-module__header :deep(.ant-switch) {
  flex-shrink: 0;
}
.permission-module__operations {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--gray-150);
}
.permission-module__selection {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.permission-module__selection > span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.permission-actions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 12px;
}
.permission-actions :deep(.ant-checkbox-wrapper) {
  margin-inline-start: 0;
  font-size: 13px;
}
@media (max-width: 600px) {
  .permission-actions {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.role-builtin {
  justify-self: start;
}
.role-details {
  display: grid;
  grid-template-columns: 80px minmax(0, 1fr);
  gap: 12px 16px;
  margin: 20px 0 0;
}
.role-details dt {
  color: var(--color-text-secondary);
}
.role-details dd {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
