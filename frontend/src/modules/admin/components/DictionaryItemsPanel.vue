<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Button as AButton, Modal, Tag as ATag } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { decodeDictionaryItem } from '../api/index.ts'
import type { Dictionary, DictionaryItem, DictionaryItemPayload } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from './AdminPage.vue'
import AdminDialog from './AdminDialog.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { activeLabel, validSortOrder } from '../presentation.ts'
const props = defineProps<{ dictionary: Dictionary }>()
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<DictionaryItem, DictionaryItemPayload>(
    `dictionaries/${encodeURIComponent(props.dictionary.id)}/items`,
    decodeDictionaryItem
  )
const writable = computed(() => hasAdminPermission(identity.value, 'dictionaries', 'write'))
const open = ref(false),
  editing = ref<DictionaryItem | null>(null),
  formError = ref('')
const form = reactive({ label: '', value: '', sort_order: 0, is_active: true, remark: '' })
function edit(row: DictionaryItem | null = null): void {
  editing.value = row
  Object.assign(
    form,
    row
      ? {
          label: row.label,
          value: row.value,
          sort_order: row.sort_order,
          is_active: row.is_active,
          remark: row.remark
        }
      : { label: '', value: '', sort_order: 0, is_active: true, remark: '' }
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
  if (!form.label.trim() || !form.value.trim()) {
    formError.value = '请填写条目标签和值'
    return
  }
  try {
    if (await save(editing.value, { ...form, label: form.label.trim(), value: form.value.trim() }))
      open.value = false
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(row: DictionaryItem): void {
  if (!saving.value)
    Modal.confirm({
      title: `删除条目“${row.label}”？`,
      okText: '删除条目',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => remove(row)
    })
}
const columns = [
  { title: '标签', dataIndex: 'label', key: 'label' },
  { title: '值', dataIndex: 'value', key: 'value' },
  { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 80 },
  { title: '状态', key: 'status', width: 90 },
  { title: '备注', dataIndex: 'remark', key: 'remark' },
  { title: '操作', key: 'actions', width: 120 }
]
</script>
<template>
  <AdminPage
    :title="dictionary.name + ' · 字典条目'"
    :subtitle="dictionary.code"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :search="search"
    :can-create="writable"
    create-label="新建条目"
    @refresh="load"
    @create="edit()"
    @search="find"
    @page="changePage"
    ><ResourceTable
      :columns="columns"
      :rows="rows"
      :loading="loading"
      :scroll-x="650"
      empty-text="暂无字典条目"
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
    ><AdminDialog
      :open="open"
      :title="editing ? '编辑字典条目' : '新建字典条目'"
      :saving="saving"
      :error="formError"
      @close="open = false"
      @save="submit"
      ><label>条目标签<input v-model="form.label" aria-label="条目标签" maxlength="100" /></label
      ><label>条目值<input v-model="form.value" aria-label="条目值" maxlength="200" /></label
      ><label
        >排序<input
          v-model.number="form.sort_order"
          aria-label="排序"
          type="number"
          min="0"
          max="1000000" /></label
      ><label class="admin-check"><input v-model="form.is_active" type="checkbox" />启用条目</label
      ><label
        >备注<textarea
          v-model="form.remark"
          aria-label="备注"
          rows="3"
          maxlength="1000"
        /></label></AdminDialog
  ></AdminPage>
</template>
