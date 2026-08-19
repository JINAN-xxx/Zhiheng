<template>
  <el-form ref="formRef" :model="form" :rules="rules" label-position="top" class="engine-form">
    <el-form-item v-if="!quick" label="凭据名称" prop="credentialName">
      <el-input
        v-model="form.credentialName"
        :disabled="mode === 'rotate'"
        maxlength="128"
        placeholder="例如 openai-production"
      />
    </el-form-item>
    <el-form-item v-if="!quick" label="Provider" prop="provider">
      <el-select
        v-model="form.provider"
        :disabled="mode === 'rotate'"
        filterable
        placeholder="选择 Provider"
        style="width: 100%"
      >
        <el-option
          v-for="item in providers"
          :key="item.provider"
          :label="item.displayName"
          :value="item.provider"
        />
      </el-select>
    </el-form-item>

    <el-divider content-position="left">凭据字段</el-divider>
    <el-empty v-if="form.provider && currentFields.length === 0" description="该 Provider 无需额外凭据" :image-size="56" />
    <el-form-item
      v-for="field in currentFields"
      :key="field.key"
      :label="field.label"
      :prop="`values.${field.key}`"
      :rules="field.required ? [{ required: true, message: `${field.label}不能为空`, trigger: 'blur' }] : []"
    >
      <template #label>
        <span>{{ field.label }}</span>
        <el-tooltip v-if="field.tooltip" :content="field.tooltip" placement="top">
          <el-icon class="field-help"><QuestionFilled /></el-icon>
        </el-tooltip>
      </template>
      <el-select
        v-if="field.fieldType === 'select'"
        v-model="form.values[field.key]"
        :placeholder="field.placeholder || `选择${field.label}`"
        clearable
        style="width: 100%"
      >
        <el-option v-for="option in field.options" :key="String(option)" :label="String(option)" :value="String(option)" />
      </el-select>
      <el-input
        v-else-if="field.fieldType === 'textarea'"
        v-model="form.values[field.key]"
        type="textarea"
        :rows="4"
        :placeholder="field.placeholder || `输入${field.label}`"
      />
      <div v-else-if="field.fieldType === 'upload'" class="upload-field">
        <el-upload
          accept=".json,.pem,.txt,.key,.crt,.cer,.yaml,.yml,.conf,.config,application/json,text/plain,text/yaml,application/x-pem-file,application/yaml"
          :auto-upload="false"
          :show-file-list="false"
          :on-change="(file) => readCredentialFile(field.key, file)"
        >
          <el-button :icon="Upload">选择文件</el-button>
        </el-upload>
        <span v-if="fileNames[field.key]" class="file-name">{{ fileNames[field.key] }}</span>
      </div>
      <el-input
        v-else
        v-model="form.values[field.key]"
        :type="field.fieldType === 'password' ? 'password' : 'text'"
        :show-password="field.fieldType === 'password'"
        autocomplete="new-password"
        :placeholder="field.placeholder || `输入${field.label}`"
      />
    </el-form-item>

    <div class="form-actions">
      <el-button @click="$emit('cancel')">取消</el-button>
      <el-button type="primary" :loading="submitting" :icon="Check" @click="submit">
        {{ mode === 'rotate' ? '确认轮换' : (quick ? '保存并继续' : '创建凭据') }}
      </el-button>
    </div>
  </el-form>
</template>

<script setup>
import { Check, QuestionFilled, Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  buildCredentialValues, resolveProvider, validateCredentialFileMetadata, validateCredentialFileText,
} from '../../../utils/modelEngine.js'

const props = defineProps({
  providers: { type: Array, default: () => [] },
  mode: { type: String, default: 'create' },
  credentialName: { type: String, default: '' },
  provider: { type: String, default: '' },
  quick: { type: Boolean, default: false },
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['submit', 'cancel'])
const formRef = ref()
const fileNames = reactive({})
const form = reactive({ credentialName: '', provider: '', values: {} })
let fileScopeGeneration = 0
const fileFieldGenerations = new Map()

const rules = {
  credentialName: [
    { required: true, message: '凭据名称不能为空', trigger: 'blur' },
    { pattern: /^(?!\.{1,2}$)[A-Za-z0-9._-]{1,128}$/, message: '仅支持字母、数字、点、下划线和短横线，且不能仅为点', trigger: 'blur' },
  ],
  provider: [{ required: true, message: '请选择 Provider', trigger: 'change' }],
}

const currentProvider = computed(() => resolveProvider(props.providers, form.provider))
const currentFields = computed(() => currentProvider.value?.fields || [])

function resetForm() {
  invalidateFileReads()
  form.credentialName = props.credentialName
  form.provider = resolveProvider(props.providers, props.provider)?.provider || props.provider
  form.values = {}
  Object.keys(fileNames).forEach((key) => delete fileNames[key])
  for (const field of currentFields.value) {
    form.values[field.key] = field.defaultValue == null ? '' : String(field.defaultValue)
  }
  nextTick(() => formRef.value?.clearValidate())
}

watch(() => [props.credentialName, props.provider, props.mode], resetForm, { immediate: true })
watch(
  () => form.provider,
  (provider, previous) => {
    if (provider === previous) return
    invalidateFileReads()
    form.values = {}
    Object.keys(fileNames).forEach((key) => delete fileNames[key])
    for (const field of currentFields.value) {
      form.values[field.key] = field.defaultValue == null ? '' : String(field.defaultValue)
    }
  },
)

function invalidateFileReads() {
  fileScopeGeneration += 1
  fileFieldGenerations.clear()
}

async function readCredentialFile(key, uploadFile) {
  const raw = uploadFile?.raw
  const scopeGeneration = fileScopeGeneration
  const fieldGeneration = (fileFieldGenerations.get(key) || 0) + 1
  fileFieldGenerations.set(key, fieldGeneration)
  const isCurrent = () => (
    scopeGeneration === fileScopeGeneration
    && fileFieldGenerations.get(key) === fieldGeneration
    && currentFields.value.some((field) => field.key === key)
  )
  form.values[key] = ''
  delete fileNames[key]
  const metadataError = validateCredentialFileMetadata(raw)
  if (metadataError) {
    ElMessage.error(metadataError)
    return
  }
  if (!raw || typeof raw.text !== 'function') {
    ElMessage.error('凭据文件读取失败')
    return
  }
  try {
    const value = await raw.text()
    if (!isCurrent()) return
    const contentError = validateCredentialFileText(value)
    if (contentError) {
      ElMessage.error(contentError)
      return
    }
    form.values[key] = value
    fileNames[key] = raw.name || uploadFile?.name || '已选择文件'
  } catch {
    if (!isCurrent()) return
    form.values[key] = ''
    delete fileNames[key]
    ElMessage.error('凭据文件读取失败')
  }
}

async function submit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  const values = buildCredentialValues(currentFields.value, form.values)
  if (Object.keys(values).length === 0) {
    ElMessage.error('请至少填写一项凭据值')
    return
  }
  emit('submit', {
    credentialName: form.credentialName,
    provider: form.provider,
    values,
  })
}

onBeforeUnmount(invalidateFileReads)
</script>

<style scoped>
.engine-form { padding: 0 4px 16px; }
.field-help { margin-left: 5px; color: var(--el-text-color-secondary); vertical-align: -2px; }
.upload-field { display: flex; min-width: 0; align-items: center; gap: 10px; }
.file-name { min-width: 0; overflow: hidden; color: var(--el-text-color-secondary); text-overflow: ellipsis; white-space: nowrap; }
.form-actions { display: flex; justify-content: flex-end; gap: 8px; padding-top: 12px; }
</style>
