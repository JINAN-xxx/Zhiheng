<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { Button as AButton, Modal, Tag as ATag, message } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { decodeUser, getUserOptions, resetPassword } from '../api/index.ts'
import type { AdminUser, UserPayload, UserOptions } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { activeLabel } from '../presentation.ts'
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<AdminUser, UserPayload>('users', decodeUser)
const writable = computed(() => hasAdminPermission(identity.value, 'users', 'write'))
const open = ref(false),
  editing = ref<AdminUser | null>(null),
  formError = ref(''),
  optionsLoading = ref(false)
const options = ref<UserOptions>({ departments: [], posts: [], roles: [] })
const form = reactive({
  username: '',
  display_name: '',
  email: '',
  phone: '',
  department_id: '',
  post_ids: [] as string[],
  role_ids: [] as string[],
  is_active: true,
  is_admin: false,
  remark: '',
  password: ''
})
let optionsController: AbortController | null = null
const canManage = (user: AdminUser): boolean => Boolean(identity.value?.is_admin || !user.is_admin)
const ownAccount = computed(() => editing.value?.id === identity.value?.id)
const protectedAdmin = (user: AdminUser): boolean =>
  user.is_admin &&
  user.is_active &&
  rows.value.filter((row) => row.is_active && row.is_admin).length <= 1
function close(): void {
  if (saving.value) return
  open.value = false
  form.password = ''
  optionsController?.abort()
}
async function edit(user: AdminUser | null = null): Promise<void> {
  if (user && !canManage(user)) return
  editing.value = user
  Object.assign(
    form,
    user
      ? {
          ...user,
          department_id: user.department_id ?? '',
          post_ids: [...user.post_ids],
          role_ids: [...user.role_ids],
          password: ''
        }
      : {
          username: '',
          display_name: '',
          email: '',
          phone: '',
          department_id: '',
          post_ids: [],
          role_ids: [],
          is_active: true,
          is_admin: false,
          remark: '',
          password: ''
        }
  )
  formError.value = ''
  open.value = true
  optionsLoading.value = true
  optionsController?.abort()
  optionsController = new AbortController()
  const controller = optionsController
  try {
    const value = await getUserOptions(controller.signal)
    if (!controller.signal.aborted) options.value = value
  } catch (failure) {
    if (!controller.signal.aborted) formError.value = adminError(failure)
  } finally {
    if (!controller.signal.aborted) optionsLoading.value = false
  }
}
function withExisting(
  items: { id: string; name: string; system_key?: string | null }[],
  selected: string[]
): { id: string; name: string; system_key?: string | null }[] {
  return [
    ...items,
    ...selected
      .filter((id) => !items.some((item) => item.id === id))
      .map((id) => ({ id, name: `已分配：${id}` }))
  ]
}
async function submit(): Promise<void> {
  if (saving.value || optionsLoading.value) return
  formError.value = ''
  if (!form.username.trim()) {
    formError.value = '请填写用户名'
    return
  }
  if (!editing.value && form.password.length < 12) {
    formError.value = '新用户密码至少需要 12 位'
    return
  }
  const body = {
    display_name: form.display_name.trim(),
    email: form.email.trim(),
    phone: form.phone.trim(),
    department_id: form.department_id || null,
    post_ids: [...form.post_ids],
    role_ids: [...form.role_ids],
    is_active: form.is_active,
    remark: form.remark,
    ...(identity.value?.is_admin ? { is_admin: form.is_admin } : {}),
    ...(!editing.value ? { username: form.username.trim(), password: form.password } : {})
  }
  try {
    if (await save(editing.value, body)) {
      open.value = false
      form.password = ''
      message.success('用户已保存')
    }
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(user: AdminUser): void {
  if (saving.value || !canManage(user) || user.id === identity.value?.id || protectedAdmin(user))
    return
  Modal.confirm({
    title: `删除用户“${user.username}”？`,
    content: '删除后此账号将不能登录，历史业务归属会保留。',
    okText: '删除用户',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => remove(user)
  })
}
const passwordUser = ref<AdminUser | null>(null),
  password = ref(''),
  passwordError = ref(''),
  passwordSaving = ref(false)
function closePassword(): void {
  if (passwordSaving.value) return
  passwordUser.value = null
  password.value = ''
  passwordError.value = ''
}
function openPassword(user: AdminUser): void {
  if (!canManage(user)) return
  passwordUser.value = user
  password.value = ''
  passwordError.value = ''
}
async function submitPassword(): Promise<void> {
  if (!passwordUser.value || passwordSaving.value) return
  if (password.value.length < 12) {
    passwordError.value = '新密码至少需要 12 位'
    return
  }
  passwordSaving.value = true
  passwordError.value = ''
  try {
    await resetPassword(passwordUser.value, password.value)
    password.value = ''
    passwordUser.value = null
    message.success('密码已重置，原登录会话已失效')
    void load()
  } catch (failure) {
    passwordError.value = adminError(failure)
  } finally {
    passwordSaving.value = false
  }
}
onBeforeUnmount(() => {
  optionsController?.abort()
  form.password = ''
  password.value = ''
})
const columns = [
  { title: '用户', key: 'user', width: 190 },
  { title: '联系方式', key: 'contact', width: 200 },
  { title: '状态', key: 'status', width: 130 },
  { title: '备注', dataIndex: 'remark', key: 'remark', width: 180 },
  { title: '操作', key: 'actions', width: 220 }
]
</script>
<template>
  <AdminPage
    title="用户管理"
    subtitle="维护平台账号、组织归属与角色授权"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :search="search"
    :can-create="writable"
    create-label="新建用户"
    @refresh="load"
    @create="edit()"
    @search="find"
    @page="changePage"
  >
    <ResourceTable :columns="columns" :rows="rows" :loading="loading" empty-text="暂无用户">
      <template #bodyCell="{ column, record }">
        <div v-if="column.key === 'user'" class="admin-primary">
          <strong>{{ record.display_name || record.username }}</strong
          ><small>{{ record.username }}{{ record.id === identity?.id ? '（本人）' : '' }}</small>
        </div>
        <div v-else-if="column.key === 'contact'" class="admin-primary">
          <span>{{ record.email || '—' }}</span
          ><small>{{ record.phone || '—' }}</small>
        </div>
        <template v-else-if="column.key === 'status'"
          ><ATag :color="record.is_active ? 'green' : 'default'">{{
            activeLabel(record.is_active)
          }}</ATag
          ><ATag v-if="record.is_admin" color="purple">超级管理员</ATag></template
        >
        <div v-else-if="column.key === 'actions' && writable" class="admin-actions">
          <AButton type="link" :disabled="!canManage(record)" @click="edit(record)">编辑</AButton
          ><AButton type="link" :disabled="!canManage(record)" @click="openPassword(record)"
            >重置密码</AButton
          ><AButton
            type="link"
            danger
            :disabled="
              saving || !canManage(record) || record.id === identity?.id || protectedAdmin(record)
            "
            @click="confirmDelete(record)"
            >删除</AButton
          >
        </div>
      </template>
    </ResourceTable>
    <AdminDialog
      :open="open"
      :title="editing ? '编辑用户' : '新建用户'"
      :saving="saving || optionsLoading"
      :error="formError"
      :width="640"
      @close="close"
      @save="submit"
    >
      <div class="admin-form__grid">
        <label
          >用户名<input
            v-model="form.username"
            aria-label="用户名"
            :disabled="Boolean(editing)"
            maxlength="100"
            autocomplete="off"
            required /></label
        ><label
          >显示名称<input v-model="form.display_name" aria-label="显示名称" maxlength="100"
        /></label>
      </div>
      <label v-if="!editing"
        >初始密码<input
          v-model="form.password"
          aria-label="初始密码"
          type="password"
          minlength="12"
          maxlength="128"
          autocomplete="new-password"
          placeholder="至少 12 位"
          required
        /><small>密码只用于本次设置，不会再次显示。</small></label
      >
      <div class="admin-form__grid">
        <label
          >电子邮箱<input
            v-model="form.email"
            aria-label="电子邮箱"
            type="email"
            maxlength="254" /></label
        ><label>手机号<input v-model="form.phone" aria-label="手机号" maxlength="30" /></label>
      </div>
      <label
        >所属部门<select v-model="form.department_id" aria-label="所属部门">
          <option value="">未分配</option>
          <option
            v-for="item in withExisting(
              options.departments,
              form.department_id ? [form.department_id] : []
            )"
            :key="item.id"
            :value="item.id"
          >
            {{ item.name }}
          </option>
        </select></label
      >
      <div class="admin-form__grid">
        <label
          >岗位<select v-model="form.post_ids" aria-label="岗位" multiple>
            <option
              v-for="item in withExisting(options.posts, form.post_ids)"
              :key="item.id"
              :value="item.id"
            >
              {{ item.name }}
            </option>
          </select></label
        ><label
          >角色<select v-model="form.role_ids" aria-label="角色" multiple>
            <option
              v-for="item in withExisting(options.roles, form.role_ids)"
              :key="item.id"
              :value="item.id"
              :disabled="Boolean(item.system_key)"
            >
              {{ item.name }}{{ item.system_key ? '（系统内置）' : '' }}
            </option></select
          ><small>系统内置角色由“超级管理员”开关管理。</small></label
        >
      </div>
      <label class="admin-check"
        ><input
          v-model="form.is_active"
          type="checkbox"
          :disabled="ownAccount || Boolean(editing && protectedAdmin(editing))"
        />启用账号</label
      >
      <label v-if="identity?.is_admin" class="admin-check"
        ><input
          v-model="form.is_admin"
          type="checkbox"
          :disabled="ownAccount || Boolean(editing && protectedAdmin(editing))"
        />超级管理员</label
      >
      <label
        >备注<textarea v-model="form.remark" aria-label="备注" rows="3" maxlength="1000" />
      </label>
    </AdminDialog>
    <AdminDialog
      :open="Boolean(passwordUser)"
      :title="`重置密码：${passwordUser?.username || ''}`"
      :saving="passwordSaving"
      :error="passwordError"
      save-label="重置密码"
      @close="closePassword"
      @save="submitPassword"
      ><label
        >新密码<input
          v-model="password"
          aria-label="新密码"
          type="password"
          minlength="12"
          maxlength="128"
          autocomplete="new-password"
          placeholder="至少 12 位"
        /><small>重置后该用户已有会话会失效。</small></label
      ></AdminDialog
    >
  </AdminPage>
</template>
