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
  gap: 24px;
  padding: 24px;
}

.overview-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px 28px;
}

.overview-field {
  display: grid;
  min-height: 44px;
  align-content: center;
  gap: 6px;
  min-width: 0;
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
  border-top: 1px solid var(--gray-150);
  background: var(--gray-25);
  border-radius: 5px;
}

.access-guide p {
  margin: 6px 0 0;
  line-height: 1.7;
}
.overview-field strong {
  font-size: 14px;
  font-weight: 400;
  overflow-wrap: anywhere;
}
.access-guide > strong {
  font-size: 14px;
  font-weight: 500;
}
@media (max-width: 640px) {
  .overview-panel {
    padding: 18px;
  }
  .overview-grid {
    grid-template-columns: 1fr;
    gap: 20px;
  }
}
</style>
