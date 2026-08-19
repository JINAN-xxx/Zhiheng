<template>
  <el-form ref="formRef" :model="form" :rules="rules" label-position="top" class="engine-form">
    <div class="form-grid">
      <el-form-item v-if="!quick" label="路由别名" prop="modelAlias">
        <el-input v-model="form.modelAlias" placeholder="例如 chat-default" @input="markChanged" />
      </el-form-item>
      <el-form-item label="上游模型" prop="upstreamModel">
        <el-input v-model="form.upstreamModel" placeholder="例如 gpt-4.1-mini" @input="markChanged" />
      </el-form-item>
      <el-form-item v-if="!quick" label="Provider" prop="provider">
        <el-select v-model="form.provider" filterable style="width: 100%" placeholder="选择 Provider" @change="markChanged">
          <el-option v-for="item in providers" :key="item.provider" :label="item.displayName" :value="item.provider" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="!quick" label="Provider 凭据" prop="credentialName">
        <el-select v-model="form.credentialName" filterable style="width: 100%" placeholder="选择凭据" @change="markChanged">
          <el-option
            v-for="item in matchingCredentials"
            :key="item.name"
            :label="item.name"
            :value="item.name"
          />
        </el-select>
      </el-form-item>
      <el-form-item v-if="!quick" label="请求超时（秒）">
        <el-input-number v-model="form.timeout" :min="0.1" :max="600" controls-position="right" style="width: 100%" />
      </el-form-item>
      <el-form-item v-if="!quick" label="流式超时（秒）">
        <el-input-number v-model="form.streamTimeout" :min="0.1" :max="600" controls-position="right" style="width: 100%" />
      </el-form-item>
      <el-form-item v-if="!quick" label="最大重试">
        <el-input-number v-model="form.maxRetries" :min="0" :max="10" controls-position="right" style="width: 100%" />
      </el-form-item>
      <el-form-item v-if="!quick && mode === 'edit'" label="阻止流量">
        <el-switch v-model="form.blocked" inline-prompt active-text="是" inactive-text="否" />
      </el-form-item>
    </div>
    <el-form-item v-if="!quick" label="描述">
      <el-input v-model="form.description" type="textarea" :rows="3" maxlength="2000" show-word-limit />
    </el-form-item>

    <el-collapse v-if="quick" class="advanced-settings">
      <el-collapse-item name="advanced">
        <template #title><span>高级设置</span></template>
        <div class="form-grid">
          <el-form-item label="请求超时（秒）">
            <el-input-number v-model="form.timeout" :min="0.1" :max="600" controls-position="right" style="width: 100%" />
          </el-form-item>
          <el-form-item label="流式超时（秒）">
            <el-input-number v-model="form.streamTimeout" :min="0.1" :max="600" controls-position="right" style="width: 100%" />
          </el-form-item>
          <el-form-item label="最大重试">
            <el-input-number v-model="form.maxRetries" :min="0" :max="10" controls-position="right" style="width: 100%" />
          </el-form-item>
        </div>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" maxlength="2000" show-word-limit />
        </el-form-item>
      </el-collapse-item>
    </el-collapse>

    <div class="form-actions">
      <el-button @click="$emit('cancel')">取消</el-button>
      <el-button :loading="testLoading" :icon="Connection" @click="testConnection">测试连接</el-button>
      <el-button
        type="primary"
        :loading="submitting"
        :disabled="requireTest && !tested"
        :icon="Check"
        @click="submit"
      >
        {{ mode === 'edit' ? '保存变更' : '创建部署' }}
      </el-button>
    </div>
  </el-form>
</template>

<script setup>
import { Check, Connection } from '@element-plus/icons-vue'
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
  provider: [{ required: true, message: '请选择 Provider', trigger: 'change' }],
  credentialName: [{ required: true, message: '请选择凭据', trigger: 'change' }],
}

const matchingCredentials = computed(() => props.credentials.filter((item) => (
  !form.provider || isCredentialCompatible(item, form.provider, props.providers)
)))

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
  nextTick(() => formRef.value?.validateField('credentialName').catch(() => {}))
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
  const valid = await formRef.value.validateField(['upstreamModel', 'provider', 'credentialName']).then(() => true).catch(() => false)
  if (!valid) return
  emit('test', {
    upstreamModel: form.upstreamModel,
    provider: form.provider,
    credentialName: form.credentialName,
    values: {},
  })
}

async function submit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  emit('submit', payload())
}
</script>

<style scoped>
.engine-form { padding: 0 4px 16px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 16px; }
.advanced-settings { margin-bottom: 12px; }
.form-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; padding-top: 12px; }
@media (max-width: 640px) {
  .form-grid { grid-template-columns: minmax(0, 1fr); }
  .form-actions :deep(.el-button) { flex: 1 1 auto; margin-left: 0; }
}
</style>
