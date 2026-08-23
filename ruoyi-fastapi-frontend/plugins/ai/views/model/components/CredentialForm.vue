<template>
  <a-form ref="formRef" :model="form" :rules="rules" layout="vertical" class="engine-form">
    <a-form-item v-if="!quick" label="凭据名称" name="credentialName">
      <a-input
        v-model:value="form.credentialName"
        :disabled="mode === 'rotate'"
        :maxlength="128"
        placeholder="例如 openai-production"
      />
    </a-form-item>
    <a-form-item v-if="!quick" label="服务商" name="provider">
      <a-select
        v-model:value="form.provider"
        :disabled="mode === 'rotate'"
        show-search
        option-filter-prop="label"
        placeholder="选择服务商"
        style="width: 100%"
        :options="providerOptions"
      />
    </a-form-item>

    <a-divider orientation="left">凭据字段</a-divider>
    <a-empty v-if="form.provider && currentFields.length === 0" description="该服务商无需额外凭据" />
    <a-form-item
      v-for="field in currentFields"
      :key="field.key"
      :name="['values', field.key]"
      :rules="field.required ? [{ required: true, message: `${field.label}不能为空`, trigger: 'blur' }] : []"
    >
      <template #label>
        <span>{{ field.label }}</span>
        <a-tooltip v-if="field.tooltip" :title="field.tooltip" placement="top">
          <QuestionCircleOutlined class="field-help" />
        </a-tooltip>
      </template>
      <a-select
        v-if="field.fieldType === 'select'"
        v-model:value="form.values[field.key]"
        :placeholder="field.placeholder || `选择${field.label}`"
        allow-clear
        style="width: 100%"
        :options="(field.options || []).map((option) => ({ label: String(option), value: String(option) }))"
      />
      <SecretField
        v-else-if="isSecretField(field)"
        v-model:value="form.values[field.key]"
        :multiline="field.fieldType === 'textarea'"
        :placeholder="field.placeholder || `输入${field.label}`"
      />
      <a-textarea
        v-else-if="field.fieldType === 'textarea'"
        v-model:value="form.values[field.key]"
        :rows="4"
        :placeholder="field.placeholder || `输入${field.label}`"
      />
      <div v-else-if="field.fieldType === 'upload'" class="upload-field">
        <a-upload
          accept=".json,.pem,.txt,.key,.crt,.cer,.yaml,.yml,.conf,.config,application/json,text/plain,text/yaml,application/x-pem-file,application/yaml"
          :before-upload="(file) => selectCredentialFile(field.key, file)"
          :show-upload-list="false"
        >
          <a-button><UploadOutlined />选择文件</a-button>
        </a-upload>
        <span v-if="fileNames[field.key]" class="file-name">{{ fileNames[field.key] }}</span>
      </div>
      <a-input
        v-else
        v-model:value="form.values[field.key]"
        autocomplete="new-password"
        :placeholder="field.placeholder || `输入${field.label}`"
      />
    </a-form-item>

    <div class="form-actions">
      <a-button @click="$emit('cancel')">取消</a-button>
      <a-button type="primary" :loading="submitting" @click="submit">
        <CheckOutlined />
        {{ mode === 'rotate' ? '确认轮换' : (quick ? '保存并继续' : '创建凭据') }}
      </a-button>
    </div>
  </a-form>
</template>

<script setup>
import { CheckOutlined, QuestionCircleOutlined, UploadOutlined } from '@ant-design/icons-vue'
import SecretField from '@/components/Antd/SecretField.vue'
import feedback from '@/plugins/antdFeedback'
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
  provider: [{ required: true, message: '请选择服务商', trigger: 'change' }],
}

const currentProvider = computed(() => resolveProvider(props.providers, form.provider))
const currentFields = computed(() => currentProvider.value?.fields || [])
const providerOptions = computed(() => props.providers.map((item) => ({
  label: item.displayName,
  value: item.provider,
})))

function isSecretField(field) {
  if (field.fieldType === 'password') return true
  if (field.fieldType === 'select' || field.fieldType === 'upload') return false
  const normalizedKey = String(field.key || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  return ['key', 'credential', 'cred', 'auth', 'token', 'secret', 'password', 'pass', 'private', 'cert']
    .some((marker) => normalizedKey.includes(marker))
}

function initialFieldValue(field) {
  if (isSecretField(field) || ['upload', 'textarea'].includes(field.fieldType)) return ''
  return field.defaultValue == null ? '' : String(field.defaultValue)
}

function resetForm() {
  invalidateFileReads()
  form.credentialName = props.credentialName
  form.provider = resolveProvider(props.providers, props.provider)?.provider || props.provider
  form.values = {}
  Object.keys(fileNames).forEach((key) => delete fileNames[key])
  for (const field of currentFields.value) {
    form.values[field.key] = initialFieldValue(field)
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
      form.values[field.key] = initialFieldValue(field)
    }
  },
)

function invalidateFileReads() {
  fileScopeGeneration += 1
  fileFieldGenerations.clear()
}

function selectCredentialFile(key, file) {
  void readCredentialFile(key, file)
  return false
}

async function readCredentialFile(key, uploadFile) {
  const raw = uploadFile?.originFileObj || uploadFile?.raw || uploadFile
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
    feedback.msgError(metadataError)
    return
  }
  if (!raw || typeof raw.text !== 'function') {
    feedback.msgError('凭据文件读取失败')
    return
  }
  try {
    const value = await raw.text()
    if (!isCurrent()) return
    const contentError = validateCredentialFileText(value)
    if (contentError) {
      feedback.msgError(contentError)
      return
    }
    form.values[key] = value
    fileNames[key] = raw.name || uploadFile?.name || '已选择文件'
  } catch {
    if (!isCurrent()) return
    form.values[key] = ''
    delete fileNames[key]
    feedback.msgError('凭据文件读取失败')
  }
}

async function submit() {
  const valid = await formRef.value.validateFields().then(() => true).catch(() => false)
  if (!valid) return
  const values = buildCredentialValues(currentFields.value, form.values)
  if (Object.keys(values).length === 0) {
    feedback.msgError('请至少填写一项凭据值')
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
.field-help { margin-left: 5px; color: var(--workspace-text-secondary); vertical-align: -2px; }
.upload-field { display: flex; min-width: 0; align-items: center; gap: 10px; }
.file-name { min-width: 0; overflow: hidden; color: var(--workspace-text-secondary); text-overflow: ellipsis; white-space: nowrap; }
.form-actions { display: flex; justify-content: flex-end; gap: 8px; padding-top: 12px; }
</style>
