<!-- Adapted from original FileTransferDialog, MIT licensed. -->
<template>
  <ResourceDrawer :title="`转移文件 - ${fileName}`" :open="visible" :width="620" @close="close">
    <a-alert v-if="error" :message="error" type="error" show-icon />
    <a-form :model="form" layout="vertical" :disabled="saving">
      <a-form-item label="新所有者" name="ownerUserId">
        <a-select
          :value="form.ownerUserId"
          show-search
          allow-clear
          :filter-option="false"
          :loading="userLoading"
          placeholder="输入用户名称搜索"
          style="width: 100%"
          :options="userSelectOptions"
          @update:value="setOwner"
          @search="searchUsers"
          @dropdown-visible-change="(isOpen) => isOpen && searchUsers('')"
        />
      </a-form-item>
      <a-form-item label="上传人权限">
        <a-switch
          :checked="form.retainUploaderAccess"
          checked-children="保留"
          un-checked-children="移除"
          @update:checked="(value) => (form.retainUploaderAccess = value === true)"
        />
        <div class="form-tip">
          {{
            form.retainUploaderAccess
              ? '原上传人继续拥有内置下载权限，匹配的显式拒绝仍可覆盖。'
              : '原上传人不再因上传身份获得下载权限，上传记录仍会保留。'
          }}
        </div>
      </a-form-item>
      <a-form-item label="转移原因" name="reason">
        <a-textarea
          v-model:value="form.reason"
          :rows="3"
          :maxlength="1000"
          show-count
          placeholder="请输入转移原因"
        />
      </a-form-item>
    </a-form>
    <template #footer>
      <div class="dialog-footer">
        <a-button type="primary" :loading="saving" @click="submit"> 确 定 </a-button>
        <a-button @click="close">取 消</a-button>
      </div>
    </template>
  </ResourceDrawer>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Select as ASelect,
  Switch as ASwitch,
  Textarea as ATextarea
} from 'ant-design-vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { findFileUsers, transferFile } from '../api/index.ts'
import type { FileUser, ManagedFile } from '../api/index.ts'
const emit = defineEmits<{ refresh: [] }>()
const visible = ref(false),
  saving = ref(false),
  userLoading = ref(false),
  error = ref('')
const targets = ref<ManagedFile[]>([]),
  userOptions = ref<FileUser[]>([])
const fileName = computed(() =>
  targets.value.length === 1 ? targets.value[0]!.displayName : `${targets.value.length}个文件`
)
const userSelectOptions = computed(() =>
  userOptions.value.map((user) => ({ label: user.username, value: user.id }))
)
const form = reactive({
  ownerUserId: undefined as string | undefined,
  retainUploaderAccess: false,
  reason: ''
})
const scope = new RequestScope(),
  search = new RequestScope()
function close(): void {
  scope.replace()
  search.replace()
  visible.value = false
  saving.value = false
  userLoading.value = false
  targets.value = []
  userOptions.value = []
}
function open(files: ManagedFile[]): void {
  close()
  targets.value = files.map((file) => ({ ...file }))
  Object.assign(form, { ownerUserId: undefined, retainUploaderAccess: false, reason: '' })
  error.value = ''
  visible.value = true
  void searchUsers('')
}
function setOwner(value: unknown): void {
  form.ownerUserId = typeof value === 'string' ? value : undefined
}
async function searchUsers(keyword: string): Promise<void> {
  const context = search.replace()
  userLoading.value = true
  try {
    const users = await findFileUsers(keyword, context.signal)
    if (context.isCurrent() && visible.value) userOptions.value = users
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) userLoading.value = false
  }
}
async function submit(): Promise<void> {
  const owner = form.ownerUserId,
    reason = form.reason.trim()
  if (saving.value || !targets.value.length) return
  if (!owner || !reason || [...reason].length > 500) {
    error.value = '请选择新所有者并填写不超过500字的转移原因'
    return
  }
  const files = [...targets.value],
    keepUploader = form.retainUploaderAccess,
    context = scope.replace()
  saving.value = true
  error.value = ''
  let changed = 0
  try {
    for (const file of files) {
      context.signal.throwIfAborted()
      await transferFile(file, owner, keepUploader, reason, context.signal)
      if (!context.isCurrent() || !visible.value) return
      changed += 1
    }
    if (context.isCurrent()) {
      emit('refresh')
      close()
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      error.value = `${changed ? `已转移${changed}项；` : ''}${errorMessage(reason)}`
      if (changed) {
        targets.value = files.slice(changed)
        emit('refresh')
      }
    }
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
onBeforeUnmount(() => {
  scope.dispose()
  search.dispose()
})
defineExpose({ open })
</script>

<style scoped>
.form-tip {
  width: 100%;
  margin-top: 4px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
  line-height: 20px;
}
</style>
