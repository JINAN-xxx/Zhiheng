<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Button as AButton, Drawer as ADrawer, Modal, Tag as ATag } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { decodeDictionary } from '../api/index.ts'
import type { Dictionary, DictionaryPayload } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import DictionaryItemsPanel from '../components/DictionaryItemsPanel.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { activeLabel } from '../presentation.ts'
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<Dictionary, DictionaryPayload>('dictionaries', decodeDictionary)
const writable = computed(() => hasAdminPermission(identity.value, 'dictionaries', 'write'))
const open = ref(false),
  editing = ref<Dictionary | null>(null),
  selected = ref<Dictionary | null>(null),
  formError = ref('')
const form = reactive({ code: '', name: '', is_active: true, remark: '' })
function edit(row: Dictionary | null = null): void {
  editing.value = row
  Object.assign(
    form,
    row
      ? { code: row.code, name: row.name, is_active: row.is_active, remark: row.remark }
      : { code: '', name: '', is_active: true, remark: '' }
  )
  formError.value = ''
  open.value = true
}
async function submit(): Promise<void> {
  if (saving.value) return
  if (!form.code.trim() || !form.name.trim()) {
    formError.value = '请填写字典编码和名称'
    return
  }
  try {
    if (await save(editing.value, { ...form, code: form.code.trim(), name: form.name.trim() }))
      open.value = false
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(row: Dictionary): void {
  if (!saving.value)
    Modal.confirm({
      title: `删除字典“${row.name}”？`,
      content: '包含字典条目的类型无法删除，请先处理条目。',
      okText: '删除字典',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => remove(row)
    })
}
const columns = [
  { title: '字典名称', dataIndex: 'name', key: 'name' },
  { title: '字典编码', dataIndex: 'code', key: 'code' },
  { title: '状态', key: 'status', width: 100 },
  { title: '备注', dataIndex: 'remark', key: 'remark' },
  { title: '操作', key: 'actions', width: 210 }
]
</script>
<template>
  <AdminPage
    title="字典管理"
    subtitle="维护字典类型及其条目"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :search="search"
    :can-create="writable"
    create-label="新建字典"
    @refresh="load"
    @create="edit()"
    @search="find"
    @page="changePage"
    ><ResourceTable :columns="columns" :rows="rows" :loading="loading" empty-text="暂无字典"
      ><template #bodyCell="{ column, record }"
        ><ATag v-if="column.key === 'status'" :color="record.is_active ? 'green' : 'default'">{{
          activeLabel(record.is_active)
        }}</ATag>
        <div v-else-if="column.key === 'actions'" class="admin-actions">
          <AButton type="link" @click="selected = record">字典条目</AButton
          ><AButton v-if="writable" type="link" @click="edit(record)">编辑</AButton
          ><AButton
            v-if="writable"
            type="link"
            danger
            :disabled="saving"
            @click="confirmDelete(record)"
            >删除</AButton
          >
        </div></template
      ></ResourceTable
    ><AdminDialog
      :open="open"
      :title="editing ? '编辑字典' : '新建字典'"
      :saving="saving"
      :error="formError"
      @close="open = false"
      @save="submit"
      ><label>字典编码<input v-model="form.code" aria-label="字典编码" maxlength="100" /></label
      ><label>字典名称<input v-model="form.name" aria-label="字典名称" maxlength="100" /></label
      ><label class="admin-check"><input v-model="form.is_active" type="checkbox" />启用字典</label
      ><label
        >备注<textarea
          v-model="form.remark"
          aria-label="备注"
          rows="3"
          maxlength="1000"
        /></label></AdminDialog
    ><ADrawer
      :open="Boolean(selected)"
      title="字典条目"
      width="min(900px, 100vw)"
      :body-style="{ padding: 0 }"
      destroy-on-close
      @close="selected = null"
      ><DictionaryItemsPanel v-if="selected" :key="selected.id" :dictionary="selected" /></ADrawer
  ></AdminPage>
</template>
