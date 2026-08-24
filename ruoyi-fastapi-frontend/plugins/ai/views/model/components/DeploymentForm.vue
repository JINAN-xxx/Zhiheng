<template>
  <a-form ref="formRef" :model="form" :rules="rules" layout="vertical" class="engine-form" :class="{ 'is-quick': quick }">
    <div class="form-grid">
      <a-form-item v-if="!quick" label="路由别名" name="modelAlias">
        <a-input v-model:value="form.modelAlias" placeholder="例如 chat-default" @input="markChanged" />
      </a-form-item>
      <a-form-item label="上游模型" name="upstreamModel">
        <a-input v-model:value="form.upstreamModel" placeholder="例如 gpt-4.1-mini" @input="markChanged" />
      </a-form-item>
      <a-form-item v-if="!quick" label="服务商" name="provider">
        <a-select
          v-model:value="form.provider"
          show-search
          option-filter-prop="label"
          style="width: 100%"
          placeholder="选择服务商"
          :options="providerOptions"
          @change="markChanged"
        />
      </a-form-item>
      <a-form-item v-if="!quick" label="服务商凭据" name="credentialName">
        <a-select
          v-model:value="form.credentialName"
          show-search
          option-filter-prop="label"
          style="width: 100%"
          placeholder="选择凭据"
          :options="credentialOptions"
          @change="markChanged"
        />
      </a-form-item>
      <a-form-item v-if="!quick" label="请求超时（秒）">
        <a-input-number v-model:value="form.timeout" :min="0.1" :max="600" style="width: 100%" />
      </a-form-item>
      <a-form-item v-if="!quick" label="流式超时（秒）">
        <a-input-number v-model:value="form.streamTimeout" :min="0.1" :max="600" style="width: 100%" />
      </a-form-item>
      <a-form-item v-if="!quick" label="最大重试">
        <a-input-number v-model:value="form.maxRetries" :min="0" :max="10" style="width: 100%" />
      </a-form-item>
      <a-form-item v-if="!quick && mode === 'edit'" label="阻止流量">
        <a-switch v-model:checked="form.blocked" checked-children="是" un-checked-children="否" />
      </a-form-item>
    </div>
    <a-form-item v-if="!quick" label="描述">
      <a-textarea v-model:value="form.description" :rows="3" :maxlength="2000" show-count />
    </a-form-item>

    <a-collapse v-if="quick" class="advanced-settings">
      <a-collapse-panel key="advanced" header="高级设置">
        <div class="form-grid">
          <a-form-item label="请求超时（秒）">
            <a-input-number v-model:value="form.timeout" :min="0.1" :max="600" style="width: 100%" />
          </a-form-item>
          <a-form-item label="流式超时（秒）">
            <a-input-number v-model:value="form.streamTimeout" :min="0.1" :max="600" style="width: 100%" />
          </a-form-item>
          <a-form-item label="最大重试">
            <a-input-number v-model:value="form.maxRetries" :min="0" :max="10" style="width: 100%" />
          </a-form-item>
        </div>
        <a-form-item label="描述">
          <a-textarea v-model:value="form.description" :rows="3" :maxlength="2000" show-count />
        </a-form-item>
      </a-collapse-panel>
    </a-collapse>

    <div class="form-actions">
      <a-button @click="$emit('cancel')">取消</a-button>
      <a-button :loading="testLoading" @click="testConnection"><ApiOutlined />测试连接</a-button>
      <a-button
        type="primary"
        :loading="submitting"
        :disabled="requireTest && !tested"
        @click="submit"
      >
        <CheckOutlined />
        {{ mode === 'edit' ? '保存变更' : (quick ? '保存并继续' : '创建部署') }}
      </a-button>
    </div>
  </a-form>
</template>

<script setup>
import { ApiOutlined, CheckOutlined } from '@ant-design/icons-vue'
import { isCredentialCompatible, resolveProvider } from '../../../utils/modelEngine.js'

const props = defineProps({
  providers: { type: Array, default: () => [] },
  credentials: { type: Array, default: () => [] },
  deployment: { type: Object, default: null },
  mode: { type: String, default: 'create' },
  submitting: { type: Boolean, default: false },
  testLoading: { type: Boolean, default: false },
  tested: { type: Boolean, default: false },
  requireTest: { type: Boolean, default: false },
  quick: { type: Boolean, default: false },
  initialAlias: { type: String, default: '' },
  initialCredentialName: { type: String, default: '' },
  initialProvider: { type: String, default: '' },
})

const emit = defineEmits(['submit', 'test', 'cancel', 'change'])
const formRef = ref()
const form = reactive({
  modelAlias: '', upstreamModel: '', provider: '', credentialName: '', timeout: null,
  streamTimeout: null, maxRetries: null, description: '', blocked: false,
})

const rules = {
  modelAlias: [{ required: true, message: '路由别名不能为空', trigger: 'blur' }],
  upstreamModel: [{ required: true, message: '上游模型不能为空', trigger: 'blur' }],
  provider: [{ required: true, message: '请选择服务商', trigger: 'change' }],
  credentialName: [{ required: true, message: '请选择凭据', trigger: 'change' }],
}

const matchingCredentials = computed(() => props.credentials.filter((item) => (
  !form.provider || isCredentialCompatible(item, form.provider, props.providers)
)))
const providerOptions = computed(() => props.providers.map((item) => ({
  label: item.displayName,
  value: item.provider,
})))
const credentialOptions = computed(() => matchingCredentials.value.map((item) => ({
  label: item.name,
  value: item.name,
})))

function resetForm() {
  const source = props.deployment || {}
  form.modelAlias = source.alias || props.initialAlias || ''
  form.upstreamModel = source.baseModel || ''
  const providerIdentity = source.provider || props.initialProvider || ''
  form.provider = resolveProvider(props.providers, providerIdentity)?.provider || providerIdentity
  form.credentialName = source.credentialName || props.initialCredentialName || ''
  form.timeout = source.timeout
  form.streamTimeout = source.streamTimeout
  form.maxRetries = source.maxRetries
  form.description = source.description || ''
  form.blocked = source.blocked === true
  nextTick(() => formRef.value?.clearValidate())
}

watch(() => [props.deployment, props.initialAlias, props.initialCredentialName, props.initialProvider], resetForm, { immediate: true })
watch(() => form.provider, (provider, previousProvider) => {
  if (provider === previousProvider || !form.credentialName) return
  const selectedCredential = props.credentials.find((item) => item.name === form.credentialName)
  if (isCredentialCompatible(selectedCredential, provider, props.providers)) return
  form.credentialName = ''
  markChanged()
  nextTick(() => formRef.value?.validateFields(['credentialName']).catch(() => {}))
})

function markChanged() { emit('change') }

function payload() {
  const selectedCredential = props.credentials.find((item) => item.name === form.credentialName)
  const data = {
    modelAlias: form.modelAlias,
    upstreamModel: form.upstreamModel,
    provider: form.provider,
    credentialName: isCredentialCompatible(selectedCredential, form.provider, props.providers) ? form.credentialName : '',
  }
  for (const key of ['timeout', 'streamTimeout', 'maxRetries']) {
    if (form[key] != null) data[key] = form[key]
  }
  if (form.description.trim()) data.description = form.description.trim()
  if (props.mode === 'edit') data.blocked = form.blocked
  return data
}

async function testConnection() {
  const fields = props.quick ? ['upstreamModel'] : ['upstreamModel', 'provider', 'credentialName']
  const valid = await formRef.value.validateFields(fields)
    .then(() => true)
    .catch(() => false)
  if (!valid || !form.provider || !form.credentialName) return
  emit('test', {
    upstreamModel: form.upstreamModel,
    provider: form.provider,
    credentialName: form.credentialName,
    values: {},
  })
}

async function submit() {
  const valid = await formRef.value.validateFields().then(() => true).catch(() => false)
  if (!valid || !form.provider || !form.credentialName) return
  emit('submit', payload())
}
</script>

<style scoped>
.engine-form { padding: 0 4px 16px; color: var(--color-text); }
.engine-form :deep(.ant-form-item-label > label) { color: var(--color-text); }
.engine-form :deep(.ant-input), .engine-form :deep(.ant-input-affix-wrapper), .engine-form :deep(.ant-select-selector), .engine-form :deep(.ant-input-number) { border-color: var(--gray-150); background: var(--bg-container); color: var(--color-text); }
.engine-form :deep(.ant-input::placeholder) { color: var(--color-text-tertiary); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 16px; }
.advanced-settings { margin-bottom: 12px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-10); }
.advanced-settings :deep(.ant-collapse-header) { color: var(--color-text) !important; }
.advanced-settings :deep(.ant-collapse-content) { border-top-color: var(--gray-150); background: var(--bg-container); }
.form-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; padding-top: 12px; }
.engine-form.is-quick .form-actions { position: sticky; bottom: 0; z-index: 1; background: var(--bg-container); border-top: 1px solid var(--gray-150); padding: 12px 0; }
@media (max-width: 640px) {
  .form-grid { grid-template-columns: minmax(0, 1fr); }
  .form-actions { flex-wrap: wrap; }
  .form-actions :deep(.ant-btn) { flex: 1 1 auto; margin-left: 0; }
  .engine-form.is-quick .form-actions :deep(.ant-btn) { flex-basis: 100%; }
}
</style>
