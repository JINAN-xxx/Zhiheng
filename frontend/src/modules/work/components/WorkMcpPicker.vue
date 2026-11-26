<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Modal as AModal,
  Select as ASelect,
  Textarea as ATextarea
} from 'ant-design-vue'
import { ApiOutlined } from '@ant-design/icons-vue'
import { errorMessage, RequestScope } from '../../../shared/http.ts'
import { listMcpTools } from '../../mcp/public.ts'
import type { CatalogTool, ToolSelection } from '../../mcp/public.ts'

const props = defineProps<{ selection: ToolSelection | undefined; disabled: boolean }>()
const emit = defineEmits<{ change: [selection: ToolSelection | undefined] }>()
const scope = new RequestScope()
const visible = ref(false),
  loading = ref(false),
  error = ref('')
const tools = ref<CatalogTool[]>([]),
  selected = ref<string | undefined>(),
  argumentsText = ref('{}')
const key = (row: { serverId: string; name: string }) => row.serverId + ':' + row.name
const current = computed(() => tools.value.find((row) => key(row) === selected.value))
const options = computed(() =>
  tools.value.map((row) => ({ value: key(row), label: row.serverName + ' · ' + row.name }))
)

async function open() {
  const ticket = scope.replace()
  visible.value = true
  loading.value = true
  error.value = ''
  selected.value = props.selection
    ? key({ serverId: props.selection.server_id, name: props.selection.name })
    : undefined
  argumentsText.value = JSON.stringify(props.selection?.arguments ?? {}, null, 2)
  try {
    const result = await listMcpTools(ticket.signal)
    if (ticket.isCurrent()) tools.value = result
  } catch (failure) {
    if (ticket.isCurrent()) error.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
function close() {
  scope.replace()
  visible.value = false
  tools.value = []
}
function remove() {
  emit('change', undefined)
  close()
}
function confirm() {
  if (!current.value || props.disabled) return
  try {
    const value: unknown = JSON.parse(argumentsText.value)
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error()
    emit('change', {
      server_id: current.value.serverId,
      name: current.value.name,
      version: current.value.version,
      arguments: value as Record<string, unknown>
    })
    close()
  } catch {
    error.value = '请输入有效的 JSON 对象作为参数'
  }
}
onBeforeUnmount(() => scope.dispose())
</script>
<template>
  <AButton
    type="text"
    :disabled="disabled"
    :title="selection?.name ?? '选择 MCP 工具'"
    aria-label="选择 MCP 工具"
    :class="{ 'mcp-selected': selection }"
    @click="open"
  >
    <ApiOutlined /><span class="composer-tool-label">{{ selection ? '已选 MCP' : 'MCP' }}</span>
  </AButton>
  <AModal :open="visible" title="选择 MCP 工具" @cancel="close">
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <AForm layout="vertical">
      <AFormItem label="本轮工具">
        <ASelect
          :value="selected"
          :options="options"
          :loading="loading"
          show-search
          option-filter-prop="label"
          placeholder="请选择已授权工具"
          aria-label="本轮 MCP 工具"
          not-found-content="暂无已授权工具"
          @update:value="(value) => (selected = typeof value === 'string' ? value : undefined)"
          @change="argumentsText = '{}'"
        />
      </AFormItem>
      <template v-if="current">
        <p v-if="current.description" class="tool-description">{{ current.description }}</p>
        <AFormItem label="发送给 MCP 服务的参数">
          <ATextarea
            v-model:value="argumentsText"
            :rows="4"
            :maxlength="16000"
            aria-label="MCP 工具参数"
          />
        </AFormItem>
        <details>
          <summary>参数格式</summary>
          <pre>{{ JSON.stringify(current.inputSchema, null, 2) }}</pre>
        </details>
      </template>
    </AForm>
    <template #footer>
      <AButton v-if="selection" :disabled="disabled" @click="remove">移除工具</AButton>
      <AButton @click="close">取消</AButton>
      <AButton type="primary" :disabled="loading || disabled || !current" @click="confirm"
        >确认选择</AButton
      >
    </template>
  </AModal>
</template>
<style scoped>
.mcp-selected {
  color: var(--main-color);
}
.tool-description,
pre {
  overflow-wrap: anywhere;
}
pre {
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
}
</style>
