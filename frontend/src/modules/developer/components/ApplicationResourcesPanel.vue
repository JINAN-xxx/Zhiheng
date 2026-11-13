<template>
  <section class="resources-panel">
    <WorkspaceState v-if="loading && !snapshot" kind="loading" />
    <WorkspaceState v-else-if="error && !snapshot" kind="error" :message="error" @retry="load" />
    <template v-else-if="snapshot">
      <a-alert
        v-if="error || (formError && !bindingOpen && !quotaOpen)"
        :message="error || formError"
        type="error"
        show-icon
      />
      <div class="resources-heading">
        <div class="resources-heading__copy">
          <strong>资源授权</strong>
          <p>密钥只能在操作能力与应用授权的交集内访问当前有效的个人或明确共享资源。</p>
        </div>
        <div class="resources-heading__actions">
          <a-button v-if="snapshot.canBindResources" type="primary" @click="openBinding()"
            >授权资源</a-button
          >
          <RefreshButton label="刷新资源授权" :loading="loading" @click="load" />
        </div>
      </div>
      <div class="resource-quota">
        <span>原始文件 {{ snapshot.sourceCount }} / {{ snapshot.sourceFileLimit }} 个</span>
        <span
          >逻辑空间 {{ bytes(snapshot.sourceBytes) }} / {{ bytes(snapshot.sourceByteLimit) }}</span
        >
        <a-button v-if="snapshot.canConfigureQuota" type="link" @click="openQuota"
          >修改额度</a-button
        >
        <small
          >回收和解绑不释放额度，实际清理后释放。管理操作按资源请求限速，模型步骤按原积分规则计费。</small
        >
      </div>
      <WorkspaceState
        v-if="!snapshot.items.length"
        kind="empty"
        title="尚未授权资源"
        message="在此授权已有资源，或使用资源 API 创建后自动关联当前应用。"
      />
      <ResourceTable
        v-else
        :columns="columns"
        :rows="snapshot.items"
        :scroll-x="780"
        :row-key="(row: BoundResource) => `${row.kind}:${row.resourceId}`"
      >
        <template #bodyCell="{ column, record }">
          <div v-if="column.key === 'resource'" class="resource-identity">
            <strong>{{ record.displayName }}</strong
            ><code>{{ record.resourceId }}</code>
          </div>
          <template v-else-if="column.key === 'kind'">{{
            kindLabels[record.kind as ResourceKind]
          }}</template>
          <template v-else-if="column.key === 'actions'">{{
            record.actions.map((action: ResourceAction) => actionLabels[action]).join('、')
          }}</template>
          <template v-else-if="column.key === 'status'">{{
            record.available ? (record.owned ? '本人资源' : '共享资源') : '资源已失效'
          }}</template>
          <a-space v-else-if="column.key === 'manage' && snapshot.canBindResources">
            <a-button
              type="link"
              :disabled="!record.available || saving"
              @click="openBinding(record)"
              >编辑</a-button
            >
            <a-popconfirm
              title="取消此应用的资源授权？"
              ok-text="取消授权"
              cancel-text="保留"
              @confirm="remove(record)"
              ><a-button type="link" danger :disabled="saving">取消授权</a-button></a-popconfirm
            >
          </a-space>
        </template>
      </ResourceTable>
    </template>
    <a-modal
      :open="bindingOpen"
      :title="editing ? '编辑资源授权' : '授权资源'"
      :confirm-loading="saving"
      ok-text="保存授权"
      cancel-text="取消"
      @ok="saveBinding"
      @cancel="closeBinding"
    >
      <a-alert
        v-if="formError || candidateError"
        :message="formError || candidateError"
        type="error"
        show-icon
      />
      <a-form layout="vertical">
        <a-form-item label="资源类型"
          ><a-select
            aria-label="资源类型"
            :value="form.kind"
            :options="kindOptions"
            :disabled="editing || saving"
            @change="changeKind"
        /></a-form-item>
        <a-form-item label="资源"
          ><a-select
            aria-label="资源"
            :value="form.resourceId || undefined"
            :loading="candidateLoading"
            :disabled="editing || saving"
            show-search
            option-filter-prop="label"
            placeholder="选择本人或明确共享的资源"
            :options="candidateOptions"
            @change="changeCandidate"
          /><a-button v-if="candidateError" type="link" @click="loadCandidates"
            >重试</a-button
          ></a-form-item
        >
        <a-form-item label="允许动作"
          ><a-checkbox-group
            :value="form.actions"
            :options="actionOptions"
            :disabled="saving || !selected"
            @change="setActions"
        /></a-form-item>
        <p class="resource-hint">仅展示当前可委托的动作。共享资源的写入和删除需由拥有者操作。</p>
      </a-form>
    </a-modal>
    <a-modal
      :open="quotaOpen"
      title="修改应用资源额度"
      :confirm-loading="saving"
      ok-text="保存额度"
      cancel-text="取消"
      @ok="saveQuota"
      @cancel="closeQuota"
    >
      <a-alert v-if="formError" :message="formError" type="error" show-icon />
      <a-form layout="vertical"
        ><a-form-item label="原始文件数量上限"
          ><a-input-number
            aria-label="原始文件数量上限"
            :value="quota.fileLimit"
            :min="1"
            :precision="0"
            @change="(value) => setQuota('fileLimit', value)" /></a-form-item
        ><a-form-item label="原始文件逻辑空间上限（字节）"
          ><a-input-number
            aria-label="原始文件逻辑空间上限（字节）"
            :value="quota.byteLimit"
            :min="1"
            :precision="0"
            style="width: 100%"
            @change="(value) => setQuota('byteLimit', value)" /></a-form-item
      ></a-form>
    </a-modal>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  CheckboxGroup as ACheckboxGroup,
  Form as AForm,
  FormItem as AFormItem,
  InputNumber as AInputNumber,
  Modal as AModal,
  Popconfirm as APopconfirm,
  Select as ASelect,
  Space as ASpace
} from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import type { Application, BoundResource, ResourceAction, ResourceKind } from '../api/index.ts'
import { useResourceAccess } from '../composables/resourceAccess.ts'
const props = defineProps<{ application: Application }>()
const emit = defineEmits<{
  versionChange: [
    version: number,
    limits: { sourceFileLimit: number; sourceByteLimit: number },
    refreshed?: Application
  ]
}>()
const {
  snapshot,
  candidates,
  loading,
  candidateLoading,
  saving,
  error,
  formError,
  candidateError,
  bindingOpen,
  quotaOpen,
  editing,
  form,
  quota,
  selected,
  load,
  loadCandidates,
  openBinding,
  closeBinding,
  changeKind,
  changeCandidate,
  saveBinding,
  remove,
  openQuota,
  closeQuota,
  saveQuota
} = useResourceAccess(
  () => props.application,
  (version, limits, refreshed) => emit('versionChange', version, limits, refreshed)
)
const kindLabels: Record<ResourceKind, string> = {
  file: '文件',
  knowledge_base: '知识库',
  skill: '个人技能'
}
const actionLabels: Record<ResourceAction, string> = {
  read: '读取',
  write: '写入',
  query: '检索',
  delete: '删除'
}
const kindOptions = Object.entries(kindLabels).map(([value, label]) => ({ value, label }))
const candidateOptions = computed(() =>
  candidates.value.map((item) => ({
    value: item.resourceId,
    label: `${item.displayName} · ${item.owned ? '本人' : '共享'} · ${item.resourceId}`
  }))
)
const actionOptions = computed(
  () => selected.value?.allowedActions.map((value) => ({ value, label: actionLabels[value] })) ?? []
)
const columns = [
  { title: '资源', key: 'resource', width: 320 },
  { title: '类型', key: 'kind', width: 90 },
  { title: '授权动作', key: 'actions', width: 150 },
  { title: '当前状态', key: 'status', width: 110 },
  { title: '操作', key: 'manage', width: 170 }
]
function setActions(value: unknown): void {
  form.actions = Array.isArray(value)
    ? value.filter(
        (action: unknown): action is ResourceAction =>
          typeof action === 'string' &&
          selected.value?.allowedActions.some((allowed) => allowed === action) === true
      )
    : []
}
function setQuota(field: 'fileLimit' | 'byteLimit', value: unknown): void {
  quota[field] = typeof value === 'number' ? value : 0
}
function bytes(value: number): string {
  return value < 1024
    ? `${value} B`
    : value < 1048576
      ? `${(value / 1024).toFixed(1)} KiB`
      : `${(value / 1048576).toFixed(1)} MiB`
}
</script>
<style scoped lang="scss">
.resources-panel {
  min-width: 0;
}
.resources-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 18px 22px;
  border-bottom: 1px solid var(--gray-150);
}
.resources-heading__copy {
  flex: 1;
  min-width: 0;
}
.resources-heading__actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
}
.resources-heading strong {
  font-size: 16px;
  font-weight: 500;
}
.resources-heading p,
.resource-hint {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.7;
}
.resource-quota {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 24px;
  padding: 16px 22px;
  background: var(--gray-10);
}
.resource-quota small {
  flex-basis: 100%;
  color: var(--color-text-secondary);
}
.resource-identity {
  display: grid;
  gap: 5px;
  overflow-wrap: anywhere;
}
.resource-identity strong {
  font-weight: 500;
}
.resource-identity code {
  color: var(--color-text-secondary);
  font-size: 12px;
}
@media (max-width: 760px) {
  .resources-heading,
  .resource-quota {
    padding: 16px;
  }
  .resources-heading__copy {
    flex-basis: 100%;
  }
  .resources-heading__actions {
    justify-content: flex-end;
    width: 100%;
  }
}
</style>
