<script setup lang="ts">
import { createRequestId } from '../../../shared/requestId.ts'
import { onBeforeUnmount, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Modal as AModal,
  Spin as ASpin
} from 'ant-design-vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { identity } from '../../identity/public.ts'
import { getMigration, migrateBase } from '../api/index.ts'
import type { KnowledgeBase, KnowledgeJob, MigrationPreview } from '../api/index.ts'
const emit = defineEmits<{ accepted: [job: KnowledgeJob]; cleanup: [base: KnowledgeBase] }>()
const visible = ref(false),
  loading = ref(false),
  saving = ref(false),
  uncertain = ref(false),
  confirmed = ref(false),
  error = ref(''),
  preview = ref<MigrationPreview | null>(null),
  base = ref<KnowledgeBase | null>(null)
const scope = new RequestScope(),
  writeScope = new RequestScope()
let key = ''
function close() {
  if (saving.value || uncertain.value) return
  visible.value = false
  scope.replace()
  writeScope.replace()
}
async function open(value: KnowledgeBase) {
  if (!value.can_manage || value.owner_id !== identity.value?.id) return
  base.value = value
  visible.value = true
  loading.value = true
  confirmed.value = false
  error.value = ''
  preview.value = null
  uncertain.value = false
  key = createRequestId()
  const ticket = scope.replace()
  try {
    const result = await getMigration(value.id, ticket.signal)
    if (ticket.isCurrent()) preview.value = result
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
async function submit() {
  if (
    !preview.value ||
    !base.value ||
    !confirmed.value ||
    !preview.value.can_migrate ||
    saving.value
  )
    return
  const ticket = writeScope.replace()
  saving.value = true
  error.value = ''
  try {
    const job = await migrateBase(
      base.value.id,
      {
        expected_version: preview.value.expected_version,
        expected_acl_version: preview.value.acl_version,
        confirm_shared_access: true,
        client_request_id: key
      },
      ticket.signal
    )
    if (ticket.isCurrent()) {
      uncertain.value = false
      visible.value = false
      emit('accepted', job)
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      uncertain.value = !(reason instanceof ApiError) || reason.status >= 500
    }
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}
function cleanup() {
  if (!base.value) return
  visible.value = false
  emit('cleanup', base.value)
}
onBeforeUnmount(() => {
  scope.dispose()
  writeScope.dispose()
})
defineExpose({ open })
</script>
<template>
  <a-modal
    :open="visible"
    title="迁移资料与共享"
    width="680px"
    :mask-closable="false"
    :closable="!saving && !uncertain"
    :keyboard="!saving && !uncertain"
    :confirm-loading="saving"
    :ok-text="uncertain ? '确认原提交' : '确认迁移'"
    :ok-button-props="{ disabled: !confirmed || !preview?.can_migrate || loading }"
    :cancel-button-props="{ disabled: saving || uncertain }"
    cancel-text="取消"
    @cancel="close"
    @ok="submit"
    ><a-spin :spinning="loading"
      ><a-alert v-if="error" :message="error" type="error" show-icon /><a-alert
        v-if="uncertain"
        type="warning"
        message="迁移接纳结果尚未确认。再次确认会沿用同一请求标识。"
        show-icon
      /><template v-if="preview"
        ><p>
          当前共享范围：{{
            preview.sharing.entries.length ? '已有成员' : '仅自己'
          }}。迁移将创建独立资料副本，并在新索引验证完成后切换；失败时保留原可用版本。
        </p>
        <ul class="migration-list">
          <li v-for="doc in preview.documents" :key="doc.document_id">
            {{ doc.file_name }} · {{ doc.can_migrate ? '可迁移' : doc.reason || '需所有者处理' }}
          </li>
        </ul>
        <a-alert
          v-if="preview.requires_acl_cleanup"
          type="warning"
          message="存在旧拒绝或到期规则，需先整理共享范围，再重新预览迁移。"
          show-icon
        /><a-button v-if="preview.requires_acl_cleanup" class="migration-cleanup" @click="cleanup"
          >整理共享规则</a-button
        ><a-alert
          v-else-if="!preview.can_migrate"
          type="warning"
          message="请先处理无法迁移的资料，再重新预览。"
          show-icon
        /><a-checkbox
          v-model:checked="confirmed"
          :disabled="!preview.can_migrate || saving || uncertain"
          >我已核对以上资料，确认迁移后共享用户可查看和下载这些资料，并产生处理用量</a-checkbox
        ></template
      ></a-spin
    ></a-modal
  >
</template>
<style scoped>
p {
  font-size: 13px;
  line-height: 1.8;
  color: var(--color-text-secondary);
}
.migration-list {
  max-height: 260px;
  overflow: auto;
  line-height: 2;
  font-size: 13px;
  padding-left: 20px;
}
.migration-cleanup {
  margin: 12px 0;
}
:deep(.ant-checkbox-wrapper) {
  margin-top: 16px;
  line-height: 1.8;
}
</style>
