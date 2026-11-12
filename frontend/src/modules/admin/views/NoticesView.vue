<script setup lang="ts">
import AdminRowActions from '../components/AdminRowActions.vue'
import { computed, reactive, ref } from 'vue'
import {
  Modal as AModal,
  Modal,
  Tag as ATag,
  Input as AInput,
  Textarea as ATextarea,
  Select as ASelect
} from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { decodeNotice } from '../api/index.ts'
import type { Notice, NoticePayload } from '../api/index.ts'
import { adminError, useCollection } from '../composables/useCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import AdminDialog from '../components/AdminDialog.vue'
import NoticeVisibilitySetting from '../components/NoticeVisibilitySetting.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { formatAdminTime } from '../presentation.ts'
const { rows, total, page, search, loading, error, saving, load, save, remove, find, changePage } =
  useCollection<Notice, NoticePayload>('notices', decodeNotice)
const visibility = ref<InstanceType<typeof NoticeVisibilitySetting> | null>(null)
function refreshPage(): void {
  void load()
  void visibility.value?.refresh()
}
const writable = computed(() => hasAdminPermission(identity.value, 'notices', 'write'))
const open = ref(false),
  editing = ref<Notice | null>(null),
  preview = ref<Notice | null>(null),
  formError = ref('')
const form = reactive({
  title: '',
  content: '',
  kind: 'notice' as Notice['kind'],
  status: 'draft' as Notice['status'],
  remark: ''
})
function edit(row: Notice | null = null): void {
  editing.value = row
  Object.assign(
    form,
    row
      ? {
          title: row.title,
          content: row.content,
          kind: row.kind,
          status: row.status,
          remark: row.remark
        }
      : { title: '', content: '', kind: 'notice', status: 'draft', remark: '' }
  )
  formError.value = ''
  open.value = true
}
async function submit(): Promise<void> {
  if (saving.value) return
  if (!form.title.trim() || !form.content.trim()) {
    formError.value = '请填写公告标题和正文'
    return
  }
  try {
    if (await save(editing.value, { ...form, title: form.title.trim() })) open.value = false
  } catch (failure) {
    formError.value = adminError(failure)
  }
}
function confirmDelete(row: Notice): void {
  if (!saving.value)
    Modal.confirm({
      title: `删除公告“${row.title}”？`,
      content: '删除后登录用户将不能再查看该公告。',
      okText: '删除公告',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => remove(row)
    })
}
const columns = [
  { title: '标题', dataIndex: 'title', key: 'title', width: 260 },
  { title: '类型', key: 'kind', width: 100 },
  { title: '状态', key: 'status', width: 100 },
  { title: '更新时间', key: 'updated', width: 180 },
  { title: '操作', key: 'actions', width: 110 }
]
</script>
<template>
  <AdminPage
    search-placeholder="搜索公告标题"
    title="通知公告"
    subtitle="维护平台通知与公告内容"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :search="search"
    :can-create="writable"
    create-label="新建公告"
    @refresh="refreshPage"
    @create="edit()"
    @search="find"
    @page="changePage"
    ><template #before-list><NoticeVisibilitySetting ref="visibility" /></template
    ><ResourceTable
      :loading-delay="150"
      :columns="columns"
      :rows="rows"
      :loading="loading"
      empty-text="暂无公告"
      ><template #bodyCell="{ column, record }"
        ><span v-if="column.key === 'kind'">{{ record.kind === 'notice' ? '通知' : '公告' }}</span
        ><ATag
          v-else-if="column.key === 'status'"
          :color="record.status === 'published' ? 'green' : 'default'"
          >{{ record.status === 'published' ? '已发布' : '草稿' }}</ATag
        ><span v-else-if="column.key === 'updated'">{{ formatAdminTime(record.updated_at) }}</span>
        <AdminRowActions
          v-else-if="column.key === 'actions'"
          :primary="{ key: 'preview', label: '查看' }"
          :more="
            writable
              ? [
                  { key: 'edit', label: '编辑' },
                  { key: 'delete', label: '删除', danger: true }
                ]
              : []
          "
          :busy="saving"
          @select="
            (key) =>
              key === 'preview'
                ? (preview = record)
                : key === 'edit'
                  ? edit(record)
                  : confirmDelete(record)
          " /></template></ResourceTable
    ><AdminDialog
      :open="open"
      :title="editing ? '编辑公告' : '新建公告'"
      :saving="saving"
      :error="formError"
      :width="700"
      @close="open = false"
      @save="submit"
      ><label
        >公告标题<AInput v-model:value="form.title" aria-label="公告标题" :maxlength="200"
      /></label>
      <div class="admin-form__grid">
        <label
          >类型<ASelect
            :value="form.kind"
            aria-label="类型"
            :options="[
              { value: 'notice', label: '通知' },
              { value: 'announcement', label: '公告' }
            ]"
            @update:value="
              form.kind = $event === 'announcement' ? 'announcement' : 'notice'
            " /></label
        ><label
          >发布状态<ASelect
            :value="form.status"
            aria-label="发布状态"
            :options="[
              { value: 'draft', label: '草稿' },
              { value: 'published', label: '发布' }
            ]"
            @update:value="form.status = $event === 'published' ? 'published' : 'draft'"
        /></label>
      </div>
      <label
        >公告正文<ATextarea
          v-model:value="form.content"
          aria-label="公告正文"
          :rows="10"
          :maxlength="32000" /></label
      ><label
        >备注<ATextarea
          v-model:value="form.remark"
          aria-label="备注"
          :rows="2"
          :maxlength="1000" /></label></AdminDialog
    ><AModal
      :open="Boolean(preview)"
      :title="preview?.title"
      :footer="null"
      :width="700"
      @cancel="preview = null"
      ><p class="notice-content">{{ preview?.content }}</p></AModal
    ></AdminPage
  >
</template>
<style scoped>
.notice-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
}
</style>
