<template>
  <div class="header-notice">
    <a-popover
      v-model:open="noticeVisible"
      placement="bottomRight"
      trigger="click"
      :overlay-inner-style="{ width: '340px', padding: 0 }"
    >
      <template #content>
        <section class="notice-popover" aria-label="通知公告">
          <header class="notice-header">
            <strong>通知公告</strong>
            <a-button :disabled="!noticeList.length" size="small" type="link" @click="markAllRead">
              全部已读
            </a-button>
          </header>

          <div v-if="noticeError && noticeList.length" class="notice-stale" role="status">
            刷新失败，列表可能已过期
          </div>
          <a-alert v-else-if="noticeError" :message="noticeError" show-icon type="error" />

          <a-spin :spinning="noticeLoading">
            <a-list v-if="noticeList.length" :data-source="noticeList" size="small">
              <template #renderItem="{ item }">
                <a-list-item
                  class="notice-item"
                  :class="{ 'is-read': item.isRead }"
                  role="button"
                  tabindex="0"
                  @click="previewNotice(item)"
                  @keydown.enter="previewNotice(item)"
                >
                  <a-tag :color="item.noticeType === '1' ? 'orange' : 'green'">
                    {{ item.noticeType === '1' ? '通知' : '公告' }}
                  </a-tag>
                  <span class="notice-item__title">{{ item.noticeTitle }}</span>
                  <time>{{ item.createTime }}</time>
                </a-list-item>
              </template>
            </a-list>
            <a-empty v-else-if="!noticeLoading && !noticeError" description="暂无公告" />
          </a-spin>
        </section>
      </template>

      <a-badge :count="unreadCount" :overflow-count="99">
        <a-button aria-label="通知公告" class="notice-trigger" type="text">
          <template #icon><BellOutlined /></template>
        </a-button>
      </a-badge>
    </a-popover>

    <NoticeDetailView ref="noticeViewRef" />
  </div>
</template>

<script setup>
import { BellOutlined } from '@ant-design/icons-vue'

import { listNoticeTop, markNoticeRead, markNoticeReadAll } from '@/api/system/notice'
import NoticeDetailView from './DetailView.vue'

const noticeList = ref([])
const unreadCount = ref(0)
const noticeLoading = ref(false)
const noticeError = ref('')
const noticeVisible = ref(false)
const noticeViewRef = ref()

async function loadNoticeTop() {
  noticeLoading.value = true
  noticeError.value = ''
  try {
    const response = await listNoticeTop()
    noticeList.value = response.data || []
    unreadCount.value = response.unreadCount ?? noticeList.value.filter((notice) => !notice.isRead).length
  } catch (error) {
    noticeError.value = error?.message ?? '通知公告加载失败'
  } finally {
    noticeLoading.value = false
  }
}

function previewNotice(item) {
  if (!item.isRead) {
    markNoticeRead(item.noticeId).catch(() => {})
    const index = noticeList.value.indexOf(item)
    if (index !== -1) noticeList.value[index] = { ...item, isRead: true }
    unreadCount.value = Math.max(0, unreadCount.value - 1)
  }
  noticeVisible.value = false
  noticeViewRef.value?.open(item.noticeId)
}

function markAllRead() {
  const ids = noticeList.value.map((notice) => notice.noticeId).join(',')
  if (!ids) return
  markNoticeReadAll(ids).catch(() => {})
  noticeList.value = noticeList.value.map((notice) => ({ ...notice, isRead: true }))
  unreadCount.value = 0
}

onMounted(loadNoticeTop)
</script>

<style scoped>
.header-notice {
  display: flex;
  align-items: center;
}

.notice-trigger {
  width: 32px;
  height: 32px;
}

.notice-popover {
  overflow: hidden;
  border-radius: var(--workspace-radius-md);
}

.notice-header {
  display: flex;
  min-height: 44px;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid var(--workspace-border);
}

.notice-stale {
  margin: 8px 12px 0;
  padding: 8px;
  border-radius: var(--workspace-radius-sm);
  background: var(--workspace-warning-soft);
  color: var(--workspace-warning);
  font-size: 12px;
}

.notice-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  cursor: pointer;
}

.notice-item:hover {
  background: var(--workspace-surface-muted);
}

.notice-item.is-read {
  opacity: 0.55;
}

.notice-item__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.notice-item time {
  color: var(--workspace-text-muted);
  font-size: 11px;
}

.notice-popover :deep(.ant-empty) {
  margin: 24px 0;
}
</style>
