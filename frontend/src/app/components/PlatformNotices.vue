<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { BellOutlined } from '@ant-design/icons-vue'
import { Alert as AAlert, Empty as AEmpty, Spin as ASpin } from 'ant-design-vue'
import RefreshButton from '../../shared/components/RefreshButton.vue'
import { errorMessage, isAborted, RequestScope } from '../../shared/http.ts'
import { waitForBackgroundObservation } from '../../shared/backgroundObservation.ts'
import { identity } from '../../modules/identity/public.ts'
import { listPublishedNotices } from '../../modules/admin/public.ts'
import type { PublishedNotice } from '../../modules/admin/public.ts'
import ResourceDrawer from '../../shared/components/ResourceDrawer.vue'

const open = ref(false)
const loading = ref(false)
const error = ref('')
const notices = ref<PublishedNotice[]>([])
const readVersions = ref<Record<string, number>>({})
const storageKey = computed(() =>
  identity.value ? 'zhiheng:notice-reads:' + encodeURIComponent(identity.value.id) : null
)
const unreadCount = computed(
  () => notices.value.filter((notice) => readVersions.value[notice.id] !== notice.version).length
)
const scope = new RequestScope()
const observationScope = new RequestScope()

function restoreReadVersions(): void {
  readVersions.value = {}
  if (!storageKey.value) return
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(storageKey.value) ?? '{}')
    if (stored && typeof stored === 'object' && !Array.isArray(stored))
      readVersions.value = Object.fromEntries(
        Object.entries(stored).filter(
          (entry): entry is [string, number] => Number.isInteger(entry[1]) && Number(entry[1]) > 0
        )
      )
  } catch {
    // Storage restrictions must not prevent reading announcements.
  }
}
function markRead(): void {
  readVersions.value = {
    ...readVersions.value,
    ...Object.fromEntries(notices.value.map((notice) => [notice.id, notice.version]))
  }
  if (!storageKey.value) return
  try {
    window.localStorage.setItem(storageKey.value, JSON.stringify(readVersions.value))
  } catch {
    // Keep the current session's read state when storage is unavailable.
  }
}
async function load(background = false): Promise<void> {
  if (!identity.value) return
  const ticket = scope.replace()
  loading.value = open.value && !background
  if (!background) error.value = ''
  try {
    const rows = await listPublishedNotices(ticket.signal)
    if (!ticket.isCurrent()) return
    notices.value = rows
    error.value = ''
    if (open.value) markRead()
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason) && !background) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
async function observe(): Promise<void> {
  const ticket = observationScope.replace()
  try {
    while (ticket.isCurrent()) {
      if (!document.hidden) await load(true)
      await waitForBackgroundObservation(ticket.signal, 10000)
    }
  } catch (reason) {
    if (!isAborted(reason)) throw reason
  }
}
function show(): void {
  open.value = true
  void load()
}
function close(): void {
  open.value = false
  error.value = ''
}
function storageChanged(event: StorageEvent): void {
  if (
    event.storageArea === window.localStorage &&
    (event.key === storageKey.value || event.key === null)
  )
    restoreReadVersions()
}
function focus(): void {
  if (!document.hidden) void load(true)
}
watch(
  () => identity.value?.id,
  (ownerId) => {
    observationScope.replace()
    scope.replace()
    open.value = false
    loading.value = false
    error.value = ''
    notices.value = []
    restoreReadVersions()
    if (ownerId) void observe()
  },
  { immediate: true, flush: 'sync' }
)
window.addEventListener('storage', storageChanged)
window.addEventListener('focus', focus)
onBeforeUnmount(() => {
  scope.dispose()
  observationScope.dispose()
  window.removeEventListener('storage', storageChanged)
  window.removeEventListener('focus', focus)
})
</script>
<template>
  <button
    class="platform-notices-button"
    :class="{ 'has-unread': unreadCount > 0 }"
    type="button"
    aria-label="通知"
    :title="unreadCount ? '通知（' + unreadCount + ' 条未读）' : '通知'"
    :aria-describedby="unreadCount ? 'platform-notices-unread' : undefined"
    @click="show"
  >
    <BellOutlined />
    <span v-if="unreadCount" class="platform-notices-dot" aria-hidden="true" />
    <span v-if="unreadCount" id="platform-notices-unread" class="sr-only" role="status">
      {{ unreadCount }} 条未读通知
    </span>
  </button>
  <ResourceDrawer title="通知公告" :open="open" :width="480" @close="close">
    <RefreshButton label="刷新公告" :loading="loading" @click="load()" />
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
  position: relative;
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
.platform-notices-button.has-unread {
  color: var(--main-color);
}
.platform-notices-dot {
  position: absolute;
  top: 3px;
  right: 3px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #f5222d;
  box-shadow: 0 0 0 2px var(--bg-page);
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
