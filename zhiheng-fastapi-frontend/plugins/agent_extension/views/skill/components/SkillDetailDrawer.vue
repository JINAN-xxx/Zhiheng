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
        <div><h2>{{ detail.skillName }}</h2><code>{{ detail.skillSlug }}</code></div>
        <WorkspaceStatus :status="detail.status === 'enabled' ? 'active' : 'warning'" :label="skillStatusLabel(detail.status)" />
      </div>

      <a-descriptions :column="1" bordered size="small">
        <a-descriptions-item label="说明">{{ detail.description || '-' }}</a-descriptions-item>
        <a-descriptions-item label="版本">{{ detail.version || '-' }}</a-descriptions-item>
        <a-descriptions-item label="来源">{{ detail.sourceType === 'builtin' ? '平台内置' : '管理员上传' }}</a-descriptions-item>
      </a-descriptions>

      <section class="content-section">
        <h3>技能内容</h3>
        <pre>{{ content || '暂无内容' }}</pre>
      </section>

      <div v-if="canEdit || canRemove" class="detail-actions">
        <a-button v-if="canEdit" :loading="saving" @click="toggleStatus">
          {{ detail.status === 'enabled' ? '停用技能' : '重新启用' }}
        </a-button>
        <a-tooltip v-if="canRemove && detail.sourceType !== 'builtin'" :title="detail.status === 'enabled' ? '请先停用技能再删除' : '删除技能'">
          <a-button danger :disabled="detail.status === 'enabled'" :loading="deleting" @click="remove">删除技能</a-button>
        </a-tooltip>
      </div>
    </template>
  </a-drawer>
</template>

<script setup>
import feedback from '@/plugins/antdFeedback'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceStatus from '@/components/Workspace/WorkspaceStatus.vue'
import { deleteSkill, getSkill, getSkillContent, updateSkillStatus } from '../../../api/skill.js'
import { hasSkillPermission, normalizeSkill, skillStatusLabel } from '../../../utils/skill.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  skillId: { type: String, default: '' },
  permissions: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'changed', 'deleted'])
const detail = ref(null)
const content = ref('')
const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const error = ref('')
const canEdit = computed(() => hasSkillPermission(props.permissions, 'agent_extension:skill:edit'))
const canRemove = computed(() => hasSkillPermission(props.permissions, 'agent_extension:skill:remove'))

async function load() {
  if (!props.modelValue || !props.skillId) return
  loading.value = true
  error.value = ''
  try {
    const [detailResponse, contentResponse] = await Promise.all([getSkill(props.skillId), getSkillContent(props.skillId)])
    detail.value = normalizeSkill(detailResponse?.data)
    content.value = contentResponse?.data?.content || ''
  } catch (reason) {
    error.value = normalizeErrorMessage(reason, '技能详情加载失败')
  } finally {
    loading.value = false
  }
}

async function toggleStatus() {
  if (!detail.value || !canEdit.value) return
  saving.value = true
  try {
    const status = detail.value.status === 'enabled' ? 'disabled' : 'enabled'
    const response = await updateSkillStatus(detail.value.skillId, {
      status,
      expectedVersion: detail.value.lockVersion,
    })
    detail.value = normalizeSkill(response?.data)
    feedback.msgSuccess(status === 'enabled' ? '技能已启用' : '技能已停用')
    emit('changed', detail.value)
  } catch (reason) {
    feedback.msgError(normalizeErrorMessage(reason, '技能状态更新失败'))
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (!detail.value || detail.value.status !== 'disabled' || !canRemove.value) return
  try {
    await feedback.confirm(`确认删除技能“${detail.value.skillName}”？删除后不可恢复。`)
  } catch {
    return
  }
  deleting.value = true
  try {
    await deleteSkill(detail.value.skillId)
    feedback.msgSuccess('技能已删除')
    emit('deleted', detail.value.skillId)
    emit('update:modelValue', false)
  } catch (reason) {
    feedback.msgError(normalizeErrorMessage(reason, '技能删除失败'))
  } finally {
    deleting.value = false
  }
}

watch(() => [props.modelValue, props.skillId], load, { immediate: true })
</script>

<style scoped>
.detail-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.detail-heading h2 { margin: 0 0 4px; color: var(--color-text); font-size: 18px; }
.detail-heading code { color: var(--color-text-secondary); font-family: var(--workspace-font-mono); font-size: 12px; }
.content-section { margin-top: 22px; }
.content-section h3 { margin: 0 0 10px; color: var(--color-text); font-size: 14px; }
.content-section pre { max-height: 360px; margin: 0; overflow: auto; padding: 14px; border: 1px solid var(--gray-150); border-radius: 6px; background: var(--gray-25); color: var(--color-text); font-family: var(--workspace-font-mono); font-size: 12px; white-space: pre-wrap; }
.detail-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--gray-150); }
:global(.skill-detail-drawer .ant-drawer-content), :global(.skill-detail-drawer .ant-drawer-header) { background: var(--bg-elevated); }
:global(.skill-detail-drawer .ant-drawer-header) { border-bottom-color: var(--gray-150); }
</style>

