<template>
  <section class="api-docs-page">
    <WorkspaceToolbar title="API 说明"
      ><template #actions
        ><a-button @click="$emit('close')">返回开发接入</a-button></template
      ></WorkspaceToolbar
    >
    <div class="api-docs-layout">
      <nav class="api-docs-nav" aria-label="API 文档目录">
        <div class="api-docs-nav__title">接口说明</div>
        <ApiDocsNavigation
          :nodes="navigation"
          :selected-id="selectedId"
          :expanded-ids="expandedIds"
          @select="selectSection"
          @toggle="toggleFolder"
        />
      </nav>
      <div class="api-docs-content">
        <section id="api-overview" class="api-docs-intro api-doc-anchor">
          <h2>平台 API</h2>
          <p>
            资源接口管理文件、个人知识库与个人技能；推理接口提供模型和智能体调用。两组均使用 Bearer
            访问密钥。
          </p>
          <a-radio-group v-model:value="group" button-style="solid"
            ><a-radio-button value="resources">资源 API</a-radio-button
            ><a-radio-button value="inference">推理 API</a-radio-button></a-radio-group
          >
          <div class="api-doc-code">
            <div class="api-doc-code__title">
              <span>当前调用地址</span><button @click="copy(apiBase)">复制地址</button>
            </div>
            <pre>{{ apiBase }}</pre>
          </div>
          <p>
            <a :href="docsUrl" target="_blank" rel="noopener">Swagger 分组文档</a> ·
            <a :href="schemaUrl" target="_blank" rel="noopener">OpenAPI 3.1 JSON</a>
          </p>
          <p>目录、字段和示例由当前服务的实际接口规范加载。OpenAI 资源 SDK 兼容层尚未注册。</p>
        </section>
        <section id="api-auth" class="api-doc-section">
          <h2>鉴权与授权</h2>
          <p class="api-doc-description">
            发送 Authorization: Bearer $API_KEY。Console Cookie 不作为资源或推理鉴权。Key Scope
            固定；应用资源授权可即时撤销，当前资源 ACL 始终生效。原密钥不会自动获得资源能力。
          </p>
          <p class="api-doc-description">
            资源元数据 Key
            可不选模型。索引、检索和其他模型步骤仍须允许相应模型，并沿用原积分计量；CRUD
            与上传独立按资源请求频率限制。知识检索返回的片段不自动授予原文件下载权限。
          </p>
        </section>
        <section id="api-protocol" class="api-doc-section">
          <h2>版本、幂等与操作</h2>
          <p class="api-doc-description">
            创建、导入、确认及计量检索发送单个 UUID
            Idempotency-Key。重试同一请求复用原键；不同内容返回 409。响应快照保留 24
            小时，过期重放返回 409 和当前允许的原结果引用；operation GET 可长期读取，仍复核当前
            Scope 与 ACL。
          </p>
          <p class="api-doc-description">
            资源文件的元数据、内容下载和变更响应使用同一文件强版本 ETag，可原样用于 If-Match；缺少为
            428，陈旧为 412。 技能不可变原包下载的 ETag 是内容 SHA256，不能用作技能状态版本。202
            表示已接纳，按 Location 查询 operation，建议至少间隔一秒。cancel_requested
            只表示取消意图，partially_succeeded 仍须检查失败项。
          </p>
          <p class="api-doc-description">
            列表使用 items / next_cursor / has_more，默认 20、最多 100。资源错误为 error.code /
            message / details / request_id，响应带 X-Request-ID；429 按 Retry-After 等待。下载支持单
            Range 与 Content-Range。
          </p>
          <pre>{{ resourceError }}</pre>
        </section>
        <section id="api-recipes" class="api-doc-section">
          <h2>资源接入顺序</h2>
          <p class="api-doc-description">
            先设置 API_KEY、FILE_PATH 和每次新请求的 REQUEST_ID（UUID）。知识入库：上传文件 →
            从响应取 FILE_ID → 创建知识库（EMBED_MODEL_ID）→ 从响应取 BASE_ID → 文件入库 → 按
            Location 轮询 → 检索。每个操作下均提供对应 curl，原始文件须已授权给当前应用。
          </p>
          <p class="api-doc-description">
            个人技能：将 FILE_PATH 设为 SKILL.md 或 ZIP 上传 → 预检 file_id → 检查 action /
            expected_version → 使用 DRAFT_ID 确认 → 保存 id、current_revision_id 和
            resource_version。内容版本不可原地覆盖，启停用资源版本 ETag，修订引用使用 revision_id。
          </p>
          <pre>
export API_KEY='你的访问密钥'
export FILE_PATH='./notes.txt'
export REQUEST_ID="$(python -c 'import uuid; print(uuid.uuid4())')"
# 同一请求重试保持 REQUEST_ID；下一步新请求生成新 UUID。
# If-Match 示例：export ETAG='"file-1"'</pre
          >
        </section>
        <section v-if="group === 'inference'" id="api-streaming" class="api-doc-section">
          <h2>流式调用</h2>
          <p class="api-doc-description">
            对话请求 stream=true 返回 text/event-stream。data 帧包含 choices[].delta，正常完成发送
            data:
            [DONE]；流内错误仍使用推理错误形状。客户端断开沿用原取消策略。智能体知识准备可能先返回
            202，请按准备状态继续。
          </p>
        </section>
        <WorkspaceState v-if="loading" kind="loading" />
        <WorkspaceState v-else-if="error" kind="error" :message="error" @retry="load" />
        <section
          v-for="item in documents"
          v-else
          :id="`api-${item.id}`"
          :key="item.id"
          class="api-doc-section"
        >
          <div class="api-doc-section__header">
            <span class="method" :class="`method--${item.method.toLowerCase()}`">{{
              item.method
            }}</span
            ><code>{{ item.path }}</code>
          </div>
          <h2>{{ item.title }}</h2>
          <p class="api-doc-description">{{ item.description }}</p>
          <p v-if="item.scopes.length" class="api-doc-description">
            Scope：{{ item.scopes.join('、')
            }}<span v-if="item.scopeMode">，按原生操作类型要求：{{ item.scopeMode }}</span>
          </p>
          <h3 v-if="item.fields.length" class="api-doc-subtitle">请求字段</h3>
          <table v-if="item.fields.length" class="api-doc-fields">
            <thead>
              <tr>
                <th>字段</th>
                <th>类型</th>
                <th>必填</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="field in item.fields" :key="field.name">
                <td>
                  <code>{{ field.name }}</code>
                </td>
                <td>{{ field.type }}</td>
                <td>{{ field.required ? '是' : '否' }}</td>
                <td>{{ field.description }}</td>
              </tr>
            </tbody>
          </table>
          <h3 v-if="item.responseFields.length" class="api-doc-subtitle">成功响应字段</h3>
          <table v-if="item.responseFields.length" class="api-doc-fields">
            <thead>
              <tr>
                <th>字段</th>
                <th>类型</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="field in item.responseFields" :key="field.name">
                <td>
                  <code>{{ field.name }}</code>
                </td>
                <td>{{ field.type }}</td>
                <td>{{ field.description }}</td>
              </tr>
            </tbody>
          </table>
          <div class="api-doc-code">
            <div class="api-doc-code__title">
              <span>curl</span><button @click="copy(renderCode(item.curlExample))">复制示例</button>
            </div>
            <pre>{{ renderCode(item.curlExample) }}</pre>
          </div>
          <h3 class="api-doc-subtitle">响应状态与头</h3>
          <pre>{{ item.responses }}</pre>
        </section>
      </div>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Button as AButton,
  RadioGroup as ARadioGroup,
  RadioButton as ARadioButton,
  message
} from 'ant-design-vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { copyText, loadApiDocuments, parseApiDocAnchor } from '../apiDocs.ts'
import type { ApiDocument } from '../apiDocs.ts'
import { apiDocAncestorIds, buildApiDocNavigation } from '../apiDocsNavigation.ts'
import ApiDocsNavigation from './ApiDocsNavigation.vue'
defineEmits<{ close: [] }>()
const group = ref<'resources' | 'inference'>('resources'),
  documents = ref<ApiDocument[]>([]),
  loading = ref(false),
  error = ref(''),
  selectedId = ref('api-overview'),
  expandedIds = ref<string[]>(['guide'])
const scope = new RequestScope()
const apiBase = computed(
  () => `${window.location.origin}${group.value === 'resources' ? '/api/v1' : '/v1'}`
)
const schemaUrl = computed(() => `/openapi/${group.value}-v1.json`)
const docsUrl = computed(() => `/docs/${group.value}`)
const navigation = computed(() => buildApiDocNavigation(documents.value, group.value))
const anchors = computed(() => [
  'api-overview',
  'api-auth',
  'api-protocol',
  'api-recipes',
  ...(group.value === 'inference' ? ['api-streaming'] : []),
  ...documents.value.map((item) => `api-${item.id}`)
])
watch([navigation, selectedId], () => {
  expandedIds.value = [
    ...new Set([...expandedIds.value, ...apiDocAncestorIds(navigation.value, selectedId.value)])
  ]
})
const resourceError = JSON.stringify(
  {
    error: {
      code: 'precondition_failed',
      message: '资源已变化',
      details: null,
      request_id: '服务端请求 UUID'
    }
  },
  null,
  2
)
function renderCode(code: string): string {
  return code.replaceAll('{{ORIGIN}}', window.location.origin)
}
function selectSection(anchor: string): void {
  selectedId.value = anchor
}
function toggleFolder(id: string, open: boolean): void {
  if (open && !expandedIds.value.includes(id)) expandedIds.value = [...expandedIds.value, id]
  else if (!open && expandedIds.value.includes(id))
    expandedIds.value = expandedIds.value.filter((value) => value !== id)
}
function syncSection(): void {
  selectedId.value = parseApiDocAnchor(window.location.hash, anchors.value)
}
async function copy(text: string): Promise<void> {
  if (await copyText(text)) void message.success('已复制')
  else void message.error('复制失败，请手动复制')
}
async function load(): Promise<void> {
  const ticket = scope.replace()
  loading.value = true
  error.value = ''
  documents.value = []
  try {
    const result = await loadApiDocuments(group.value, ticket.signal)
    if (ticket.isCurrent()) {
      documents.value = result
      syncSection()
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) {
      loading.value = false
      await nextTick()
      if (ticket.isCurrent() && !error.value && window.location.hash)
        document.getElementById(selectedId.value)?.scrollIntoView({ block: 'start' })
    }
  }
}
watch(
  group,
  () => {
    expandedIds.value = ['guide']
    void load()
  },
  { immediate: true }
)
onMounted(() => window.addEventListener('hashchange', syncSection))
onBeforeUnmount(() => {
  scope.dispose()
  window.removeEventListener('hashchange', syncSection)
})
</script>
<style scoped lang="scss">
.api-docs-page {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}
.api-docs-layout {
  display: grid;
  min-height: 0;
  flex: 1;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 20px;
  margin: 0 var(--workspace-space-6) var(--workspace-space-6);
}
.api-docs-nav {
  overflow-y: auto;
  padding: 16px 8px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.api-docs-nav__title {
  padding: 0 16px 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.api-docs-content {
  min-width: 0;
  overflow: auto;
  padding: 22px 32px 48px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.api-docs-intro {
  max-width: 920px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--gray-150);
  color: var(--color-text-secondary);
  line-height: 1.7;
}
.api-docs-intro h2 {
  margin: 0 0 8px;
  color: var(--color-text);
  font-size: 16px;
  font-weight: 500;
}
.api-doc-anchor {
  scroll-margin-top: 16px;
}
.api-docs-intro p {
  margin: 0 0 8px;
}
.api-docs-content code {
  font-family: var(--workspace-font-mono);
  font-size: 12px;
}
.api-docs-content pre {
  margin: 8px 0 0;
  overflow: auto;
  padding: 12px 14px;
  border: 1px solid var(--gray-150);
  background: var(--gray-10);
  color: var(--color-text);
  line-height: 1.6;
  white-space: pre-wrap;
}
.api-doc-section {
  max-width: 920px;
  padding: 28px 0;
  border-bottom: 1px solid var(--gray-150);
  scroll-margin-top: 16px;
}
.api-doc-section__header {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--color-text-secondary);
}
.api-doc-section h2 {
  margin: 12px 0 4px;
  font-size: 16px;
  font-weight: 500;
}
.api-doc-description {
  margin: 0 0 14px;
  color: var(--color-text-secondary);
  line-height: 1.7;
}
.api-doc-subtitle {
  margin: 18px 0 8px;
  font-size: 13px;
  font-weight: 600;
}
.method {
  display: inline-flex;
  min-width: 48px;
  justify-content: center;
  padding: 3px 7px;
  border-radius: 3px;
  font-family: var(--workspace-font-mono);
  font-size: var(--workspace-font-size-caption);
  font-weight: 600;
}
.method--get {
  background: var(--color-success-50);
  color: var(--color-success-700);
}
.method--post {
  background: var(--color-warning-50);
  color: var(--color-warning-900);
}
.stream-label {
  color: var(--color-success-700);
  font-size: 12px;
}
.api-doc-fields {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.api-doc-fields th,
.api-doc-fields td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--gray-150);
  text-align: left;
  vertical-align: top;
}
.api-doc-fields th {
  background: var(--gray-10);
  color: var(--color-text-secondary);
  font-weight: 500;
}
.api-doc-code {
  margin-top: 16px;
}
.api-doc-code__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.api-doc-code__title button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-700);
  cursor: pointer;
}
@media (max-width: 760px) {
  .api-docs-layout {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr);
    gap: 12px;
    margin: 0 16px 16px;
  }
  .api-docs-nav {
    display: flex;
    overflow-x: auto;
    border-right: 0;
    border-bottom: 1px solid var(--gray-150);
    padding: 8px;
  }
  .api-docs-nav__title {
    display: none;
  }
  .api-docs-content {
    padding: 18px 16px 36px;
  }
}
</style>
