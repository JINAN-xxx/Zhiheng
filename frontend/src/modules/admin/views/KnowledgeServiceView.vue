<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  InputNumber as AInputNumber,
  Select as ASelect,
  Spin as ASpin,
  message
} from 'ant-design-vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import WorkspacePanel from '../../../shared/components/WorkspacePanel.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { identity } from '../../identity/public.ts'
import { getModels } from '../../models/public.ts'
import type { ChatModel } from '../../models/public.ts'
import { hasAdminPermission } from '../access.ts'
import { getKnowledgeDefaults, updateKnowledgeDefaults } from '../api/knowledge.ts'

const scope = new RequestScope(),
  savingScope = new RequestScope()
const loading = ref(false),
  saving = ref(false),
  error = ref('')
const models = ref<ChatModel[]>([])
const writable = computed(() => hasAdminPermission(identity.value, 'configs', 'update'))
const form = reactive({
  default_embed_model_id: null as string | null,
  default_rerank_model_id: null as string | null,
  chunk_size: 1000 as number | null,
  chunk_overlap: 200 as number | null,
  version: 1
})
function options(kind: 'embedding' | 'rerank', selected: string | null) {
  const values = models.value
    .filter((model) => model.model_type === kind && model.enabled)
    .map((model) => ({ label: model.name, value: model.id, disabled: false }))
  if (selected && !values.some((item) => item.value === selected))
    values.push({ label: '当前配置模型（不可用，请重新选择）', value: selected, disabled: true })
  return values
}
const embeddingOptions = computed(() => options('embedding', form.default_embed_model_id))
const rerankOptions = computed(() => options('rerank', form.default_rerank_model_id))
async function load() {
  const ticket = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const [defaults, available] = await Promise.all([
      getKnowledgeDefaults(ticket.signal),
      getModels(ticket.signal)
    ])
    if (!ticket.isCurrent()) return
    Object.assign(form, defaults)
    models.value = available
  } catch (failure) {
    if (ticket.isCurrent() && !isAborted(failure)) error.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
async function save() {
  if (loading.value || saving.value || !writable.value) return
  const size = form.chunk_size,
    overlap = form.chunk_overlap
  if (size === null || overlap === null || !Number.isInteger(size) || !Number.isInteger(overlap)) {
    error.value = '请填写有效的切片大小和重叠大小'
    return
  }
  if (overlap >= size) {
    error.value = '重叠大小必须小于切片大小'
    return
  }
  const ticket = savingScope.replace()
  saving.value = true
  error.value = ''
  try {
    const result = await updateKnowledgeDefaults(
      {
        expected_version: form.version,
        default_embed_model_id: form.default_embed_model_id,
        default_rerank_model_id: form.default_rerank_model_id,
        chunk_size: size,
        chunk_overlap: overlap
      },
      ticket.signal
    )
    if (!ticket.isCurrent()) return
    Object.assign(form, result)
    void message.success('知识服务配置已保存')
  } catch (failure) {
    if (ticket.isCurrent() && !isAborted(failure)) error.value = errorMessage(failure)
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => {
  scope.dispose()
  savingScope.dispose()
})
</script>
<template>
  <section class="knowledge-service-page">
    <WorkspaceToolbar title="知识服务" subtitle="统一设置新知识库使用的检索服务">
      <template #actions>
        <RefreshButton label="刷新知识服务" :loading="loading" :disabled="saving" @click="load" />
        <AButton v-if="writable" type="primary" :loading="saving" :disabled="loading" @click="save"
          >保存配置</AButton
        >
      </template>
    </WorkspaceToolbar>
    <div class="knowledge-service-body">
      <AAlert v-if="error" type="error" :message="error" show-icon />
      <ASpin :spinning="loading">
        <WorkspacePanel>
          <AForm layout="vertical" :disabled="loading || saving || !writable">
            <AFormItem label="默认向量模型" html-for="knowledge-default-embedding">
              <ASelect
                id="knowledge-default-embedding"
                :value="form.default_embed_model_id ?? undefined"
                :options="embeddingOptions"
                allow-clear
                placeholder="选择已启用的向量模型"
                aria-label="默认向量模型"
                @update:value="
                  (value) =>
                    (form.default_embed_model_id = typeof value === 'string' ? value : null)
                "
              />
              <p>
                用于新建知识库。已有知识库继续使用其生效索引绑定的模型，更换时需要重新处理资料。
              </p>
            </AFormItem>
            <AFormItem label="默认重排模型" html-for="knowledge-default-reranker">
              <ASelect
                id="knowledge-default-reranker"
                :value="form.default_rerank_model_id ?? undefined"
                :options="rerankOptions"
                allow-clear
                placeholder="可选，未配置时使用基础召回"
                aria-label="默认重排模型"
                @update:value="
                  (value) =>
                    (form.default_rerank_model_id = typeof value === 'string' ? value : null)
                "
              />
              <p>
                向量与关键词召回后进行精排。未配置或临时不可用时使用基础召回结果，管理诊断中会说明原因。
              </p>
            </AFormItem>
            <div class="knowledge-default-chunks">
              <AFormItem label="默认切片大小"
                ><AInputNumber
                  :value="form.chunk_size ?? ''"
                  :min="100"
                  :max="4000"
                  :precision="0"
                  aria-label="默认切片大小"
                  @update:value="
                    (value) => (form.chunk_size = typeof value === 'number' ? value : null)
                  "
              /></AFormItem>
              <AFormItem label="默认重叠大小"
                ><AInputNumber
                  :value="form.chunk_overlap ?? ''"
                  :min="0"
                  :max="Math.min(1000, (form.chunk_size ?? 1000) - 1)"
                  :precision="0"
                  aria-label="默认重叠大小"
                  @update:value="
                    (value) => (form.chunk_overlap = typeof value === 'number' ? value : null)
                  "
              /></AFormItem>
            </div>
          </AForm>
          <AAlert
            v-if="!form.default_embed_model_id"
            type="info"
            message="尚未设置默认向量模型，新知识库暂不能创建。"
            show-icon
          />
        </WorkspacePanel>
      </ASpin>
    </div>
  </section>
</template>
<style scoped>
.knowledge-service-body {
  display: grid;
  gap: 16px;
  max-width: 960px;
  padding: 24px;
}
.knowledge-service-body :deep(.ant-select) {
  width: 100%;
}
.knowledge-service-body p {
  margin: 8px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}
.knowledge-default-chunks {
  display: flex;
  gap: 24px;
}
.knowledge-default-chunks :deep(.ant-input-number) {
  width: 100%;
}
</style>
