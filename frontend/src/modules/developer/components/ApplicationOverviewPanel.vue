<!-- Adapted from the original platform UI, MIT licensed. -->
<template>
  <section class="overview-panel">
    <div class="overview-grid">
      <div class="overview-field">
        <span>应用名称</span><strong>{{ application.applicationName }}</strong>
      </div>
      <div class="overview-field">
        <span>应用编码</span><code>{{ application.applicationCode }}</code>
      </div>
      <div class="overview-field">
        <span>当前状态</span
        ><WorkspaceStatus
          :status="application.status"
          :label="applicationStatusLabel(application.status)"
        />
      </div>
      <div class="overview-field">
        <span>创建时间</span><strong>{{ formatTime(application.createdAt) }}</strong>
      </div>
      <div class="overview-field">
        <span>更新时间</span><strong>{{ formatTime(application.updatedAt) }}</strong>
      </div>
    </div>
    <div class="access-guide">
      <strong>调用入口</strong>
      <p>
        在模型目录配置费率并发布模型，为访问密钥选择授权模型后即可调用公开接口。停用应用后，已有密钥和调用记录会保留，但接口调用将被拒绝。
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import WorkspaceStatus from '../../../shared/components/WorkspaceStatus.vue'
import { applicationStatusLabel } from '../presentation.ts'
import type { Application } from '../api/index.ts'
defineProps<{ application: Application }>()
function formatTime(value: string): string {
  return value ? new Date(value).toLocaleString('zh-CN') : '暂无记录'
}
</script>

<style scoped lang="scss">
.overview-panel {
  display: grid;
  gap: 20px;
  padding: 20px;
}

.overview-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-top: 1px solid var(--gray-150);
  border-left: 1px solid var(--gray-150);
}

.overview-field {
  display: grid;
  min-height: 76px;
  align-content: center;
  gap: 6px;
  padding: 14px 16px;
  border-right: 1px solid var(--gray-150);
  border-bottom: 1px solid var(--gray-150);
  background: var(--bg-container);
}

.overview-field span,
.access-guide p {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.overview-field code {
  overflow: hidden;
  font-family: var(--workspace-font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.access-guide {
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
}

.access-guide p {
  margin: 6px 0 0;
  line-height: 1.7;
}
</style>
