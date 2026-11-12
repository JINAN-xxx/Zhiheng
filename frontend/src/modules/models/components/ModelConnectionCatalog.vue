<script setup lang="ts">
import { copyText } from '../../../shared/clipboard.ts'
import { computed, ref } from 'vue'
import {
  Button as AButton,
  Dropdown as ADropdown,
  Input as AInput,
  Menu as AMenu,
  MenuItem as AMenuItem,
  message
} from 'ant-design-vue'
import { ApiOutlined, CopyOutlined, EllipsisOutlined, SearchOutlined } from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import type { ChatModel, ModelConnection } from '../api/index.ts'

const props = defineProps<{
  connections: ModelConnection[]
  models: ChatModel[]
  loading: boolean
  canWrite: boolean
  busy: boolean
}>()
const emit = defineEmits<{
  edit: [connection: ModelConnection]
  control: [connection: ModelConnection, action: 'enable' | 'disable' | 'delete']
}>()
const search = ref('')
const filtered = computed(() => {
  const query = search.value.trim().toLowerCase()
  return props.connections.filter((connection) =>
    (connection.name + ' ' + connection.base_url).toLowerCase().includes(query)
  )
})
const modelCounts = computed(() => {
  const counts: Record<string, number> = {}
  for (const model of props.models)
    counts[model.connection_id] = (counts[model.connection_id] ?? 0) + 1
  return counts
})
function status(connection: ModelConnection) {
  return !connection.has_api_key ? '待配置密钥' : connection.enabled ? '已启用' : '已停用'
}
function edit(connection: ModelConnection) {
  if (props.canWrite && !props.busy) emit('edit', connection)
}
function control(connection: ModelConnection, action: 'enable' | 'disable' | 'delete') {
  if (props.canWrite && !props.busy) emit('control', connection, action)
}
async function copyAddress(connection: ModelConnection) {
  try {
    await copyText(connection.base_url)
    void message.success('API 地址已复制')
  } catch {
    void message.info('暂时无法复制，请选中 API 地址手动复制')
  }
}
</script>
<template>
  <section class="credential-catalog" aria-label="凭据列表">
    <div class="credential-toolbar">
      <span class="credential-total">{{
        search.trim() ? '找到 ' + filtered.length + ' 个匹配' : connections.length + ' 个凭据'
      }}</span>
      <AInput
        v-model:value="search"
        allow-clear
        placeholder="搜索凭据名称或 API 地址"
        aria-label="搜索凭据"
      >
        <template #prefix><SearchOutlined aria-hidden="true" /></template>
      </AInput>
    </div>
    <WorkspaceState v-if="loading && !connections.length" kind="loading" title="正在加载凭据" />
    <ul v-else-if="filtered.length" class="credential-grid">
      <li
        v-for="connection in filtered"
        :key="connection.id"
        class="credential-card"
        :aria-label="'凭据：' + connection.name"
      >
        <header class="credential-heading">
          <span class="credential-icon" aria-hidden="true"><ApiOutlined /></span>
          <h2 :title="connection.name">{{ connection.name }}</h2>
          <span
            class="credential-status"
            :class="{
              'is-enabled': connection.enabled && connection.has_api_key,
              'is-warning': !connection.has_api_key
            }"
            ><i aria-hidden="true" />{{ status(connection) }}</span
          >
        </header>
        <div class="credential-endpoint">
          <code :title="connection.base_url">{{ connection.base_url }}</code>
          <AButton
            type="text"
            size="small"
            :aria-label="'复制 API 地址：' + connection.name"
            title="复制 API 地址"
            @click="copyAddress(connection)"
            ><CopyOutlined aria-hidden="true"
          /></AButton>
        </div>
        <footer class="credential-footer">
          <span class="credential-models">{{
            modelCounts[connection.id] ? modelCounts[connection.id] + ' 个关联模型' : '未关联模型'
          }}</span>
          <div v-if="canWrite" class="credential-actions">
            <AButton
              type="text"
              size="small"
              aria-label="编辑凭据"
              :disabled="busy"
              @click="edit(connection)"
              >编辑</AButton
            >
            <ADropdown :trigger="['click']" :disabled="busy">
              <AButton type="text" size="small" aria-label="更多凭据操作" :disabled="busy"
                ><EllipsisOutlined aria-hidden="true"
              /></AButton>
              <template #overlay>
                <AMenu>
                  <AMenuItem
                    :disabled="busy"
                    @click="control(connection, connection.enabled ? 'disable' : 'enable')"
                    >{{ connection.enabled ? '停用' : '启用' }}</AMenuItem
                  >
                  <AMenuItem :disabled="busy" danger @click="control(connection, 'delete')"
                    >删除凭据</AMenuItem
                  >
                </AMenu>
              </template>
            </ADropdown>
          </div>
        </footer>
      </li>
    </ul>
    <WorkspaceState
      v-else
      :title="search.trim() ? '没有匹配的凭据' : '暂无服务商凭据'"
      :message="
        search.trim()
          ? '试试其他名称或 API 地址。'
          : canWrite
            ? '点击新增凭据，保存模型服务的 API 地址和密钥。'
            : '当前暂无服务商凭据。'
      "
    />
  </section>
</template>
<style scoped>
.credential-catalog {
  min-width: 0;
}
.credential-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 4px 0 18px;
}
.credential-total {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.credential-toolbar :deep(.ant-input-affix-wrapper) {
  width: 288px;
  height: 34px;
  font-size: 13px;
}
.credential-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.credential-card {
  min-width: 0;
  padding: 20px;
  border-radius: 10px;
  background: var(--bg-container, #fff);
}
.credential-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.credential-icon {
  display: grid;
  place-items: center;
  flex: 0 0 36px;
  width: 36px;
  height: 36px;
  border-radius: 9px;
  background: var(--main-50);
  color: var(--main-color);
  font-size: 20px;
}
.credential-heading h2 {
  flex: 1;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 15px;
  font-weight: 600;
  line-height: 24px;
  color: var(--color-text);
}
.credential-status {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  font-size: 12px;
  color: var(--color-text-secondary);
}
.credential-status i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--gray-400);
}
.credential-status.is-enabled i {
  background: #3da66a;
}
.credential-status.is-warning {
  color: #b77719;
}
.credential-status.is-warning i {
  background: #d3922e;
}
.credential-endpoint {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin: 14px 0 16px;
}
.credential-endpoint code {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--workspace-font-mono);
  font-size: 12px;
  color: var(--color-text-secondary);
  user-select: all;
}
.credential-endpoint :deep(.ant-btn) {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  padding: 0;
  color: var(--color-text-secondary);
}
.credential-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 26px;
}
.credential-models {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.credential-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.credential-actions :deep(.ant-btn) {
  height: 26px;
  font-size: 12px;
  color: var(--color-text-secondary);
}
.credential-actions :deep(.ant-btn:hover) {
  color: var(--main-color);
}
@media (max-width: 1100px) {
  .credential-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 640px) {
  .credential-toolbar {
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }
  .credential-toolbar :deep(.ant-input-affix-wrapper) {
    width: 100%;
  }
  .credential-card {
    padding: 16px;
  }
  .credential-heading {
    gap: 10px;
  }
  .credential-icon {
    width: 32px;
    height: 32px;
    flex-basis: 32px;
    font-size: 18px;
  }
  .credential-heading h2 {
    font-size: 14px;
  }
}
</style>
