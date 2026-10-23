<!-- Adapted from original skill UI, MIT licensed. -->
<template>
  <a-drawer
    :open="modelValue"
    title="技能详情"
    width="min(680px, 100%)"
    destroy-on-close
    root-class-name="skill-detail-drawer"
    @close="emit('update:modelValue', false)"
  >
    <WorkspaceState v-if="loading" kind="loading" />
    <WorkspaceState v-else-if="error" kind="error" :message="error" @retry="load" />
    <template v-else-if="detail">
      <div class="detail-heading">
        <div>
          <h2>{{ detail.skillName }}</h2>
          <code>{{ detail.skillSlug }}</code>
        </div>
        <WorkspaceStatus
          :status="detail.status === 'enabled' ? 'active' : 'warning'"
          :label="skillStatusLabel(detail.status)"
        />
      </div>

      <a-descriptions :column="1" bordered size="small">
        <a-descriptions-item label="说明">{{ detail.description || '-' }}</a-descriptions-item>
        <a-descriptions-item label="版本">{{ detail.version || '-' }}</a-descriptions-item>
        <a-descriptions-item label="来源">{{
          detail.sourceType === 'builtin' ? '平台内置' : '管理员上传'
        }}</a-descriptions-item>
      </a-descriptions>

      <section class="content-section">
        <h3>技能内容</h3>
        <pre>{{ content || '暂无内容' }}</pre>
      </section>

      <AAlert
        v-if="actionError"
        :message="actionError"
        type="error"
        show-icon
        style="margin-top: 16px"
      />
      <div v-if="canEdit || canRemove" class="detail-actions">
        <a-button v-if="canEdit" :loading="saving" @click="toggleStatus">
          {{ detail.status === 'enabled' ? '停用技能' : '重新启用' }}
        </a-button>
        <a-tooltip
          v-if="canRemove && detail.sourceType !== 'builtin'"
          :title="detail.status === 'enabled' ? '请先停用技能再删除' : '删除技能'"
        >
          <a-button
            danger
            :disabled="detail.status === 'enabled'"
            :loading="deleting"
            @click="remove"
            >删除技能</a-button
          >
        </a-tooltip>
      </div>
    </template>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Descriptions as ADescriptions,
  DescriptionsItem as ADescriptionsItem,
  Drawer as ADrawer,
  Modal,
  Tooltip as ATooltip
} from 'ant-design-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceStatus from '../../../shared/components/WorkspaceStatus.vue'
import { deleteSkill, getSkill, getSkillContent, updateSkillStatus } from '../api/index.ts'
import type { Skill } from '../api/index.ts'
import { skillStatusLabel } from '../presentation.ts'
const props = defineProps<{ modelValue: boolean; skillId: string; canManage: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  changed: [skill: Skill]
  deleted: [id: string]
}>()
const detail = ref<Skill | null>(null),
  content = ref(''),
  loading = ref(false),
  saving = ref(false),
  deleting = ref(false),
  error = ref(''),
  actionError = ref('')
const canEdit = computed(() => props.canManage),
  canRemove = canEdit
const readScope = new RequestScope(),
  mutationScope = new RequestScope()
let closeConfirmation: (() => void) | undefined
async function load(): Promise<void> {
  const id = props.skillId
  if (!props.modelValue || !id) return
  const context = readScope.replace()
  loading.value = true
  error.value = ''
  try {
    const [item, text] = await Promise.all([
      getSkill(id, context.signal),
      getSkillContent(id, context.signal)
    ])
    if (context.isCurrent()) {
      detail.value = item
      content.value = text
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
async function toggleStatus(): Promise<void> {
  const item = detail.value
  if (!item || !props.canManage || saving.value || deleting.value) return
  const context = mutationScope.replace()
  saving.value = true
  actionError.value = ''
  try {
    const result = await updateSkillStatus(
      item.skillId,
      item.status === 'enabled' ? 'disabled' : 'enabled',
      item.lockVersion,
      context.signal
    )
    if (context.isCurrent()) {
      readScope.replace()
      detail.value = result
      emit('changed', result)
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) actionError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
async function remove(): Promise<void> {
  const item = detail.value
  if (
    !item ||
    item.status !== 'disabled' ||
    item.sourceType === 'builtin' ||
    !props.canManage ||
    deleting.value ||
    saving.value
  )
    return
  const context = mutationScope.replace()
  const accepted = await new Promise<boolean>((resolve) => {
    closeConfirmation?.()
    const dialog = Modal.confirm({
      title: `确认删除技能“${item.skillName}”？删除后不可恢复。`,
      okText: '确定',
      cancelText: '取消',
      onOk: () => {
        closeConfirmation = undefined
        resolve(true)
      },
      onCancel: () => {
        closeConfirmation = undefined
        resolve(false)
      }
    })
    closeConfirmation = () => {
      closeConfirmation = undefined
      dialog.destroy()
      resolve(false)
    }
  })
  if (!accepted || !context.isCurrent()) return
  deleting.value = true
  actionError.value = ''
  try {
    await deleteSkill(item.skillId, context.signal)
    if (context.isCurrent()) {
      emit('deleted', item.skillId)
      emit('update:modelValue', false)
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) actionError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) deleting.value = false
  }
}
watch(
  () => [props.modelValue, props.skillId],
  () => {
    closeConfirmation?.()
    readScope.replace()
    mutationScope.replace()
    detail.value = null
    content.value = ''
    error.value = ''
    actionError.value = ''
    saving.value = false
    deleting.value = false
    loading.value = false
    if (props.modelValue) void load()
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  closeConfirmation?.()
  readScope.dispose()
  mutationScope.dispose()
  detail.value = null
  content.value = ''
})
</script>

<style scoped>
.detail-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.detail-heading h2 {
  margin: 0 0 4px;
  color: var(--color-text);
  font-size: 18px;
}
.detail-heading code {
  color: var(--color-text-secondary);
  font-family: var(--workspace-font-mono);
  font-size: 12px;
}
.content-section {
  margin-top: 22px;
}
.content-section h3 {
  margin: 0 0 10px;
  color: var(--color-text);
  font-size: 14px;
}
.content-section pre {
  max-height: 360px;
  margin: 0;
  overflow: auto;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
  color: var(--color-text);
  font-family: var(--workspace-font-mono);
  font-size: 12px;
  white-space: pre-wrap;
}
.detail-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--gray-150);
}
:global(.skill-detail-drawer .ant-drawer-content),
:global(.skill-detail-drawer .ant-drawer-header) {
  background: var(--bg-elevated);
}
:global(.skill-detail-drawer .ant-drawer-header) {
  border-bottom-color: var(--gray-150);
}
</style>
