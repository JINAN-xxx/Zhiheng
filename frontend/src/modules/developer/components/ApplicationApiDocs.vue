<!-- Adapted from original ApplicationApiDocs.vue, MIT licensed. -->
<template>
  <section class="api-docs-page">
    <WorkspaceToolbar title="API 说明">
      <template #actions>
        <a-button aria-label="返回开发接入" @click="$emit('close')"
          ><LeftOutlined />返回开发接入</a-button
        >
        <a-button @click="copyApiBase"><CopyOutlined />复制调用地址</a-button>
        <a-button :href="swaggerUrl" v-bind="externalLinkProps">Swagger</a-button>
        <a-button :href="redocUrl" v-bind="externalLinkProps">ReDoc</a-button>
      </template>
    </WorkspaceToolbar>

    <section class="api-docs-layout">
      <!-- GET /v1/models | POST /v1/chat/completions | POST /v1/embeddings | POST /v1/rerank -->
      <nav class="api-docs-nav" aria-label="接口目录">
        <div class="api-docs-nav__title">接口目录</div>
        <a
          v-for="item in apiDocNavigation"
          :key="item.anchor"
          class="api-docs-nav__item"
          :class="{ 'is-active': item.anchor === selectedId }"
          :href="`#${item.anchor}`"
          :aria-current="item.anchor === selectedId ? 'location' : undefined"
          @click="selectSection(item.anchor)"
        >
          <span v-if="item.method" class="method" :class="`method--${item.method.toLowerCase()}`">{{
            item.method
          }}</span>
          <span>{{ item.label }}</span>
        </a>
      </nav>

      <div class="api-docs-content">
        <article id="api-overview" class="api-docs-intro api-doc-anchor">
          <h2>接入说明</h2>
          <p>
            调用地址：<code>{{ apiBase }}</code>
          </p>
          <p>先在开发接入中创建应用和访问密钥，再使用密钥授权范围内的平台模型 ID 调用以下接口。</p>
        </article>

        <article id="api-auth" class="api-doc-section api-doc-anchor">
          <h2>鉴权方式</h2>
          <p class="api-doc-description">
            所有请求都需要在 <code>Authorization</code> 请求头中携带访问密钥。
          </p>
          <pre><code>Authorization: Bearer &lt;DEVELOPER_API_KEY&gt;</code></pre>
        </article>

        <article
          v-for="item in apiDocs"
          :id="`api-${item.id}`"
          :key="item.id"
          class="api-doc-section"
        >
          <header class="api-doc-section__header">
            <span class="method" :class="`method--${item.method.toLowerCase()}`">{{
              item.method
            }}</span>
            <code>{{ item.path }}</code>
            <span v-if="item.streaming" class="stream-label">支持流式</span>
          </header>
          <h2>{{ item.title }}</h2>
          <p class="api-doc-description">{{ item.description }}</p>

          <table
            v-if="item.fields?.length"
            class="api-doc-fields"
            :aria-label="`${item.title}请求字段`"
          >
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

          <h3 v-if="item.id === 'chat-completions'" class="api-doc-subtitle">
            普通响应字段（stream=false）
          </h3>
          <table
            v-if="item.id === 'chat-completions'"
            class="api-doc-fields"
            aria-label="普通响应字段"
          >
            <thead>
              <tr>
                <th>字段</th>
                <th>类型</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="field in item.normalResponseFields" :key="field.name">
                <td>
                  <code>{{ field.name }}</code>
                </td>
                <td>{{ field.type }}</td>
                <td>{{ field.description }}</td>
              </tr>
            </tbody>
          </table>

          <h3 v-if="item.id === 'chat-completions'" class="api-doc-subtitle">
            流式响应字段（stream=true）
          </h3>
          <table
            v-if="item.id === 'chat-completions'"
            class="api-doc-fields"
            aria-label="流式响应字段"
          >
            <thead>
              <tr>
                <th>字段</th>
                <th>类型</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="field in item.streamResponseFields" :key="field.name">
                <td>
                  <code>{{ field.name }}</code>
                </td>
                <td>{{ field.type }}</td>
                <td>{{ field.description }}</td>
              </tr>
            </tbody>
          </table>

          <h3 v-if="item.responseFields?.length" class="api-doc-subtitle">响应字段</h3>
          <table
            v-if="item.responseFields?.length"
            class="api-doc-fields"
            :aria-label="`${item.title}响应字段`"
          >
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

          <div v-if="item.id === 'chat-completions'" class="api-doc-code">
            <div class="api-doc-code__title">
              普通请求示例（stream=false）
              <button type="button" @click="copyCode(item.normalRequestExample)">
                <CopyOutlined />复制
              </button>
            </div>
            <pre><code>{{ item.normalRequestExample }}</code></pre>
          </div>
          <div v-if="item.id === 'chat-completions'" class="api-doc-code">
            <div class="api-doc-code__title">普通响应示例</div>
            <pre><code>{{ item.normalResponseExample }}</code></pre>
          </div>
          <div v-if="item.id !== 'chat-completions' && item.requestExample" class="api-doc-code">
            <div class="api-doc-code__title">
              请求示例
              <button type="button" @click="copyCode(item.requestExample)">
                <CopyOutlined />复制
              </button>
            </div>
            <pre><code>{{ item.requestExample }}</code></pre>
          </div>
          <div v-if="item.id !== 'chat-completions'" class="api-doc-code">
            <div class="api-doc-code__title">响应示例</div>
            <pre><code>{{ item.responseExample }}</code></pre>
          </div>
          <div class="api-doc-code">
            <div class="api-doc-code__title">
              curl
              <button type="button" @click="copyCode(renderCode(item.curlExample))">
                <CopyOutlined />复制
              </button>
            </div>
            <pre><code>{{ renderCode(item.curlExample) }}</code></pre>
          </div>
          <div class="api-doc-code">
            <div class="api-doc-code__title">
              Python
              <button type="button" @click="copyCode(renderCode(item.pythonExample))">
                <CopyOutlined />复制
              </button>
            </div>
            <pre><code>{{ renderCode(item.pythonExample) }}</code></pre>
          </div>
        </article>

        <article id="api-streaming" class="api-doc-section api-doc-anchor">
          <h2>流式调用</h2>
          <p class="api-doc-description">
            对话请求设置 <code>stream=true</code> 后返回
            <code>text/event-stream</code>。依次接收角色帧、内容帧、完成帧，最后收到
            <code>[DONE]</code>。
          </p>
          <div class="api-doc-code">
            <div class="api-doc-code__title">流式请求示例</div>
            <pre><code>{{ chatDocs?.streamRequestExample }}</code></pre>
          </div>
          <div class="api-doc-code">
            <div class="api-doc-code__title">SSE 响应顺序</div>
            <pre><code>{{ chatDocs?.streamResponseExample }}</code></pre>
          </div>
        </article>

        <article id="api-errors" class="api-doc-section api-doc-errors">
          <h2>错误码</h2>
          <p class="api-doc-description">
            接口失败时返回统一 JSON 结构，HTTP 状态码用于区分鉴权、参数和服务错误。
          </p>
          <table class="api-doc-fields" aria-label="错误码">
            <thead>
              <tr>
                <th>状态码</th>
                <th>错误码</th>
                <th>类型</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in publicApiErrors" :key="item.code">
                <td>{{ item.status }}</td>
                <td>
                  <code>{{ item.code }}</code>
                </td>
                <td>
                  <code>{{ item.type }}</code>
                </td>
                <td>{{ item.description }}</td>
              </tr>
            </tbody>
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

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Button as AButton, message } from 'ant-design-vue'
import { CopyOutlined, LeftOutlined } from '@ant-design/icons-vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import {
  apiDocNavigation,
  apiDocs,
  copyText,
  parseApiDocAnchor,
  publicApiErrors
} from '../apiDocs.ts'
defineEmits<{ close: [] }>()
const externalLinkProps = { target: '_blank', rel: 'noopener' }
const selectedId = ref(parseApiDocAnchor(window.location.hash))
const apiBase = computed(() => `${window.location.origin}/v1`)
const swaggerUrl = `${window.location.origin}/docs`
const redocUrl = `${window.location.origin}/redoc`
const chatDocs = apiDocs.find((item) => item.id === 'chat-completions')
function renderCode(code?: string): string {
  return (code ?? '').replaceAll('{{API_BASE}}', apiBase.value)
}
function selectSection(anchor: string): void {
  selectedId.value = anchor
  if (window.location.hash !== `#${anchor}`) window.location.hash = anchor
}
function syncSelectedSection(): void {
  selectedId.value = parseApiDocAnchor(window.location.hash)
}
async function copyWithFeedback(text: string, successMessage: string): Promise<void> {
  if (await copyText(text)) void message.success(successMessage)
  else void message.error('复制失败，请手动复制')
}
async function copyApiBase(): Promise<void> {
  await copyWithFeedback(apiBase.value, '调用地址已复制')
}
async function copyCode(code?: string): Promise<void> {
  await copyWithFeedback(code ?? '', '示例已复制')
}
onMounted(() => window.addEventListener('hashchange', syncSelectedSection))
onBeforeUnmount(() => window.removeEventListener('hashchange', syncSelectedSection))
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
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 20px;
  margin: 0 var(--workspace-space-6) var(--workspace-space-6);
}
.api-docs-nav {
  overflow-y: auto;
  padding: 16px 0;
  border-right: 1px solid var(--gray-150);
}
.api-docs-nav__title {
  padding: 0 16px 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.api-docs-nav__item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 8px 16px;
  color: var(--color-text);
  text-decoration: none;
  font-size: 13px;
}
.api-docs-nav__item:hover,
.api-docs-nav__item.is-active {
  background: var(--main-50);
  color: var(--main-700);
}
.api-docs-content {
  min-width: 0;
  overflow: auto;
  padding: 16px 12px 48px;
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
  border-left: 2px solid var(--gray-150);
  background: transparent;
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
  background: var(--bg-page);
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
  .api-docs-nav__item {
    flex: 0 0 auto;
    padding: 7px 10px;
  }
  .api-docs-content {
    padding: 12px 0 36px;
  }
}
</style>
