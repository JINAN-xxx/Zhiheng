<template>
  <a-modal
    :open="modelValue"
    title="上传技能"
    :width="560"
    :mask-closable="!submitting"
    :closable="!submitting"
    destroy-on-close
    @cancel="close"
    @after-close="reset"
  >
    <a-steps :current="prepared ? 1 : 0" size="small" :items="[{ title: '选择文件' }, { title: '确认安装' }]" />

    <div v-if="!prepared" class="upload-stage">
      <a-upload-dragger
        :file-list="selectedFile ? [selectedFile] : []"
        :before-upload="selectFile"
        :show-upload-list="true"
        :max-count="1"
        accept=".md,.zip"
        @remove="removeFile"
      >
        <p class="upload-icon"><InboxOutlined /></p>
        <p>选择 SKILL.md 或技能压缩包</p>
        <small>单个文件最大 10 MB，压缩包根目录必须包含 SKILL.md</small>
      </a-upload-dragger>
    </div>

    <a-descriptions v-else class="upload-preview" :column="1" bordered size="small">
      <a-descriptions-item label="技能名称">{{ prepared.skillName }}</a-descriptions-item>
      <a-descriptions-item label="技能标识"><code>{{ prepared.skillSlug }}</code></a-descriptions-item>
      <a-descriptions-item label="版本">{{ prepared.version }}</a-descriptions-item>
      <a-descriptions-item label="说明">{{ prepared.description }}</a-descriptions-item>
      <a-descriptions-item label="文件规模">
        {{ prepared.fileCount }} 个文件，共 {{ formatSkillBytes(prepared.totalBytes) }}
      </a-descriptions-item>
    </a-descriptions>

    <template #footer>
      <a-button :disabled="submitting" @click="prepared ? back() : close()">{{ prepared ? '上一步' : '取消' }}</a-button>
      <a-button v-if="!prepared" type="primary" :disabled="!selectedFile" :loading="submitting" @click="prepare">
        解析技能
      </a-button>
      <a-button v-else type="primary" :loading="submitting" @click="install">确认安装</a-button>
    </template>
  </a-modal>
</template>

<script setup>
import { InboxOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import { confirmSkillImport, prepareSkillImport } from '../../../api/skill.js'
import { formatSkillBytes } from '../../../utils/skill.js'

defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'installed'])
const selectedFile = ref(null)
const prepared = ref(null)
const submitting = ref(false)

function selectFile(file) {
  if (file.name !== 'SKILL.md' && !file.name.toLowerCase().endsWith('.zip')) {
    feedback.msgError('仅支持 SKILL.md 或 ZIP 技能包')
    return false
  }
  selectedFile.value = file
  return false
}

function removeFile() {
  selectedFile.value = null
  return true
}

async function prepare() {
  if (!selectedFile.value) return
  submitting.value = true
  try {
    const response = await prepareSkillImport(selectedFile.value)
    prepared.value = response?.data || null
  } catch (error) {
    feedback.msgError(normalizeErrorMessage(error, '技能解析失败'))
  } finally {
    submitting.value = false
  }
}

async function install() {
  if (!prepared.value?.draftId) return
  submitting.value = true
  try {
    const response = await confirmSkillImport(prepared.value.draftId)
    feedback.msgSuccess('技能已安装')
    emit('installed', response?.data)
    emit('update:modelValue', false)
  } catch (error) {
    feedback.msgError(normalizeErrorMessage(error, '技能安装失败'))
  } finally {
    submitting.value = false
  }
}

function back() {
  prepared.value = null
}

function close() {
  if (!submitting.value) emit('update:modelValue', false)
}

function reset() {
  selectedFile.value = null
  prepared.value = null
  submitting.value = false
}
</script>

<style scoped>
.upload-stage { margin-top: 24px; }
.upload-icon { margin-bottom: 8px; color: var(--main-color); font-size: 28px; }
.upload-stage p { margin: 0 0 4px; color: var(--color-text); }
.upload-stage small { color: var(--color-text-secondary); }
.upload-preview { margin-top: 24px; }
.upload-preview code { color: var(--color-text); font-family: var(--workspace-font-mono); }
:deep(.ant-upload-drag) { border-color: var(--gray-150); background: var(--bg-container); }
:deep(.ant-upload-drag:hover) { border-color: var(--main-color); }
</style>
