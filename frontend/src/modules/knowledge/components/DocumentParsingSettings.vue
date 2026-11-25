<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputPassword as AInputPassword,
  InputNumber as AInputNumber,
  Select as ASelect,
  Switch as ASwitch,
  Tag as ATag,
  message
} from 'ant-design-vue'
import WorkspacePanel from '../../../shared/components/WorkspacePanel.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import {
  getDocumentParsingSettings,
  getDocumentParsingStatus,
  updateDocumentParsingSettings
} from '../api/documentParsing.ts'

const props = defineProps<{ writable: boolean }>()
const read = new RequestScope(),
  write = new RequestScope()
const loading = ref(false),
  saving = ref(false),
  error = ref('')
const localAvailable = ref<boolean | null>(null)
const cloudAvailable = ref<boolean | null>(null)
const localKey = ref(''),
  cloudKey = ref('')
const form = reactive({
  version: 1,
  localUrl: '',
  hasLocalKey: false,
  cloudEnabled: false,
  cloudUrl: '',
  hasCloudKey: false,
  tier: 'standard',
  timeoutSeconds: 900 as number | null,
  queueTimeoutSeconds: 120
})
const stateLabel = computed(() =>
  !form.localUrl
    ? '本地未配置'
    : localAvailable.value === true
      ? '本地可用'
      : localAvailable.value === false
        ? '本地不可用'
        : '待检测'
)
const tierOptions = [
  { value: 'flash', label: '快速提取' },
  { value: 'basic', label: '基础 OCR' },
  { value: 'standard', label: '标准解析' },
  { value: 'advanced', label: '高级解析' }
]
async function load() {
  const ticket = read.replace()
  loading.value = true
  error.value = ''
  try {
    const data = await getDocumentParsingSettings(ticket.signal)
    if (!ticket.isCurrent()) return
    Object.assign(form, data)
    const status = await getDocumentParsingStatus(ticket.signal)
    if (ticket.isCurrent()) {
      localAvailable.value = status.local
      cloudAvailable.value = status.cloud
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
async function save() {
  if (loading.value || saving.value || !props.writable) return
  if (form.timeoutSeconds === null || !Number.isInteger(form.timeoutSeconds)) {
    error.value = '请填写有效的解析超时'
    return
  }
  const ticket = write.replace()
  saving.value = true
  error.value = ''
  try {
    const data = await updateDocumentParsingSettings(
      {
        expectedVersion: form.version,
        localUrl: form.localUrl,
        localApiKey: localKey.value,
        cloudEnabled: form.cloudEnabled,
        cloudUrl: form.cloudUrl,
        cloudApiKey: cloudKey.value,
        tier: form.tier,
        timeoutSeconds: form.timeoutSeconds,
        queueTimeoutSeconds: form.queueTimeoutSeconds
      },
      ticket.signal
    )
    if (!ticket.isCurrent()) return
    Object.assign(form, data)
    localKey.value = ''
    cloudKey.value = ''
    void message.success('解析配置已保存，将用于新任务')
    await load()
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => {
  read.dispose()
  write.dispose()
  localKey.value = ''
  cloudKey.value = ''
})
</script>

<template>
  <WorkspacePanel>
    <div class="parsing-heading">
      <div>
        <strong>文档解析</strong
        ><ATag :color="localAvailable ? 'green' : 'default'">{{ stateLabel }}</ATag>
        <ATag v-if="form.cloudEnabled" :color="cloudAvailable ? 'green' : 'default'">
          {{ cloudAvailable === null ? '云端待检测' : cloudAvailable ? '云端可用' : '云端不可用' }}
        </ATag>
      </div>
      <div class="parsing-actions">
        <RefreshButton label="刷新解析服务" :loading="loading" :disabled="saving" @click="load" />
        <AButton v-if="writable" :loading="saving" :disabled="loading" @click="save"
          >保存解析配置</AButton
        >
      </div>
    </div>
    <p class="parsing-order">本地 MinerU 优先，云端按需启用，最后使用基础解析。</p>
    <AAlert v-if="error" :message="error" type="error" show-icon class="parsing-error" />
    <AForm layout="vertical" :disabled="loading || saving || !writable">
      <div class="parsing-fields">
        <AFormItem label="本地 MinerU 地址">
          <AInput
            v-model:value="form.localUrl"
            placeholder="http://mineru-api:8000，留空跳过"
            :maxlength="2048"
          />
        </AFormItem>
        <AFormItem label="本地服务密钥（可选）">
          <AInputPassword
            v-model:value="localKey"
            autocomplete="off"
            :placeholder="form.hasLocalKey ? '留空保留；更换地址请重填' : '服务启用认证时填写'"
          />
        </AFormItem>
      </div>
      <AFormItem label="云端后备">
        <ASwitch
          :checked="form.cloudEnabled"
          aria-label="启用云端解析后备"
          @update:checked="(value) => (form.cloudEnabled = value === true)"
        />
        <span class="parsing-switch-label">本地不可用时使用云端</span>
      </AFormItem>
      <template v-if="form.cloudEnabled">
        <p class="parsing-cloud-note">
          启用后可能向此服务上传文档，并产生服务商费用。官方云端使用 vlm 模型，解析档位仅用于本地及
          V1 兼容服务。
        </p>
        <div class="parsing-fields">
          <AFormItem label="云端 MinerU 地址" required>
            <AInput
              v-model:value="form.cloudUrl"
              placeholder="https://mineru.net/api/v4"
              :maxlength="2048"
            />
          </AFormItem>
          <AFormItem label="云端 API Key">
            <AInputPassword
              v-model:value="cloudKey"
              autocomplete="off"
              :placeholder="form.hasCloudKey ? '留空保留；更换地址请重填' : '填写服务商密钥'"
            />
          </AFormItem>
        </div>
      </template>
      <div class="parsing-fields">
        <AFormItem label="解析档位"
          ><ASelect
            :value="form.tier"
            :options="tierOptions"
            @update:value="
              (value) => {
                if (typeof value === 'string') form.tier = value
              }
            "
        /></AFormItem>
        <AFormItem label="解析超时（秒）"
          ><AInputNumber
            :value="form.timeoutSeconds ?? ''"
            :min="30"
            :max="7200"
            :precision="0"
            @update:value="
              (value) => (form.timeoutSeconds = typeof value === 'number' ? value : null)
            "
        /></AFormItem>
      </div>
    </AForm>
  </WorkspacePanel>
</template>

<style scoped>
.parsing-heading,
.parsing-heading > div,
.parsing-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.parsing-heading {
  justify-content: space-between;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.parsing-heading strong {
  font-size: var(--workspace-font-size-section);
  font-weight: var(--workspace-font-weight-medium);
}
.parsing-order,
.parsing-cloud-note {
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-helper);
  margin: 0 0 16px;
}
.parsing-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.parsing-fields :deep(.ant-select),
.parsing-fields :deep(.ant-input-number) {
  width: 100%;
}
.parsing-switch-label {
  margin-left: 10px;
  color: var(--color-text-secondary);
}
.parsing-error {
  margin-bottom: 16px;
}
@media (max-width: 700px) {
  .parsing-fields {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
