<script setup lang="ts">
import { computed, inject, reactive, ref } from 'vue'
import { Button as AButton, Modal, Tag as ATag } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { adminNavigation, hasAdminPermission } from '../access.ts'
import { decodeMenu } from '../api/index.ts'
import type { Menu, MenuPayload } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { validSortOrder } from '../presentation.ts'
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<Menu, MenuPayload>('menus', decodeMenu, {}, true)
const writable = computed(() => hasAdminPermission(identity.value, 'menus', 'write'))
const refreshNavigation = inject<() => Promise<void>>('refreshAdminNavigation', () =>
  Promise.resolve()
)
const open = ref(false),
  editing = ref<Menu | null>(null),
  formError = ref('')
const form = reactive({
  parent_id: '',
  name: '',
  path: '/admin/users',
  permission: 'system:users:read',
  sort_order: 0,
  is_visible: true
})
function edit(row: Menu | null = null): void {
  editing.value = row
  Object.assign(
    form,
    row
      ? {
          parent_id: row.parent_id ?? '',
          name: row.name,
          path: row.path,
          permission: row.permission,
          sort_order: row.sort_order,
          is_visible: row.is_visible
        }
      : {
          parent_id: '',
          name: '',
          path: '/admin/users',
          permission: 'system:users:read',
          sort_order: 0,
          is_visible: true
        }
  )
  formError.value = ''
  open.value = true
}
function choosePath(): void {
  const entry = adminNavigation.find((item) => item.path === form.path)
  if (entry) {
    form.permission = `system:${entry.area}:read`
    if (!editing.value && !form.name) form.name = entry.label
  }
}
const parentOptions = computed(() =>
  rows.value.filter((row) => row.id !== editing.value?.id && row.parent_id === null)
)
async function submit(): Promise<void> {
  if (saving.value) return
  if (!validSortOrder(form.sort_order)) {
    formError.value = '排序必须为 0 至 1000000 之间的整数'
    return
  }
  if (!form.name.trim()) {
    formError.value = '请填写菜单名称'
    return
  }
  const entry = adminNavigation.find((item) => item.path === form.path)
  if (!entry) {
    formError.value = '请选择已接通的后台页面'
    return
  }
  try {
    if (
      await save(editing.value, {
        ...form,
        name: form.name.trim(),
        parent_id: form.parent_id || null,
        permission: `system:${entry.area}:read`
      })
    ) {
      open.value = false
      await refreshNavigation()
    }
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(row: Menu): void {
  if (!saving.value)
    Modal.confirm({
      title: `删除菜单配置“${row.name}”？`,
      content: '删除后该页面会恢复默认导航名称、顺序及显示状态。',
      okText: '删除配置',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        await remove(row)
        await refreshNavigation()
      }
    })
}
const columns = [
  { title: '导航名称', dataIndex: 'name', key: 'name' },
  { title: '页面路径', dataIndex: 'path', key: 'path', width: 220 },
  { title: '固定权限', dataIndex: 'permission', key: 'permission', width: 240 },
  { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 80 },
  { title: '显示', key: 'visible', width: 90 },
  { title: '操作', key: 'actions', width: 120 }
]
</script>
<template>
  <AdminPage
    title="菜单管理"
    subtitle="设置后台导航名称、排序与显隐"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :page-size="Math.max(total, 20)"
    :search="search"
    :can-create="writable"
    create-label="新建菜单配置"
    @refresh="load"
    @create="edit()"
    @search="find"
    @page="changePage"
    ><ResourceTable
      :columns="columns"
      :rows="rows"
      :loading="loading"
      empty-text="尚未配置菜单，当前使用默认导航"
      ><template #bodyCell="{ column, record }"
        ><ATag v-if="column.key === 'visible'" :color="record.is_visible ? 'green' : 'default'">{{
          record.is_visible ? '显示' : '隐藏'
        }}</ATag>
        <div v-else-if="column.key === 'actions' && writable" class="admin-actions">
          <AButton type="link" @click="edit(record)">编辑</AButton
          ><AButton type="link" danger :disabled="saving" @click="confirmDelete(record)"
            >删除</AButton
          >
        </div></template
      ></ResourceTable
    >
    <AdminDialog
      :open="open"
      :title="editing ? '编辑菜单配置' : '新建菜单配置'"
      :saving="saving"
      :error="formError"
      @close="open = false"
      @save="submit"
      ><label>导航名称<input v-model="form.name" aria-label="导航名称" maxlength="100" /></label
      ><label
        >已接通页面<select v-model="form.path" aria-label="已接通页面" @change="choosePath">
          <option v-for="entry in adminNavigation" :key="entry.path" :value="entry.path">
            {{ entry.label }}（{{ entry.path }}）
          </option>
        </select></label
      ><label
        >上级配置<select v-model="form.parent_id" aria-label="上级配置">
          <option value="">无</option>
          <option v-for="item in parentOptions" :key="item.id" :value="item.id">
            {{ item.name }}
          </option>
        </select></label
      ><label
        >固定权限<input aria-label="固定权限" :value="form.permission" disabled /><small
          >菜单配置不能改变页面和接口所需权限。</small
        ></label
      ><label
        >排序<input
          v-model.number="form.sort_order"
          aria-label="排序"
          type="number"
          min="0"
          max="1000000" /></label
      ><label class="admin-check"
        ><input v-model="form.is_visible" type="checkbox" />在后台导航中显示</label
      ></AdminDialog
    >
  </AdminPage>
</template>
