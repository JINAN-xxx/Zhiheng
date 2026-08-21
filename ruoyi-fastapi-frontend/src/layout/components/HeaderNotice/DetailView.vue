<template>
  <a-drawer
    v-model:open="visible"
    class="notice-detail-drawer"
    title="公告详情"
    :width="drawerWidth"
    @close="handleClose"
  >
    <a-spin :spinning="loading">
      <a-empty v-if="!loading && !detail" description="暂无数据" />
      <article v-else-if="detail" class="notice-page">
        <a-tag :color="noticeTag.color">
          <component :is="noticeTag.icon" />
          {{ noticeTag.label }}
        </a-tag>

        <h1>{{ detail.noticeTitle }}</h1>

        <div class="notice-meta">
          <span><UserOutlined />{{ detail.createBy || '—' }}</span>
          <span><ClockCircleOutlined />{{ detail.createTime || '—' }}</span>
          <a-badge :status="isStatusNormal ? 'success' : 'error'" :text="isStatusNormal ? '正常' : '已关闭'" />
        </div>

        <div v-if="hasContent" class="notice-content" v-html="safeNoticeContent" />
        <a-empty v-else description="暂无内容" />
      </article>
    </a-spin>
  </a-drawer>
</template>

<script setup>
import {
  BellOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  NotificationOutlined,
  UserOutlined,
} from '@ant-design/icons-vue'
import { useWindowSize } from '@vueuse/core'
import DOMPurify from 'dompurify'

import { getNotice } from '@/api/system/notice'

const visible = ref(false)
const loading = ref(false)
const detail = ref(null)
const { width } = useWindowSize()
const drawerWidth = computed(() => (width.value < 760 ? '100%' : Math.min(width.value * 0.5, 720)))

const isStatusNormal = computed(() => detail.value?.status === '0' || detail.value?.status === 0)
const hasContent = computed(() => String(detail.value?.noticeContent ?? '').trim() !== '')
const safeNoticeContent = computed(() => DOMPurify.sanitize(String(detail.value?.noticeContent ?? ''), {
  USE_PROFILES: { html: true },
}))
const noticeTag = computed(() => {
  if (detail.value?.noticeType === '1') return { color: 'orange', icon: BellOutlined, label: '通知' }
  if (detail.value?.noticeType === '2') return { color: 'green', icon: NotificationOutlined, label: '公告' }
  return { color: 'blue', icon: FileTextOutlined, label: '消息' }
})

async function open(payload) {
  const id = typeof payload === 'object' && payload !== null ? payload.noticeId : payload
  const preset = typeof payload === 'object' && payload?.noticeContent != null ? payload : null
  visible.value = true
  if (preset) {
    detail.value = preset
    return
  }
  if (id == null || id === '') {
    detail.value = null
    return
  }

  loading.value = true
  detail.value = null
  try {
    const response = await getNotice(id)
    detail.value = response.data
  } catch {
    detail.value = null
  } finally {
    loading.value = false
  }
}

function handleClose() {
  detail.value = null
  loading.value = false
}

defineExpose({ open })
</script>

<style scoped>
.notice-page {
  max-width: 680px;
  margin: 0 auto;
}

.notice-page h1 {
  margin: 18px 0 14px;
  color: var(--workspace-text);
  font-size: 24px;
  line-height: 1.45;
}

.notice-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  padding: 12px 0 20px;
  border-bottom: 1px solid var(--workspace-border);
  color: var(--workspace-text-secondary);
  font-size: 12px;
}

.notice-meta > span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.notice-content {
  min-height: 120px;
  margin-top: 24px;
  padding: 20px;
  border: 1px solid var(--workspace-border);
  border-radius: var(--workspace-radius-md);
  background: var(--workspace-surface-muted);
  color: var(--workspace-text);
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}

.notice-content :deep(img),
.notice-content :deep(video),
.notice-content :deep(table) {
  max-width: 100%;
}

.notice-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
}

.notice-content :deep(th),
.notice-content :deep(td) {
  padding: 8px;
  border: 1px solid var(--workspace-border);
}

.notice-content :deep(a) {
  color: var(--workspace-main-deep);
  text-decoration: underline;
}
</style>
