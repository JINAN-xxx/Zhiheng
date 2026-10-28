<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { BellOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { Alert as AAlert, Button as AButton, Empty as AEmpty, Spin as ASpin } from 'ant-design-vue'
import { errorMessage, http, isAborted, RequestScope } from '../../shared/http.ts'
import { list, object, string } from '../../shared/validation.ts'
import ResourceDrawer from '../../shared/components/ResourceDrawer.vue'

interface PublishedNotice {
  id: string
  title: string
  content: string
}
function decodeNotices(value: unknown): PublishedNotice[] {
  return list((item) => {
    const notice = object(item)
    return { id: string(notice.id), title: string(notice.title), content: string(notice.content) }
  })(object(value).items)
}
const open = ref(false)
const loading = ref(false)
const error = ref('')
const notices = ref<PublishedNotice[]>([])
const scope = new RequestScope()
async function load(): Promise<void> {
  const ticket = scope.replace()
  loading.value = true
  error.value = ''
  notices.value = []
  try {
    const rows = await http.request('/admin/notices/published', {
      signal: ticket.signal,
      decode: decodeNotices
    })
    if (ticket.isCurrent()) notices.value = rows
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
function show(): void {
  open.value = true
  void load()
}
function close(): void {
  scope.replace()
  open.value = false
  loading.value = false
  notices.value = []
  error.value = ''
}
onBeforeUnmount(() => scope.dispose())
</script>
<template>
  <button
    class="platform-notices-button"
    type="button"
    aria-label="通知"
    title="通知"
    @click="show"
  >
    <BellOutlined />
  </button>
  <ResourceDrawer title="通知公告" :open="open" :width="480" @close="close">
    <a-button :loading="loading" aria-label="刷新公告" @click="load">
      <ReloadOutlined />刷新
    </a-button>
    <a-alert v-if="error" type="error" :message="error" show-icon class="notice-error" />
    <a-spin v-if="loading" class="notice-loading" />
    <a-empty v-else-if="!error && !notices.length" description="暂无公告" />
    <article v-for="notice in notices" :key="notice.id" class="platform-notice">
      <h2>{{ notice.title }}</h2>
      <p>{{ notice.content }}</p>
    </article>
  </ResourceDrawer>
</template>
<style scoped>
.platform-notices-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 18px;
  cursor: pointer;
}
.platform-notices-button:hover {
  background: var(--gray-50);
  color: var(--main-color);
}
.platform-notices-button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.notice-error {
  margin-top: 16px;
}
.notice-loading {
  display: block;
  margin: 40px auto;
}
.platform-notice {
  padding: 20px 0;
  border-bottom: 1px solid var(--gray-150);
}
.platform-notice h2 {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 500;
}
.platform-notice p {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
