<template>
  <section class="api-docs-page">
    <WorkspaceToolbar title="API 说明" subtitle="使用访问密钥调用平台模型">
      <template #actions>
        <a-button @click="$emit('close')"><LeftOutlined />返回开发接入</a-button>
        <a-button @click="copyApiBase"><CopyOutlined />复制调用地址</a-button>
        <a-button :href="swaggerUrl" target="_blank" rel="noopener">Swagger</a-button>
        <a-button :href="redocUrl" target="_blank" rel="noopener">ReDoc</a-button>
      </template>
    </WorkspaceToolbar>

    <section class="api-docs-layout">
      <!-- GET /v1/models | POST /v1/chat/completions | POST /v1/embeddings | POST /v1/rerank -->
      <nav class="api-docs-nav" aria-label="接口目录">
        <div class="api-docs-nav__title">接口目录</div>
        <a
          v-for="item in apiDocs"
          :key="item.id"
          class="api-docs-nav__item"
          :class="{ 'is-active': item.id === selectedId }"
          :href="`#api-${item.id}`"
          @click="selectedId = item.id"
        >
          <span class="method" :class="`method--${item.method.toLowerCase()}`">{{ item.method }}</span>
          <span>{{ item.title }}</span>
        </a>
      </nav>

      <div class="api-docs-content">
        <div class="api-docs-intro">
          <p>所有请求都需要在 <code>Authorization</code> 请求头中携带访问密钥：</p>
          <pre><code>Authorization: Bearer &lt;DEVELOPER_API_KEY&gt;</code></pre>
          <p>调用地址：<code>{{ apiBase }}</code></p>
        </div>

        <article v-for="item in apiDocs" :id="`api-${item.id}`" :key="item.id" class="api-doc-section">
          <header class="api-doc-section__header">
            <span class="method" :class="`method--${item.method.toLowerCase()}`">{{ item.method }}</span>
            <code>{{ item.path }}</code>
            <span v-if="item.streaming" class="stream-label">支持流式</span>
          </header>
          <h2>{{ item.title }}</h2>
          <p class="api-doc-description">{{ item.description }}</p>

          <table v-if="item.fields?.length" class="api-doc-fields">
            <thead><tr><th>字段</th><th>类型</th><th>必填</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="field in item.fields" :key="field.name">
                <td><code>{{ field.name }}</code></td><td>{{ field.type }}</td><td>{{ field.required ? '是' : '否' }}</td><td>{{ field.description }}</td>
              </tr>
            </tbody>
          </table>

          <h3 v-if="item.responseFields?.length" class="api-doc-subtitle">响应字段</h3>
          <table v-if="item.responseFields?.length" class="api-doc-fields">
            <thead><tr><th>字段</th><th>类型</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="field in item.responseFields" :key="field.name">
                <td><code>{{ field.name }}</code></td><td>{{ field.type }}</td><td>{{ field.description }}</td>
              </tr>
            </tbody>
          </table>

          <div v-if="item.requestExample" class="api-doc-code">
            <div class="api-doc-code__title">请求示例 <button type="button" @click="copyCode(item.requestExample)"><CopyOutlined />复制</button></div>
            <pre><code>{{ item.requestExample }}</code></pre>
          </div>
          <div class="api-doc-code">
            <div class="api-doc-code__title">响应示例</div>
            <pre><code>{{ item.responseExample }}</code></pre>
          </div>
          <div class="api-doc-code">
            <div class="api-doc-code__title">curl <button type="button" @click="copyCode(renderCode(item.curlExample))"><CopyOutlined />复制</button></div>
            <pre><code>{{ renderCode(item.curlExample) }}</code></pre>
          </div>
          <div class="api-doc-code">
            <div class="api-doc-code__title">Python <button type="button" @click="copyCode(renderCode(item.pythonExample))"><CopyOutlined />复制</button></div>
            <pre><code>{{ renderCode(item.pythonExample) }}</code></pre>
          </div>
        </article>

        <article id="api-errors" class="api-doc-section api-doc-errors">
          <h2>错误处理</h2>
          <p class="api-doc-description">接口失败时返回统一 JSON 结构，HTTP 状态码用于区分鉴权、参数和服务错误。</p>
          <table class="api-doc-fields">
            <thead><tr><th>状态码</th><th>错误码</th><th>说明</th></tr></thead>
            <tbody><tr v-for="item in errorCodes" :key="item.code"><td>{{ item.status }}</td><td><code>{{ item.code }}</code></td><td>{{ item.description }}</td></tr></tbody>
          </table>
          <pre><code>{
  "error": {
    "message": "The request is invalid.",
    "type": "invalid_request_error",
    "param": null,
    "code": "invalid_request"
  }
}</code></pre>
        </article>
      </div>
    </section>
  </section>
</template>

<script setup>
import { CopyOutlined, LeftOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import { apiDocs, buildPublicApiBase } from '../../../utils/apiDocs.js'

defineEmits(['close'])
const selectedId = ref(apiDocs[0]?.id || '')
const baseApi = String(import.meta.env.VITE_APP_BASE_API || '')
const apiBase = computed(() => buildPublicApiBase(window.location.origin, baseApi))
const docsBase = computed(() => apiBase.value.replace(/\/v1$/, ''))
const swaggerUrl = computed(() => `${docsBase.value}/docs`)
const redocUrl = computed(() => `${docsBase.value}/redoc`)
const errorCodes = [
  { status: 401, code: 'invalid_api_key', description: '访问密钥无效、已撤销或所属应用已停用。' },
  { status: 403, code: 'permission_denied', description: '当前密钥没有目标模型权限。' },
  { status: 404, code: 'model_not_found', description: '模型不存在或未发布。' },
  { status: 400, code: 'invalid_request', description: '请求结构或字段不符合协议。' },
  { status: 429, code: 'rate_limit_exceeded', description: '超过请求、并发或预算限额。' },
  { status: 502, code: 'upstream_error', description: '模型服务提供方请求失败。' },
  { status: 503, code: 'service_unavailable', description: '模型服务暂时不可用。' },
]

function renderCode(code) {
  return String(code || '').replaceAll('{{API_BASE}}', apiBase.value)
}

async function copyApiBase() {
  await navigator.clipboard.writeText(apiBase.value)
  feedback.msgSuccess('调用地址已复制')
}

async function copyCode(code) {
  await navigator.clipboard.writeText(code)
  feedback.msgSuccess('示例已复制')
}
</script>

<style scoped lang="scss">
.api-docs-page { display: flex; height: 100%; min-height: 0; flex-direction: column; color: var(--color-text); font-family: var(--workspace-font-family); }
.api-docs-layout { display: grid; min-height: 0; flex: 1; grid-template-columns: 220px minmax(0, 1fr); border-top: 1px solid var(--gray-150); }
.api-docs-nav { overflow-y: auto; padding: 16px 0; border-right: 1px solid var(--gray-150); background: var(--bg-container); }
.api-docs-nav__title { padding: 0 16px 10px; color: var(--color-text-secondary); font-size: 12px; }
.api-docs-nav__item { display: flex; align-items: center; gap: 8px; min-height: 38px; padding: 8px 16px; color: var(--color-text); text-decoration: none; font-size: 13px; }
.api-docs-nav__item:hover, .api-docs-nav__item.is-active { background: var(--main-50); color: var(--main-700); }
.api-docs-content { min-width: 0; overflow: auto; padding: 22px 32px 48px; background: var(--bg-container); }
.api-docs-intro { max-width: 920px; padding-bottom: 18px; border-bottom: 1px solid var(--gray-150); color: var(--color-text-secondary); line-height: 1.7; }
.api-docs-intro p { margin: 0 0 8px; }
.api-docs-content code { font-family: var(--workspace-font-mono); font-size: 12px; }
.api-docs-content pre { margin: 8px 0 0; overflow: auto; padding: 12px 14px; border: 1px solid var(--gray-150); background: var(--gray-10); color: var(--color-text); line-height: 1.6; white-space: pre-wrap; }
.api-doc-section { max-width: 920px; padding: 28px 0; border-bottom: 1px solid var(--gray-150); scroll-margin-top: 16px; }
.api-doc-section__header { display: flex; align-items: center; gap: 10px; color: var(--color-text-secondary); }
.api-doc-section h2 { margin: 12px 0 4px; font-size: 18px; font-weight: 600; }
.api-doc-description { margin: 0 0 14px; color: var(--color-text-secondary); line-height: 1.7; }
.api-doc-subtitle { margin: 18px 0 8px; font-size: 13px; font-weight: 600; }
.method { display: inline-flex; min-width: 48px; justify-content: center; padding: 3px 7px; border-radius: 3px; font-family: var(--workspace-font-mono); font-size: 11px; font-weight: 600; }
.method--get { background: var(--color-success-50); color: var(--color-success-700); }
.method--post { background: var(--color-warning-50); color: var(--color-warning-900); }
.stream-label { color: var(--color-success-700); font-size: 12px; }
.api-doc-fields { width: 100%; border-collapse: collapse; font-size: 12px; }
.api-doc-fields th, .api-doc-fields td { padding: 9px 10px; border-bottom: 1px solid var(--gray-150); text-align: left; vertical-align: top; }
.api-doc-fields th { background: var(--gray-10); color: var(--color-text-secondary); font-weight: 500; }
.api-doc-code { margin-top: 16px; }
.api-doc-code__title { display: flex; align-items: center; justify-content: space-between; color: var(--color-text-secondary); font-size: 12px; }
.api-doc-code__title button { display: inline-flex; align-items: center; gap: 4px; padding: 0; border: 0; background: transparent; color: var(--main-700); cursor: pointer; }
@media (max-width: 760px) { .api-docs-layout { grid-template-columns: 1fr; } .api-docs-nav { display: flex; overflow-x: auto; border-right: 0; border-bottom: 1px solid var(--gray-150); padding: 8px; } .api-docs-nav__title { display: none; } .api-docs-nav__item { flex: 0 0 auto; padding: 7px 10px; } .api-docs-content { padding: 18px 16px 36px; } }
</style>
