<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Button as AButton, Modal, Tag as ATag } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { decodeDepartment } from '../api/index.ts'
import type { Department, DepartmentPayload } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { activeLabel, validSortOrder } from '../presentation.ts'
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<Department, DepartmentPayload>('departments', decodeDepartment, {}, true)
const writable = computed(() => hasAdminPermission(identity.value, 'departments', 'write'))
interface DepartmentNode extends Department {
  children?: DepartmentNode[]
}
function branch(parent: string | null, ancestors: string[] = []): DepartmentNode[] {
  return rows.value
    .filter((row) => row.parent_id === parent && !ancestors.includes(row.id))
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((row) => {
      const children = branch(row.id, [...ancestors, row.id])
      return { ...row, ...(children.length ? { children } : {}) }
    })
}
const tree = computed(() => {
  const roots = rows.value.filter(
    (row) => row.parent_id === null || !rows.value.some((parent) => parent.id === row.parent_id)
  )
  return roots
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((row) => {
      const children = branch(row.id, [row.id])
      return { ...row, ...(children.length ? { children } : {}) }
    })
})
const open = ref(false),
  editing = ref<Department | null>(null),
  formError = ref('')
const form = reactive({
  parent_id: '',
  name: '',
  sort_order: 0,
  leader: '',
  phone: '',
  email: '',
  is_active: true
})
function descendsFrom(row: Department, id: string): boolean {
  const visited = new Set<string>()
  let parent = row.parent_id
  while (parent) {
    if (parent === id || visited.has(parent)) return true
    visited.add(parent)
    parent = rows.value.find((item) => item.id === parent)?.parent_id ?? null
  }
  return row.id === id
}
const parentOptions = computed(() =>
  rows.value.filter((row) => !editing.value || !descendsFrom(row, editing.value.id))
)
function edit(row: Department | null = null, parent = ''): void {
  editing.value = row
  Object.assign(
    form,
    row
      ? {
          parent_id: row.parent_id ?? '',
          name: row.name,
          sort_order: row.sort_order,
          leader: row.leader,
          phone: row.phone,
          email: row.email,
          is_active: row.is_active
        }
      : {
          parent_id: parent,
          name: '',
          sort_order: 0,
          leader: '',
          phone: '',
          email: '',
          is_active: true
        }
  )
  formError.value = ''
  open.value = true
}
async function submit(): Promise<void> {
  if (saving.value) return
  if (!validSortOrder(form.sort_order)) {
    formError.value = '排序必须为 0 至 1000000 之间的整数'
    return
  }
  if (!form.name.trim()) {
    formError.value = '请填写部门名称'
    return
  }
  try {
    if (
      await save(editing.value, {
        ...form,
        name: form.name.trim(),
        parent_id: form.parent_id || null
      })
    )
      open.value = false
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(row: Department): void {
  if (!saving.value)
    Modal.confirm({
      title: `删除部门“${row.name}”？`,
      content: '包含子部门或关联用户的部门无法删除。',
      okText: '删除部门',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => remove(row)
    })
}
const columns = [
  { title: '部门名称', dataIndex: 'name', key: 'name', width: 240 },
  { title: '负责人', dataIndex: 'leader', key: 'leader', width: 120 },
  { title: '联系方式', key: 'contact', width: 200 },
  { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 80 },
  { title: '状态', key: 'status', width: 100 },
  { title: '操作', key: 'actions', width: 180 }
]
</script>
<template>
  <AdminPage
    title="部门管理"
    subtitle="按组织层级维护部门与负责人"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :page-size="Math.max(total, 20)"
    :search="search"
    :can-create="writable"
    create-label="新建部门"
    @refresh="load"
    @create="edit()"
    @search="find"
    @page="changePage"
    ><ResourceTable :columns="columns" :rows="tree" :loading="loading" empty-text="暂无部门"
      ><template #bodyCell="{ column, record }"
        ><div v-if="column.key === 'contact'" class="admin-primary">
          <span>{{ record.phone || '—' }}</span
          ><small>{{ record.email || '—' }}</small>
        </div>
        <ATag v-else-if="column.key === 'status'" :color="record.is_active ? 'green' : 'default'">{{
          activeLabel(record.is_active)
        }}</ATag>
        <div v-else-if="column.key === 'actions' && writable" class="admin-actions">
          <AButton type="link" @click="edit(null, record.id)">新增下级</AButton
          ><AButton type="link" @click="edit(record)">编辑</AButton
          ><AButton
            type="link"
            danger
            :disabled="saving || rows.some((row) => row.parent_id === record.id)"
            @click="confirmDelete(record)"
            >删除</AButton
          >
        </div></template
      ></ResourceTable
    >
    <AdminDialog
      :open="open"
      :title="editing ? '编辑部门' : '新建部门'"
      :saving="saving"
      :error="formError"
      @close="open = false"
      @save="submit"
      ><label
        >上级部门<select v-model="form.parent_id" aria-label="上级部门">
          <option value="">顶级部门</option>
          <option v-for="item in parentOptions" :key="item.id" :value="item.id">
            {{ item.name }}
          </option>
        </select></label
      ><label>部门名称<input v-model="form.name" aria-label="部门名称" maxlength="100" /></label>
      <div class="admin-form__grid">
        <label>负责人<input v-model="form.leader" aria-label="负责人" maxlength="100" /></label
        ><label
          >排序<input
            v-model.number="form.sort_order"
            aria-label="排序"
            type="number"
            min="0"
            max="1000000" /></label
        ><label>手机号<input v-model="form.phone" aria-label="手机号" maxlength="30" /></label
        ><label
          >电子邮箱<input v-model="form.email" aria-label="电子邮箱" type="email" maxlength="254"
        /></label>
      </div>
      <label class="admin-check"
        ><input v-model="form.is_active" type="checkbox" />启用部门</label
      ></AdminDialog
    >
  </AdminPage>
</template>
