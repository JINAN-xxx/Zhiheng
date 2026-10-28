<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Button as AButton, Modal, Tag as ATag } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { decodePost } from '../api/index.ts'
import type { Post, PostPayload } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { activeLabel, validSortOrder } from '../presentation.ts'
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<Post, PostPayload>('posts', decodePost)
const writable = computed(() => hasAdminPermission(identity.value, 'posts', 'write'))
const open = ref(false),
  editing = ref<Post | null>(null),
  formError = ref('')
const form = reactive({ code: '', name: '', sort_order: 0, is_active: true, remark: '' })
function edit(row: Post | null = null): void {
  editing.value = row
  Object.assign(
    form,
    row
      ? {
          code: row.code,
          name: row.name,
          sort_order: row.sort_order,
          is_active: row.is_active,
          remark: row.remark
        }
      : { code: '', name: '', sort_order: 0, is_active: true, remark: '' }
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
  if (!form.code.trim() || !form.name.trim()) {
    formError.value = '请填写岗位编码和名称'
    return
  }
  try {
    if (
      await save(editing.value, {
        name: form.name.trim(),
        sort_order: form.sort_order,
        is_active: form.is_active,
        remark: form.remark,
        ...(!editing.value ? { code: form.code.trim() } : {})
      })
    )
      open.value = false
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(row: Post): void {
  if (!saving.value)
    Modal.confirm({
      title: `删除岗位“${row.name}”？`,
      content: '仍有关联用户的岗位无法删除。',
      okText: '删除岗位',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => remove(row)
    })
}
const columns = [
  { title: '岗位名称', dataIndex: 'name', key: 'name' },
  { title: '岗位编码', dataIndex: 'code', key: 'code' },
  { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 80 },
  { title: '状态', key: 'status', width: 100 },
  { title: '备注', dataIndex: 'remark', key: 'remark' },
  { title: '操作', key: 'actions', width: 120 }
]
</script>
<template>
  <AdminPage
    title="岗位管理"
    subtitle="维护账号可分配的组织岗位"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :search="search"
    :can-create="writable"
    create-label="新建岗位"
    @refresh="load"
    @create="edit()"
    @search="find"
    @page="changePage"
    ><ResourceTable :columns="columns" :rows="rows" :loading="loading" empty-text="暂无岗位"
      ><template #bodyCell="{ column, record }"
        ><ATag v-if="column.key === 'status'" :color="record.is_active ? 'green' : 'default'">{{
          activeLabel(record.is_active)
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
      :title="editing ? '编辑岗位' : '新建岗位'"
      :saving="saving"
      :error="formError"
      @close="open = false"
      @save="submit"
      ><label
        >岗位编码<input
          v-model="form.code"
          aria-label="岗位编码"
          :disabled="Boolean(editing)"
          maxlength="100" /></label
      ><label>岗位名称<input v-model="form.name" aria-label="岗位名称" maxlength="100" /></label
      ><label
        >排序<input
          v-model.number="form.sort_order"
          aria-label="排序"
          type="number"
          min="0"
          max="1000000" /></label
      ><label class="admin-check"><input v-model="form.is_active" type="checkbox" />启用岗位</label
      ><label
        >备注<textarea v-model="form.remark" aria-label="备注" rows="3" maxlength="1000" /></label
    ></AdminDialog>
  </AdminPage>
</template>
