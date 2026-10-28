<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton, Tag as ATag } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { RequestScope, errorMessage, isAborted } from '../../../shared/http.ts'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import WorkspacePanel from '../../../shared/components/WorkspacePanel.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { getMonitor } from '../api/monitor.ts'
import type { Monitor } from '../api/monitor.ts'
const data = ref<Monitor | null>(null),
  loading = ref(false),
  error = ref(''),
  collectedAt = ref('')
const scope = new RequestScope()
async function load(): Promise<void> {
  const ticket = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await getMonitor(ticket.signal)
    if (ticket.isCurrent()) {
      data.value = result
      collectedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
    }
  } catch (failure) {
    if (ticket.isCurrent() && !isAborted(failure)) error.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
function bytes(value: number | null): string {
  if (value === null) return '不可用'
  if (value < 1024) return `${value} B`
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1073741824) return `${(value / 1048576).toFixed(1)} MB`
  return `${(value / 1073741824).toFixed(1)} GB`
}
const measure = (value: string | number | null, suffix = ''): string =>
  value === null ? '不可用' : `${value}${suffix}`
onMounted(load)
onBeforeUnmount(() => scope.dispose())
</script>
<template>
  <section class="admin-page">
    <WorkspaceToolbar
      title="系统状态"
      :subtitle="collectedAt ? '采集时间：' + collectedAt : '查看平台及基础服务当前状态'"
      ><template #actions
        ><AButton :loading="loading" @click="load"
          ><ReloadOutlined aria-hidden="true" />刷新</AButton
        ></template
      ></WorkspaceToolbar
    >
    <div class="admin-page__body">
      <AAlert v-if="error" :message="error" type="error" show-icon /><WorkspaceState
        v-if="loading && !data"
        kind="loading"
      /><WorkspaceState v-else-if="!data" kind="error" :message="error" @retry="load" />
      <div v-else class="monitor-grid">
        <WorkspacePanel title="平台"
          ><dl>
            <dt>操作系统</dt>
            <dd>{{ measure(data.platform.system) }}</dd>
            <dt>Python 版本</dt>
            <dd>{{ measure(data.platform.python_version) }}</dd>
            <dt>CPU 核数</dt>
            <dd>{{ measure(data.platform.cpu_count) }}</dd>
            <dt>进程内存</dt>
            <dd>{{ bytes(data.platform.rss_bytes) }}</dd>
            <dt>磁盘使用</dt>
            <dd>
              {{ bytes(data.platform.disk_used_bytes) }} /
              {{ bytes(data.platform.disk_total_bytes) }}
            </dd>
            <dt>数据库版本标识</dt>
            <dd>{{ measure(data.schema_version) }}</dd>
          </dl></WorkspacePanel
        ><WorkspacePanel title="数据库"
          ><template #header
            ><ATag :color="data.database.status === 'ok' ? 'green' : 'red'">{{
              data.database.status === 'ok' ? '正常' : '不可用'
            }}</ATag></template
          >
          <dl>
            <dt>数据库版本</dt>
            <dd>{{ measure(data.database.version) }}</dd>
            <dt>数据库大小</dt>
            <dd>{{ bytes(data.database.size_bytes) }}</dd>
            <dt>当前连接数</dt>
            <dd>{{ measure(data.database.connections) }}</dd>
          </dl></WorkspacePanel
        ><WorkspacePanel title="Redis"
          ><template #header
            ><ATag :color="data.redis.status === 'ok' ? 'green' : 'red'">{{
              data.redis.status === 'ok' ? '正常' : '不可用'
            }}</ATag></template
          >
          <dl>
            <dt>内存使用</dt>
            <dd>{{ bytes(data.redis.used_memory_bytes) }}</dd>
            <dt>连接客户端</dt>
            <dd>{{ measure(data.redis.connected_clients) }}</dd>
            <dt>运行时长</dt>
            <dd>{{ measure(data.redis.uptime_seconds, ' 秒') }}</dd>
            <dt>当前库键数</dt>
            <dd>{{ measure(data.redis.db_keys) }}</dd>
          </dl></WorkspacePanel
        ><WorkspacePanel title="Worker"
          ><template #header
            ><ATag :color="data.worker.status === 'ok' ? 'green' : 'red'">{{
              data.worker.status === 'ok' ? '正常' : '不可用'
            }}</ATag></template
          >
          <dl>
            <dt>最近心跳</dt>
            <dd>{{ measure(data.worker.heartbeat_age_seconds, ' 秒前') }}</dd>
          </dl></WorkspacePanel
        >
      </div>
    </div>
  </section>
</template>
<style scoped>
.monitor-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 32px 40px;
}
.monitor-grid dl {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: 14px 16px;
  margin: 0;
  font-size: 13px;
}
.monitor-grid dt {
  color: var(--color-text-secondary);
}
.monitor-grid dd {
  margin: 0;
  overflow-wrap: anywhere;
}
@media (max-width: 1100px) {
  .monitor-grid {
    grid-template-columns: 1fr;
  }
}
</style>
