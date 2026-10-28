<!-- Adapted from original FileStatistics, MIT licensed. -->
<template>
  <section class="file-statistics">
    <div class="file-statistics-header">
      <div>
        <h2 class="file-statistics-title">文件概览</h2>
        <div class="file-statistics-tip">统计数据随当前查询条件实时更新</div>
      </div>
    </div>
    <div class="file-statistics-content">
      <div class="file-stat-item stat-primary">
        <div class="file-stat-main">
          <a-statistic title="文件总数" :value="stats.totalCount" />
          <div class="file-stat-icon"><FileOutlined /></div>
        </div>
        <div class="file-stat-extra">
          <span>正常 {{ stats.activeCount }}</span>
          <span>回收站 {{ stats.deletedCount }}</span>
        </div>
      </div>

      <div class="file-stat-item stat-purple">
        <div class="file-stat-main">
          <a-statistic title="占用空间" :value="formatFileSize(stats.totalSize)" />
          <div class="file-stat-icon"><BarChartOutlined /></div>
        </div>
        <div class="file-stat-extra">
          <span>全部文件累计占用</span>
        </div>
      </div>

      <div class="file-stat-item stat-green">
        <div class="file-stat-main">
          <a-statistic title="公开文件空间" :value="formatFileSize(stats.publicSize)" />
          <div class="file-stat-icon"><FolderOpenOutlined /></div>
        </div>
        <div class="file-stat-extra">
          <span>占总空间 {{ formatPercentage(stats.publicSize) }}</span>
        </div>
      </div>

      <div class="file-stat-item stat-orange">
        <div class="file-stat-main">
          <a-statistic title="受保护文件空间" :value="formatFileSize(stats.privateSize)" />
          <div class="file-stat-icon"><LockOutlined /></div>
        </div>
        <div class="file-stat-extra">
          <span>占总空间 {{ formatPercentage(stats.privateSize) }}</span>
        </div>
      </div>

      <div class="file-stat-item stat-red">
        <div class="file-stat-main">
          <a-statistic title="已过期文件" :value="stats.expiredCount" />
          <div class="file-stat-icon"><ClockCircleOutlined /></div>
        </div>
        <div class="file-stat-extra">
          <span>7天内到期 {{ stats.retentionExpiringCount }}</span>
        </div>
      </div>

      <div class="file-stat-item stat-cyan">
        <div class="file-stat-main">
          <a-statistic title="即将过期授权" :value="stats.aclExpiringCount" />
          <div class="file-stat-icon"><KeyOutlined /></div>
        </div>
        <div class="file-stat-extra">
          <span>7天内失效的 ACL 项</span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { Statistic as AStatistic } from 'ant-design-vue'
import {
  BarChartOutlined,
  ClockCircleOutlined,
  FileOutlined,
  FolderOpenOutlined,
  KeyOutlined,
  LockOutlined
} from '@ant-design/icons-vue'
import { formatFileSize } from '../presentation.ts'
import type { FileStats } from '../api/index.ts'
const props = defineProps<{ stats: FileStats }>()
function formatPercentage(size: number): string {
  if (!props.stats.totalSize) return '0%'
  const percentage = (size / props.stats.totalSize) * 100
  return `${percentage >= 10 ? percentage.toFixed(0) : percentage.toFixed(1)}%`
}
</script>

<style scoped>
.file-statistics {
  margin-bottom: 28px;
}

.file-statistics-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.file-statistics-title {
  margin: 0;
  color: var(--workspace-text);
  font-size: 16px;
  font-weight: 500;
  line-height: 22px;
}

.file-statistics-tip {
  margin-top: 2px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
  line-height: 18px;
}

.file-statistics-content {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 20px;
}

.file-stat-item {
  --stat-color: var(--workspace-main);
  position: relative;
  min-width: 0;
  min-height: 108px;
  padding: 12px 0;
}

.file-stat-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.file-stat-title {
  color: var(--workspace-text-secondary);
  font-size: 13px;
  line-height: 20px;
}
.file-stat-item :deep(.ant-statistic-title) {
  color: var(--workspace-text-secondary);
  font-size: 13px;
}
.file-stat-item :deep(.ant-statistic-content) {
  color: var(--workspace-text);
  font-size: 23px;
  font-weight: 500;
}

.file-stat-value {
  margin-top: 5px;
  color: var(--workspace-text);
  font-size: 23px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  line-height: 32px;
  flex-wrap: wrap;
}

.file-stat-icon {
  display: flex;
  flex: 0 0 30px;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  color: var(--stat-color);
  font-size: 16px;
}

.file-stat-extra {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 12px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
  line-height: 18px;
}

.stat-purple {
  --stat-color: #7c5ce7;
}

.stat-green {
  --stat-color: #20a162;
}

.stat-orange {
  --stat-color: #e58a17;
}

.stat-red {
  --stat-color: #e45656;
}

.stat-cyan {
  --stat-color: #168d9c;
}

@media (max-width: 1280px) {
  .file-statistics-content {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .file-statistics-content {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .file-statistics-content {
    gap: 12px 20px;
  }
}
</style>
