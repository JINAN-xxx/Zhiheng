<!-- Adapted from original skill UI, MIT licensed. -->
<template>
  <a-modal
    :open="modelValue && canManage"
    title="上传技能"
    :width="640"
    :mask-closable="!submitting"
    :closable="!submitting"
    destroy-on-close
    :after-close="reset"
    @cancel="close"
  >
    <a-steps
      :current="prepared ? 1 : 0"
      size="small"
      :items="[{ title: '选择文件' }, { title: '确认安装' }]"
    />

    <div v-if="!prepared" class="upload-stage">
      <a-upload-dragger
        :file-list="fileList"
        :before-upload="selectFile"
        :show-upload-list="true"
        :max-count="1"
        accept=".md,.zip"
        @remove="removeFile"
      >
        <p class="upload-icon"><InboxOutlined /></p>
        <h3>拖拽技能包到此处，或点击选择</h3>
        <p>支持 SKILL.md 与 ZIP 技能包</p>
        <small>单个文件最大 10 MB</small>
      </a-upload-dragger>
      <div class="upload-guide">
        <FileTextOutlined />
        <div>
          <strong>技能包要求</strong>
          <p>SKILL.md 需包含技能名称与描述；ZIP 包根目录必须包含 SKILL.md。</p>
          <p>解析后核对技能信息，再确认安装。</p>
        </div>
      </div>
    </div>

    <div v-else class="upload-preview">
      <div class="prepared-heading">
        <CheckCircleOutlined />
        <div>
          <h3>技能解析完成</h3>
          <p>{{ selectedFile?.name }}</p>
        </div>
      </div>
      <a-descriptions :column="1" bordered size="small">
        <a-descriptions-item label="技能名称">{{ prepared.skillName }}</a-descriptions-item>
        <a-descriptions-item label="技能标识"
          ><code>{{ prepared.skillSlug }}</code></a-descriptions-item
        >
        <a-descriptions-item label="版本">{{ prepared.version }}</a-descriptions-item>
        <a-descriptions-item label="说明">{{ prepared.description }}</a-descriptions-item>
        <a-descriptions-item label="文件规模">
          {{ prepared.fileCount }} 个文件，共 {{ formatSkillBytes(prepared.totalBytes) }}
        </a-descriptions-item>
      </a-descriptions>
    </div>

    <AAlert v-if="error" :message="error" type="error" show-icon style="margin-top: 16px" />
    <template #footer>
      <a-button :disabled="submitting" @click="prepared ? back() : close()">{{
        prepared ? '上一步' : '取消'
      }}</a-button>
      <a-button
        v-if="!prepared"
        type="primary"
        :disabled="!selectedFile"
        :loading="submitting"
        @click="prepare"
      >
        解析技能
      </a-button>
      <a-button v-else type="primary" :loading="submitting" @click="install">确认安装</a-button>
    </template>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Descriptions as ADescriptions,
  DescriptionsItem as ADescriptionsItem,
  Modal as AModal,
  Steps as ASteps,
  UploadDragger as AUploadDragger
} from 'ant-design-vue'
import type { UploadProps } from 'ant-design-vue'
import { CheckCircleOutlined, FileTextOutlined, InboxOutlined } from '@ant-design/icons-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { confirmSkillImport, prepareSkillImport } from '../api/index.ts'
import type { PreparedSkill, Skill } from '../api/index.ts'
import { formatSkillBytes } from '../presentation.ts'
const props = defineProps<{ modelValue: boolean; canManage: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; installed: [skill: Skill] }>()
const selectedFile = ref<File | null>(null),
  prepared = ref<PreparedSkill | null>(null),
  submitting = ref(false),
  error = ref('')
const scope = new RequestScope()
const fileList = computed(() =>
  selectedFile.value
    ? [
        {
          uid: selectedFile.value.name,
          name: selectedFile.value.name,
          size: selectedFile.value.size
        }
      ]
    : []
)
const selectFile: NonNullable<UploadProps['beforeUpload']> = (file) => {
  scope.replace()
  prepared.value = null
  error.value = ''
  submitting.value = false
  if (file.name !== 'SKILL.md' && !file.name.toLowerCase().endsWith('.zip')) {
    selectedFile.value = null
    error.value = '仅支持 SKILL.md 或 ZIP 技能包'
    return false
  }
  if (file.size > 10 * 1024 * 1024) {
    selectedFile.value = null
    error.value = '技能包超过 10 MB 大小限制'
    return false
  }
  selectedFile.value = file
  return false
}
function removeFile(): boolean {
  scope.replace()
  submitting.value = false
  selectedFile.value = null
  prepared.value = null
  error.value = ''
  return true
}
async function prepare(): Promise<void> {
  const file = selectedFile.value
  if (!file || !props.canManage || submitting.value) return
  const context = scope.replace()
  submitting.value = true
  error.value = ''
  try {
    const result = await prepareSkillImport(file, context.signal)
    if (context.isCurrent() && props.modelValue) prepared.value = result
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) submitting.value = false
  }
}
async function install(): Promise<void> {
  const draft = prepared.value
  if (!draft || !props.canManage || submitting.value) return
  const context = scope.replace()
  submitting.value = true
  error.value = ''
  try {
    const result = await confirmSkillImport(draft.draftId, context.signal)
    if (context.isCurrent() && props.modelValue) {
      emit('installed', result)
      emit('update:modelValue', false)
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) submitting.value = false
  }
}
function back(): void {
  scope.replace()
  prepared.value = null
  error.value = ''
}
function close(): void {
  if (!submitting.value) emit('update:modelValue', false)
}
function reset(): void {
  if (props.modelValue) return
  scope.replace()
  selectedFile.value = null
  prepared.value = null
  submitting.value = false
  error.value = ''
}
watch(
  () => props.modelValue,
  (open) => {
    if (!open) reset()
  }
)
onBeforeUnmount(() => {
  scope.dispose()
  selectedFile.value = null
  prepared.value = null
})
</script>

<style scoped>
.upload-stage {
  margin-top: 26px;
}
.upload-stage h3 {
  margin: 12px 0 8px;
  font-size: 16px;
  font-weight: 500;
  color: var(--color-text);
}
.upload-guide {
  display: flex;
  gap: 10px;
  margin-top: 18px;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  background: var(--gray-25);
  color: var(--main-700);
}
.upload-guide strong {
  color: var(--color-text);
  font-size: 14px;
  font-weight: 500;
}
.upload-guide p {
  margin-top: 7px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.8;
}
.prepared-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
  padding: 16px;
  border-radius: 7px;
  background: var(--main-50);
  color: var(--main-700);
}
.prepared-heading > span {
  font-size: 25px;
}
.prepared-heading h3 {
  margin: 0 0 5px;
  color: var(--color-text);
  font-size: 16px;
  font-weight: 500;
}
.prepared-heading p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.upload-icon {
  margin-bottom: 8px;
  color: var(--main-color);
  font-size: 38px;
}
.upload-stage p {
  margin: 0 0 4px;
  color: var(--color-text);
}
.upload-stage small {
  color: var(--color-text-secondary);
}
.upload-preview {
  margin-top: 24px;
}
.upload-preview code {
  color: var(--color-text);
  font-family: var(--workspace-font-mono);
}
:deep(.ant-upload-drag) {
  border-color: var(--gray-150);
  background: var(--gray-25);
  padding: 16px;
  border-radius: 8px;
}
:deep(.ant-upload-drag:hover) {
  border-color: var(--main-color);
}
</style>
