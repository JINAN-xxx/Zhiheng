<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Button as AButton, Modal, Tag as ATag } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { decodeConfig } from '../api/index.ts'
import type { Config, ConfigPayload } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { activeLabel } from '../presentation.ts'
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<Config, ConfigPayload>('configs', decodeConfig)
const writable = computed(() => hasAdminPermission(identity.value, 'configs', 'write'))
const open = ref(false),
  editing = ref<Config | null>(null),
  formError = ref('')
const form = reactive({
  key: 'console.notice_enabled',
  name: '控制台公告显示',
  value: 'true',
  is_active: true,
  remark: ''
})
function edit(row: Config | null = null): void {
  editing.value = row
  Object.assign(
    form,
    row
      ? {
          key: row.key,
          name: row.name,
          value: row.value,
          is_active: row.is_active,
          remark: row.remark
        }
      : {
          key: 'console.notice_enabled',
          name: '控制台公告显示',
          value: 'true',
          is_active: true,
          remark: ''
        }
  )
  formError.value = ''
  open.value = true
}
async function submit(): Promise<void> {
  if (saving.value) return
  if (!form.key.trim() || !form.name.trim()) {
    formError.value = '请填写参数键和名称'
    return
  }
  if (form.key === 'console.notice_enabled' && !['true', 'false'].includes(form.value)) {
    formError.value = '公告开关只能为 true 或 false'
    return
  }
  try {
    if (await save(editing.value, { ...form, key: form.key.trim(), name: form.name.trim() }))
      open.value = false
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(row: Config): void {
  if (!saving.value)
    Modal.confirm({
      title: `删除参数“${row.name}”？`,
      content: '删除后对应行为会使用系统默认设置。',
      okText: '删除参数',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => remove(row)
    })
}
const columns = [
  { title: '参数名称', dataIndex: 'name', key: 'name' },
  { title: '参数键', dataIndex: 'key', key: 'key', width: 250 },
  { title: '参数值', dataIndex: 'value', key: 'value', width: 120 },
  { title: '状态', key: 'status', width: 100 },
  { title: '备注', dataIndex: 'remark', key: 'remark' },
  { title: '操作', key: 'actions', width: 120 }
]
</script>
<template>
  <AdminPage
    title="参数管理"
    subtitle="维护控制台已支持的业务参数"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :search="search"
    :can-create="writable"
    create-label="新建参数"
    @refresh="load"
    @create="edit()"
    @search="find"
    @page="changePage"
    ><ResourceTable :columns="columns" :rows="rows" :loading="loading" empty-text="暂无业务参数"
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
      :title="editing ? '编辑参数' : '新建参数'"
      :saving="saving"
      :error="formError"
      @close="open = false"
      @save="submit"
      ><label
        >参数键<select v-model="form.key" aria-label="参数键" :disabled="Boolean(editing)">
          <option value="console.notice_enabled">console.notice_enabled · 控制台公告显示</option>
          <option v-if="form.key !== 'console.notice_enabled'" :value="form.key">
            {{ form.key }}
          </option>
        </select></label
      ><label>参数名称<input v-model="form.name" aria-label="参数名称" maxlength="100" /></label
      ><label
        >参数值<select
          v-if="form.key === 'console.notice_enabled'"
          v-model="form.value"
          aria-label="参数值"
        >
          <option value="true">显示公告（true）</option>
          <option value="false">隐藏公告（false）</option></select
        ><input v-else v-model="form.value" maxlength="4000" /><small
          >公告开关用于控制登录用户的通知面板，停用参数时默认显示公告。</small
        ></label
      ><label class="admin-check"><input v-model="form.is_active" type="checkbox" />启用参数</label
      ><label
        >备注<textarea
          v-model="form.remark"
          aria-label="备注"
          rows="3"
          maxlength="1000"
        /></label></AdminDialog
  ></AdminPage>
</template>
