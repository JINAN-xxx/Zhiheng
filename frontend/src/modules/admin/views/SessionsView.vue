<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button as AButton, Modal, Tag as ATag } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { hasAdminPermission } from '../access.ts'
import { decodeSession, revokeSession } from '../api/index.ts'
import type { Session } from '../api/index.ts'
import { adminError } from '../composables/useCollection.ts'
import { useReadCollection } from '../composables/useReadCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { formatAdminTime } from '../presentation.ts'
const { rows, total, page, search, loading, error, load, find, changePage } = useReadCollection(
  'sessions',
  decodeSession
)
const writable = computed(() => hasAdminPermission(identity.value, 'sessions', 'write'))
const saving = ref(false)
function confirmRevoke(session: Session): void {
  if (saving.value || session.is_current) return
  Modal.confirm({
    title: `强制退出“${session.username}”？`,
    content: '该会话将立即失效，用户需要重新登录。',
    okText: '强制退出',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      if (saving.value) return
      saving.value = true
      try {
        await revokeSession(session.session_id, session.version)
        await load()
      } catch (failure) {
        error.value = adminError(failure)
      } finally {
        saving.value = false
      }
    }
  })
}
const columns = [
  { title: '账号', key: 'user', width: 180 },
  { title: '会话编号', dataIndex: 'session_id', key: 'session', width: 280 },
  { title: '登录时间', key: 'created', width: 180 },
  { title: '过期时间', key: 'expires', width: 180 },
  { title: '操作', key: 'actions', width: 120 }
]
</script>
<template>
  <AdminPage
    title="在线会话"
    subtitle="查看有效登录会话并撤销异常访问"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :search="search"
    @refresh="load"
    @search="find"
    @page="changePage"
    ><ResourceTable
      :columns="columns"
      :rows="rows"
      row-key="session_id"
      :loading="loading"
      empty-text="暂无在线会话"
      ><template #bodyCell="{ column, record }"
        ><div v-if="column.key === 'user'" class="admin-primary">
          <strong>{{ record.username }}</strong
          ><ATag v-if="record.is_current" color="blue">当前会话</ATag>
        </div>
        <span v-else-if="column.key === 'created'">{{ formatAdminTime(record.created_at) }}</span
        ><span v-else-if="column.key === 'expires'">{{ formatAdminTime(record.expires_at) }}</span
        ><AButton
          v-else-if="column.key === 'actions' && writable"
          type="link"
          danger
          :disabled="record.is_current || saving"
          @click="confirmRevoke(record)"
          >强制退出</AButton
        ></template
      ></ResourceTable
    ></AdminPage
  >
</template>
